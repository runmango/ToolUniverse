import asyncio
import copy
import csv
import io
import json
from pathlib import Path
import platform
import random
import threading
import time
import uuid

from ..medical_core.db import encoded, now, review, revision
from .metrics import aggregate, evaluate_assessment, evaluate_entities, evaluate_query, gold_labels, human_verified
from .retrieval import Retriever, Unavailable, environment, public_corpus
from .spec import text
from .store import digest


def code_hash():
    root=Path(__file__).parents[1]
    files={str(p.relative_to(root)):p.read_text(encoding='utf-8') for folder in ('benchmark','medical_core','knowledge_graph','evidence_chain','evidence_intelligence') for p in sorted((root/folder).glob('*.py'))}
    shared=root.parents[1]/'src/tooluniverse/database_setup/embedder.py'
    files['shared_embedder']=shared.read_text(encoding='utf-8')
    return digest(files)


class ExperimentRunner:
    def __init__(self, store, research=None):
        self.research = research
        self.store = store
        self.tasks = set()
        self.stop = threading.Event()
        self.cache = store.db_path.parent / 'benchmark-cache'

    async def close(self):
        self.stop.set()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def start(self, version_id, model_ids, *, top_k=20, threshold=2, rerun=None):
        if self.tasks: raise ValueError('已有 Benchmark 实验正在运行')
        if type(top_k) is not int or not 20 <= top_k <= 200: raise ValueError('top_k 必须为20–200，以覆盖 K=5/10/20')
        if threshold not in (1,2,3): raise ValueError('相关性阈值只能是1、2或3')
        snapshot = self.store.snapshot(version_id)
        payload = snapshot['payload']
        if not payload['documents'] or not payload['queries']: raise ValueError('需要至少一篇文献及一个 Query')
        ids = {d['document_id'] for d in payload['documents']}
        for query in payload['queries']:
            missing = set(gold_labels(query)) - ids
            if missing: raise ValueError(f"{query['query_id']} 的 Gold 文献不在固定语料中：{', '.join(sorted(missing))}")
        if rerun:
            models = [rerun['model_snapshot']]
        else:
            if not 1 <= len(set(model_ids)) <= 8: raise ValueError('每组选择1–8个模型配置')
            catalog = {m['id']:m for m in self.store.models()}
            if any(m not in catalog for m in model_ids): raise KeyError('模型配置不存在')
            models = [catalog[key] for key in dict.fromkeys(model_ids)]
        graph_ids={m['config'].get('release_id') for m in models if m['method']=='graph'}
        if graph_ids and (None in graph_ids or len(graph_ids)>1 or self.research is None): raise ValueError('图谱检索需要同一个固定图谱版本')
        graph_release=self.research.release(next(iter(graph_ids))) if graph_ids else None
        group, experiments = uuid.uuid4().hex, []
        settings = {'top_k':top_k, 'ks':[5,10,20], 'relevance_threshold':threshold, 'seed':42, 'query_field':'query',
                    'document_fields':['title','text'], 'gold_access':'evaluation_only', 'engine_hash':code_hash(), 'graph_release_id':graph_release['id'] if graph_release else None, 'graph_release_hash':graph_release['content_hash'] if graph_release else None}
        with self.store.connect() as db:
            for model in models:
                identity=uuid.uuid4().hex
                db.execute('INSERT INTO benchmark_experiments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (identity,group,version_id,encoded(model),encoded(settings),'queued',now(),None,None,None,None,rerun['id'] if rerun else None))
                experiments.append(identity)
        task=asyncio.create_task(asyncio.to_thread(self._work,experiments,snapshot,models,settings,rerun))
        self.tasks.add(task); task.add_done_callback(self.tasks.discard)
        return {'group_id':group,'experiment_ids':experiments}

    def _work(self, ids, snapshot, models, settings, rerun):
        payload=snapshot['payload']
        corpus=public_corpus(payload['documents'])
        documents={d['document_id']:d for d in payload['documents']}
        for identity,model in zip(ids,models):
            retriever=None; records=[]; start=time.perf_counter()
            runtime={'python':platform.python_version(),'dependencies':environment(),'corpus_hash':snapshot['corpus_hash'],
                     'query_count':len(payload['queries']),'document_count':len(corpus),'engine_hash':code_hash()}
            try:
                if self.stop.is_set(): raise InterruptedError('任务被停止')
                with self.store.connect() as db: db.execute("UPDATE benchmark_experiments SET status='running' WHERE id=?",(identity,))
                if model['method']=='graph':
                    from .graph_retrieval import GraphRetriever
                    retriever=GraphRetriever(self.research.release(model['config']['release_id']),corpus,model['config'])
                else: retriever=Retriever(model['method'],model['config'],corpus,self.cache)
                for query in payload['queries']:
                    if self.stop.is_set(): raise InterruptedError('任务被停止')
                    replay=None
                    if rerun and model['method']=='llm_assisted':
                        old=next((r for r in rerun['results'] if r['query_id']==query['query_id']),None)
                        replay=old['trace'].get('expansion') if old else None
                    ranking,trace=retriever.rank(query['query'],settings['top_k'],replay_expansion=replay)
                    if len({r['document_id'] for r in ranking}) != len(ranking) or any(r['document_id'] not in documents for r in ranking):
                        raise ValueError('检索器返回重复或固定语料范围外的文献')
                    result=evaluate_query(query,ranking,threshold=settings['relevance_threshold'])
                    result['medical_retrieval']=evaluate_entities(query,[documents[r['document_id']] for r in ranking])
                    trace['query_hash']=digest(query['query'])
                    with self.store.connect() as db:
                        db.execute('INSERT INTO benchmark_results VALUES (?,?,?,?,?)',(identity,query['query_id'],encoded(ranking),encoded(result),encoded(trace)))
                    records.append(result)
                metrics=aggregate(records,settings['top_k'])
                status='completed' if metrics['scored_query_count'] else 'completed_unscored'
                runtime.update(retriever.runtime)
                runtime['elapsed_seconds']=round(time.perf_counter()-start,3)
                with self.store.connect() as db:
                    db.execute('UPDATE benchmark_experiments SET status=?,finished_at=?,metrics=?,runtime=? WHERE id=?',
                               (status,now(),encoded(metrics),encoded(runtime),identity))
            except Exception as exc:
                status='not_run' if isinstance(exc,Unavailable) else 'interrupted' if isinstance(exc,InterruptedError) else 'failed'
                # Never leak credentials or response bodies to experiment reports.
                message=str(exc) if isinstance(exc,(Unavailable,ValueError,InterruptedError)) else type(exc).__name__+'：模型或服务执行失败，请核对配置及本机日志。'
                with self.store.connect() as db:
                    db.execute('UPDATE benchmark_experiments SET status=?,finished_at=?,runtime=?,error=? WHERE id=?',(status,now(),encoded(runtime),message[:1500],identity))
            finally:
                if retriever: retriever.close()

    def replay(self, identity):
        experiment=self.store.experiment(identity)
        if experiment['status'] not in {'completed','completed_unscored'}: raise ValueError('只能重算已完成实验')
        snapshot=self.store.snapshot(experiment['dataset_version_id'])
        queries={q['query_id']:q for q in snapshot['payload']['queries']}
        results=[evaluate_query(queries[r['query_id']],r['ranking'],threshold=experiment['config']['relevance_threshold']) for r in experiment['results']]
        metrics=aggregate(results,experiment['config']['top_k'])
        return {'experiment_id':identity,'replayed_metrics':metrics,'matches_saved':metrics==experiment['metrics'],
                'mode':'saved_rankings_and_frozen_labels','engine_matches':code_hash()==experiment['config']['engine_hash']}


def mine_negatives(store, version_id, query_id, method, count=10, seed=42, experiment_id=None):
    if method not in {'random','hard','in_batch'}: raise ValueError('负样本方法无效')
    if not 1 <= count <= 100: raise ValueError('候选数量必须为1–100')
    snapshot=store.snapshot(version_id); payload=snapshot['payload']
    query=next((q for q in payload['queries'] if q['query_id']==query_id),None)
    if not query: raise KeyError('Query 不存在')
    labels=gold_labels(query)
    # Even weakly relevant labels are excluded. Unknown documents remain candidates.
    exclude={d for d,v in labels.items() if v>0}
    documents={d['document_id']:d for d in payload['documents']}
    score_map={}
    if method=='random':
        pool=sorted(set(documents)-exclude); random.Random(seed).shuffle(pool)
    elif method=='in_batch':
        pool=sorted({doc for q in payload['queries'] if q['query_id']!=query_id for doc,grade in gold_labels(q).items() if grade>=2} & set(documents)-exclude)
        random.Random(seed).shuffle(pool)
    else:
        if not experiment_id: raise ValueError('Hard Negative 需要选择真实完成的检索实验')
        experiment=store.experiment(experiment_id)
        if experiment['status'] not in {'completed','completed_unscored'} or experiment['corpus_hash']!=snapshot['corpus_hash']:
            raise ValueError('Hard Negative 实验必须完成且候选语料完全一致')
        if experiment['model_snapshot']['method'] not in {'embedding','embedding_reranker','hybrid'}:
            raise ValueError('语义困难负样本需来自 Embedding、Reranker 或 Hybrid 实验')
        old=store.snapshot(experiment['dataset_version_id'])
        if not any(q['query_id']==query_id and q['query']==query['query'] for q in old['payload']['queries']): raise ValueError('实验问题与当前问题不一致')
        row=next((r for r in experiment['results'] if r['query_id']==query_id),None)
        if not row: raise ValueError('实验没有该 Query 的结果')
        pool=[r['document_id'] for r in row['ranking'] if r['document_id'] not in exclude]
        score_map={r['document_id']:r['score'] for r in row['ranking']}
    with store.connect() as db:
        for doc in pool[:count]:
            reviewed=labels.get(doc)==0
            review={'seed':seed,'source_experiment_id':experiment_id,'reason':'已有人工 verified relevance=0' if reviewed else '尚未判断不相关，必须人工审核'}
            db.execute('INSERT OR IGNORE INTO benchmark_negatives VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex,version_id,query_id,doc,method,'unclassified',score_map.get(doc),'accepted' if reviewed else 'candidate',encoded(review),now()))
    return negatives(store,version_id)


def negatives(store,version_id):
    with store.connect() as db:
        return [{**dict(r),'review':json.loads(r['review'])} for r in db.execute('SELECT * FROM benchmark_negatives WHERE dataset_version_id=? ORDER BY created_at DESC',(version_id,))]


def review_negative(store, identity, actor, accepted, category, note):
    actor=text(actor,'审核人',120)
    allowed={'unclassified','same_disease_different_subtype','same_drug_different_indication','different_trial',
             'different_population','similar_term_different_meaning'}
    if category not in allowed: raise ValueError('困难负样本分类无效')
    if type(accepted) is not bool: raise ValueError('需要明确接受或拒绝')
    with store.connect() as db:
        row=db.execute('SELECT * FROM benchmark_negatives WHERE id=?',(identity,)).fetchone()
        if not row: raise KeyError('负样本候选不存在')
        if row['status']!='candidate': raise ValueError('该候选已审核')
    snapshot=store.snapshot(row['dataset_version_id'])
    original=next(q for q in snapshot['payload']['queries'] if q['query_id']==row['query_id'])
    original_doc=next(d for d in snapshot['payload']['documents'] if d['document_id']==row['document_id'])
    def change(payload):
        query=next((q for q in payload['queries'] if q['query_id']==row['query_id']),None)
        if not query or query['query']!=original['query']: raise ValueError('Query 已修改，请重新挖掘')
        current_doc=next((d for d in payload['documents'] if d['document_id']==row['document_id']),None)
        if not current_doc: raise ValueError('文献已不在当前语料中')
        if (current_doc['title'],current_doc['text'])!=(original_doc['title'],original_doc['text']): raise ValueError('文献内容已修改，请重新挖掘并审核')
        if gold_labels(query).get(row['document_id'],0)>0: raise ValueError('该文献已标为相关，不能自动改成负样本')
        if accepted:
            query['relevant_documents']=[x for x in query['relevant_documents'] if x['document_id']!=row['document_id']]+[
                {'document_id':row['document_id'],'relevance':0,'annotation_source':'human','verification_status':'verified','annotator':actor,'note':note}]
    dataset=store.mutate(snapshot['dataset_id'],change,'人工审核负样本',actor,row['query_id'])
    with store.connect() as db:
        db.execute('UPDATE benchmark_negatives SET status=?,category=?,review=? WHERE id=?',
                   ('accepted' if accepted else 'rejected',category,encoded({'actor':actor,'note':note,'reviewed_at':now(),'result_version_id':dataset['snapshot']['id']}),identity))
    return dataset


def save_assessment(store, experiment_id, query_id, payload, actor):
    actor=text(actor,'审核人',120)
    experiment=store.experiment(experiment_id)
    if experiment['status'] not in {'completed','completed_unscored'}: raise ValueError('请先完成检索实验')
    row=next((r for r in experiment['results'] if r['query_id']==query_id),None)
    if not row: raise KeyError('Query 不在实验中')
    snapshot=store.snapshot(experiment['dataset_version_id'])
    docs={d['document_id']:d for d in snapshot['payload']['documents']}
    returned={r['document_id'] for r in row['ranking']}
    data=copy.deepcopy(payload)
    json.dumps(data,allow_nan=False)
    for key in ('trial_matches','citations','claims'):
        if not isinstance(data.get(key,[]),list) or len(data.get(key,[]))>200: raise ValueError('医学标注数组最多200项')
        if any(not isinstance(item,dict) for item in data.get(key,[])): raise ValueError('每条医学标注必须为 JSON 对象')
    for item in data.get('trial_matches',[]):
        if item.get('document_id') not in returned: raise ValueError('试验关联标注的文献必须来自当前检索结果')
        import re
        if not re.fullmatch(r'NCT\d{8}',item.get('nct_id','')): raise ValueError('试验关联需要真实格式的 NCT ID')
        if item.get('correct') is not None and type(item['correct']) is not bool: raise ValueError('correct 必须是布尔值或 null')
        if item.get('correct') is not None and not item.get('verification_reference'): raise ValueError('试验关联审核需要记录证据来源')
    trial_keys=[(x['document_id'],x['nct_id']) for x in data.get('trial_matches',[])]
    if len(trial_keys)!=len(set(trial_keys)): raise ValueError('试验关联标注不能重复')
    citations=data.get('citations',[])
    citation_ids=[text(c.get('citation_id'),'citation_id',100) for c in citations]
    if len(set(citation_ids))!=len(citation_ids): raise ValueError('citation_id 不能重复')
    for item in citations:
        if item.get('exists') is not None and type(item['exists']) is not bool: raise ValueError('exists 必须是布尔值或 null')
        if item.get('exists') is not None and not item.get('verification_reference'): raise ValueError('引用真实性审核需要验证来源或失败依据')
    for claim in data.get('claims',[]):
        text(claim.get('claim_id'),'claim_id',100); text(claim.get('claim_text'),'claim_text',10000)
        if not isinstance(claim.get('citation_ids',[]),list) or any(not isinstance(value,str) for value in claim.get('citation_ids',[])):
            raise ValueError('citation_ids 必须为字符串数组')
        if not set(claim.get('citation_ids',[])) <= set(citation_ids): raise ValueError('结论引用了不存在的 citation_id')
        if claim.get('supported') is not None and type(claim['supported']) is not bool: raise ValueError('supported 必须是布尔值或 null')
        if claim.get('supported') is True:
            source=claim.get('evidence_document_id'); quote=claim.get('evidence_quote','')
            text(quote,'evidence_quote',100000)
            if source not in returned or not quote.strip() or quote not in docs[source]['title']+'\n'+docs[source]['text']:
                raise ValueError('支持性标注需要来自检索文献中的原文证据片段')
            cited={c.get('document_id') for c in citations if c['citation_id'] in claim.get('citation_ids',[]) and c.get('exists') is True}
            if source not in cited: raise ValueError('支持证据必须对应已核验真实的结论引用')
    claim_ids=[x['claim_id'] for x in data.get('claims',[])]
    if len(claim_ids)!=len(set(claim_ids)): raise ValueError('claim_id 不能重复')
    data.update(annotation_source='human',verification_status='verified',annotator=actor)
    metrics=evaluate_assessment(data); identity=uuid.uuid4().hex
    with store.connect() as db:
        db.execute('INSERT INTO benchmark_assessments VALUES (?,?,?,?,?,?,?)',(identity,experiment_id,query_id,encoded(data),encoded(metrics),actor,now()))
        version=revision(db,'benchmark_assessment',identity,data)
        review(db,'benchmark_assessment',identity,version,actor,'recorded','独立医学评价',{'experiment_id':experiment_id,'query_id':query_id})
    return {'id':identity,'metrics':metrics,'payload':data}


def report(store, identity, format):
    experiment=store.experiment(identity); snapshot=store.snapshot(experiment['dataset_version_id'])
    record={'report_schema':'medical-benchmark-v1','experiment':experiment,'dataset_snapshot':snapshot,
            'note':'未审核的标签不参与 Gold 评价；医学生成指标单独列示。MRR/MAP 在保存的 top_k 范围计算。'}
    if format=='json': return encoded(record)
    if format=='csv':
        output=io.StringIO(newline=''); writer=csv.writer(output)
        writer.writerow(['experiment_id','dataset_version','model_name','model_version','query_id','K','Precision','Recall','MRR','MAP','NDCG','HitRate','judged_fraction','evaluation_status','result_document_ids'])
        for row in experiment['results']:
            for k,values in row['metrics']['at_k'].items():
                cells=[identity,experiment['dataset_version'],experiment['model_snapshot']['name'],experiment['model_snapshot']['version'],row['query_id'],k,
                       values['Precision'],values['Recall'],row['metrics']['MRR'],row['metrics']['MAP'],values['NDCG'],values['HitRate'],values['judged_fraction'],row['metrics']['evaluation_status'],encoded([x['document_id'] for x in row['ranking']])]
                writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in cells])
        return '\ufeff'+output.getvalue()
    if format!='markdown': raise ValueError('报告格式必须为 json、csv 或 markdown')
    model=experiment['model_snapshot']; metrics=experiment.get('metrics') or {}
    score=lambda v:'待标注 / 未运行' if v is None else f'{v:.4f}'
    safe=lambda value:str(value).replace('|','\\|').replace('\n',' ')
    lines=['# 医学检索实验报告','',f"实验：{identity}",f"数据集版本：{experiment['dataset_version']} / {snapshot['content_hash']}",
           f"语料哈希：{snapshot['corpus_hash']}",f"模型：{safe(model['name'])} / {safe(model['version'])}",f"方法：{model['method']}；状态：{experiment['status']}",
           f"时间：{experiment['created_at']}；Top K：{experiment['config']['top_k']}",'',
           f"已评分 Query：{metrics.get('scored_query_count',0)}；MRR：{score(metrics.get('MRR'))}；MAP：{score(metrics.get('MAP'))}",
           '', '| K | Precision | Recall | NDCG | Hit Rate | Judged coverage |','| --- | --- | --- | --- | --- | --- |']
    for k,v in metrics.get('at_k',{}).items(): lines.append('| '+' | '.join([k,*[score(v[m]) for m in ('Precision','Recall','NDCG','HitRate','judged_fraction')]])+' |')
    lines.extend(['','## 评价口径','',record['note'],'未标注文献按零收益计算，并报告标注覆盖率；无相关文献的已审核问题计零分，未标注问题不计入宏平均。',
                  '', '## 可复现配置','', '```json', json.dumps({'model':model,'experiment':experiment['config'],'runtime':experiment['runtime']},ensure_ascii=False,indent=2),'```'])
    for row in experiment['results']:
        query=next(q for q in snapshot['payload']['queries'] if q['query_id']==row['query_id'])
        lines.extend(['', '## '+safe(row['query_id']),'',safe(query['query']), '', '```json',json.dumps({'metrics':row['metrics'],'ranking':row['ranking'],'gold':query['relevant_documents'],'trace':row['trace']},ensure_ascii=False,indent=2),'```'])
    lines.extend(['','## 医学关联和生成评价','', '```json',json.dumps(experiment['assessments'],ensure_ascii=False,indent=2),'```'])
    if experiment['error']: lines.extend(['','执行信息：'+safe(experiment['error'])])
    return '\n'.join(lines)
