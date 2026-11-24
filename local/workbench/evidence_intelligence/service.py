import asyncio
import json
import re
from collections import Counter
from contextlib import closing
from pathlib import Path
import uuid
import sqlite3
from . import pico, extraction, quality
from .models import VERSION
from .grounding import validate_extraction, locate
from .comparison import compare
from .llm import SemanticExtractor
from ..medical_core.db import encoded,now,revision,review,digest
from ..medical_core.service import eligible_edges,match_entities
from ..medical_core.model import mentioned_domains
from ..benchmark.retrieval import BM25


class IntelligenceService:
    def __init__(self,research,importer,benchmark,bridge):
        self.research,self.importer,self.benchmark,self.bridge=research,importer,benchmark,bridge
        self.tasks={}
        with research.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='ei_analyses'").fetchone():
                folder=research.db_path.parent/'backups';folder.mkdir(parents=True,exist_ok=True)
                backup=folder/('evidence-intelligence-'+now().replace(':','-')+'.sqlite')
                with closing(sqlite3.connect(backup)) as destination: db.backup(destination)
                db.execute('INSERT OR IGNORE INTO research_migrations VALUES (?,?,?)',('evidence-intelligence-v1',now(),encoded({'backup':str(backup),'change':'additive ei_* tables; no existing rows replaced'})))
                db.commit()
            db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            db.execute("UPDATE ei_analyses SET status='interrupted',finished_at=? WHERE status IN ('queued','running')",(now(),))

    def configure(self,body):
        cfg=dict(body)
        from ..benchmark.experiments import code_hash
        cfg.setdefault('engine_hash',code_hash())
        if cfg['release_id'] and cfg['live_search']: raise ValueError('固定历史版本与实时检索不能同时启用')
        if cfg['mode']=='account' and not cfg['model'].strip(): raise ValueError('请选择账号模型')
        if cfg['mode']=='api':
            profile=next((m for m in self.benchmark.models() if m['id']==cfg['model_profile_id'] and m['method']=='llm_assisted'),None)
            if not profile or not profile['config'].get('enabled'): raise ValueError('请选择已启用的 LLM API 配置')
            cfg['llm']=profile['config']['llm'];cfg['model_profile_snapshot']=profile
        return cfg

    async def parse_pico(self,body):
        cfg=self.configure(body);question=cfg['question'];warning=None
        proposed=pico.rules(question);generation={'origin':'rule','rule_version':VERSION,'model_name':'not_applicable','model_version':'not_applicable','verification_status':'unverified'}
        if cfg['mode']!='rules':
            proposed,generation=await SemanticExtractor(self.bridge,cfg).generate('pico',{'question':question})
        result=pico.validate(question,proposed)
        return {'question':question,'pico':result,'generation':generation,'search_query':cfg['search_query'] or pico.search_terms(question,result)}

    def start(self,body):
        if self.tasks: raise ValueError('已有证据分析正在运行，请等待或取消')
        cfg=self.configure(body);identity=uuid.uuid4().hex
        with self.research.connect() as db:
            db.execute('INSERT INTO ei_analyses VALUES (?,?,?,?,?,?,NULL)',(identity,cfg['question'],'queued',encoded(cfg),encoded({'phase':'queued','errors':[]}),now()))
        task=asyncio.create_task(self.run(identity,cfg));self.tasks[identity]=task
        task.add_done_callback(lambda _:self.tasks.pop(identity,None))
        return self.get(identity)

    def save(self,identity,status,payload,finished=False):
        with self.research.connect() as db:
            db.execute('UPDATE ei_analyses SET status=?,payload=?,finished_at=? WHERE id=?',(status,encoded(payload),now() if finished else None,identity))

    async def run(self,identity,cfg):
        payload={'phase':'pico','errors':[],'generation_version':VERSION};status='running'
        self.save(identity,status,payload)
        try:
            try: parsed=await self.parse_pico(cfg)
            except Exception as exc:
                if cfg['mode']=='rules': raise
                parsed={'pico':pico.validate(cfg['question'],pico.rules(cfg['question'])),
                        'generation':{'origin':'rule','rule_version':VERSION,'model_name':'not_applicable','model_version':'not_applicable','verification_status':'unverified'}}
                parsed['search_query']=cfg['search_query'] or pico.search_terms(cfg['question'],parsed['pico'])
                payload['errors'].append({'phase':'pico','error':type(exc).__name__,'message':'模型解析未通过，已明确降级为保守原文规则；可更换模型重试。'})
            payload.update(pico=parsed['pico'],pico_generation=parsed['generation'],search_query=parsed['search_query'],phase='retrieval')
            self.save(identity,status,payload)
            if cfg['live_search']:
                sync=self.importer.start(parsed['search_query'],cfg['limit'],False)
                payload['sync_run_id']=sync['id'];self.save(identity,status,payload)
                while self.importer.run(sync['id'])['status']=='running': await asyncio.sleep(.3)
                report=self.importer.run(sync['id']);payload['retrieval']=report
                payload['errors'].extend(report['report'].get('errors',[]))
            release=self.research.release(cfg['release_id']) if cfg['release_id'] else self.research.create_release('Evidence Intelligence：'+cfg['question'][:160])
            payload.update(release_id=release['id'],release_hash=release['content_hash'],phase='extraction')
            trial_ids=set(payload.get('retrieval',{}).get('report',{}).get('imported_trial_ids',[]))
            payload['trial_candidates']=[n for n in release['payload']['nodes'] if n['entity_type']=='ClinicalTrial' and any(i['scheme']=='NCT' and i['value'] in trial_ids for i in n['identifiers'])]
            publications=self.candidates(release,parsed['search_query'],cfg['limit'])
            payload['candidate_ids']=[p['entity']['id'] for p in publications]
            payload['retrieval_scope']='bounded_live_search_and_saved_sources' if cfg['live_search'] else 'frozen_saved_sources'
            payload['candidate_note']='词法召回候选，未人工确认与问题相关；不是系统综述的穷尽检索。'
            payload['processed']=0;self.save(identity,status,payload)
            for paper in publications:
                raw=paper['source']['payload'];proposal=extraction.baseline(raw)
                generation={'origin':'rule','rule_version':VERSION,'model_name':'not_applicable','model_version':'not_applicable','verification_status':'unverified'}
                if cfg['mode']!='rules':
                    try:
                        proposal,generation=await SemanticExtractor(self.bridge,cfg).generate('extract',{k:raw.get(k) for k in ('title','abstract','publication_types')})
                        validate_extraction(proposal,raw.get('abstract') or '',raw.get('title') or '',raw.get('publication_types') or [])
                    except Exception as exc:
                        payload['errors'].append({'phase':'extraction','pmid':raw['pmid'],'error':type(exc).__name__,
                                                 'message':'模型结果未通过结构或原文校验；本篇使用明确标记的规则提取。'})
                        proposal=extraction.baseline(raw)
                        generation={'origin':'rule','rule_version':VERSION,'model_name':'not_applicable','model_version':'not_applicable','verification_status':'unverified','fallback_from':cfg['mode']}
                generation['engine_hash']=cfg['engine_hash']
                self.store_extraction(identity,release,paper,proposal,generation)
                payload['processed']+=1;self.save(identity,status,payload)
            rows=self.extractions(identity)
            payload['comparisons']=compare(rows);payload['design_distribution']=dict(Counter(e['payload']['design']['label'] for e in rows))
            payload['graph']=self.graph(identity,cfg['question'],release,rows)
            payload['phase']='finished';payload['limitations']=['仅摘要与书目元数据；未核验全文。','自动抽取为候选，数值与引文定位校验不能代替医学语义审核。','没有自动 GRADE 等级、期刊评分或汇总疗效。']
            status='partial' if payload['errors'] else 'completed' if rows else 'empty'
        except asyncio.CancelledError:
            status='interrupted';payload['phase']='interrupted'
            sync_id=payload.get('sync_run_id')
            if sync_id and self.importer.run(sync_id)['status']=='running':
                # GraphImporter permits only one active sync; this run owns it.
                await self.importer.close()
        except Exception as exc:
            status='failed';payload['errors'].append({'phase':payload['phase'],'error':type(exc).__name__,'message':'分析未完成，请检查来源、参数或模型配置后重试。'})
        self.save(identity,status,payload,True)

    def candidates(self,release,query,limit):
        papers={}
        for node in release['payload']['nodes']:
            if node['entity_type']!='Publication': continue
            sources=[release['payload']['sources'][s['id']] for s in node['sources'] if s['id'] in release['payload']['sources'] and s['provider']=='PubMed']
            if not sources: continue
            source=max(sources,key=lambda s:s['fetched_at']);data=source['payload']
            if not data.get('title') or not data.get('pmid'): continue
            papers[node['id']]={'entity':node,'source':source}
        corpus=[]
        for key,value in papers.items():
            text=value['source']['payload']['title']+'\n'+(value['source']['payload'].get('abstract') or '')
            # Shared exact domain aliases enrich the search index, never the source abstract.
            aliases=' '.join(d['english']+' '+d['key'] for d in mentioned_domains(text))
            corpus.append({'document_id':key,'text':text+'\n'+aliases})
        ranking=BM25(corpus).rank(re.sub(r'\b(?:AND|OR|NOT)\b',' ',query))
        # Identifier lookups are exact, not a low-score text approximation.
        matched=[key for key,value in papers.items() if query.strip().removeprefix('PMID:').strip()==str(value['source']['payload']['pmid'])]
        ids=matched or [r['document_id'] for r in ranking]
        return [papers[key] for key in ids[:limit]]

    def store_extraction(self,analysis,release,paper,proposal,generation):
        raw=paper['source']['payload'];data=validate_extraction(proposal,raw.get('abstract') or '',raw.get('title') or '',raw.get('publication_types') or [])
        entity=paper['entity'];edges=eligible_edges(release['payload'],False)
        trial_nodes={n['id']:n for n in release['payload']['nodes'] if n['entity_type']=='ClinicalTrial'}
        links=[e for e in edges if (e['source_entity']==entity['id'] and e['target_entity'] in trial_nodes and e['relation_type']=='MENTIONS_TRIAL') or
               (e['target_entity']==entity['id'] and e['source_entity'] in trial_nodes and e['relation_type'] in ('HAS_RESULT_PUBLICATION','HAS_DERIVED_PUBLICATION','REFERENCES_PUBLICATION'))]
        trial_ids=sorted({e['target_entity'] if e['target_entity'] in trial_nodes else e['source_entity'] for e in links})
        data.update(publication={'entity_id':entity['id'],'pmid':raw['pmid'],'doi':raw.get('doi'),'title':raw['title'],'journal':raw.get('journal'),'publication_date':raw.get('pub_date'),
                                 'publication_types':raw.get('publication_types') or [],'url':paper['source']['source_url']},
                    source={'id':paper['source']['id'],'hash':paper['source']['checksum'],'abstract':raw.get('abstract') or '',
                            'fetched_at':paper['source']['fetched_at'],'source_updated_at':paper['source']['source_updated_at']},
                    generation=generation,extracted_at=now(),verification_status='unverified',trial_ids=trial_ids,trial_links=links,
                    trials=[trial_nodes[key] for key in trial_ids],release_id=release['id'],schema_version=VERSION)
        data['appraisal_tools']=quality.suggestions(data)
        identity=uuid.uuid4().hex
        with self.research.connect() as db:
            db.execute('INSERT INTO ei_extractions VALUES (?,?,?,?,?,?)',(identity,analysis,entity['id'],paper['source']['id'],encoded(data),now()))
            revision_id=revision(db,'evidence_extraction',identity,data)
        return identity

    def extractions(self,analysis):
        with self.research.connect() as db:
            rows=[{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM ei_extractions WHERE analysis_id=? ORDER BY created_at,id',(analysis,))]
            for row in rows:
                version=db.execute("SELECT id FROM research_revisions WHERE object_type='evidence_extraction' AND object_id=?",(row['id'],)).fetchone()
                row['revision_id']=version[0]
                event=db.execute('SELECT decision FROM research_reviews WHERE revision_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(version[0],)).fetchone()
                row['review_status']=event[0] if event else 'unreviewed'
        return rows

    def extraction(self,identity):
        with self.research.connect() as db:
            row=db.execute('SELECT analysis_id FROM ei_extractions WHERE id=?',(identity,)).fetchone()
            if not row: raise KeyError('抽取记录不存在')
        return next(r for r in self.extractions(row[0]) if r['id']==identity)

    def get(self,identity):
        with self.research.connect() as db:
            row=db.execute('SELECT * FROM ei_analyses WHERE id=?',(identity,)).fetchone()
            if not row: raise KeyError('证据分析不存在')
            result={**dict(row),'payload':json.loads(row['payload']),'config':json.loads(row['config'])}
            result['appraisals']=[{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute('SELECT a.* FROM ei_appraisals a JOIN ei_extractions e ON e.id=a.extraction_id WHERE e.analysis_id=? ORDER BY a.created_at DESC',(identity,))]
            result['statements']=[{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM ei_statements WHERE analysis_id=? ORDER BY created_at DESC',(identity,))]
        result['extractions']=self.extractions(identity)
        # Rejected candidates remain auditable but are excluded from the current comparison view.
        result['current_comparisons']=compare([r for r in result['extractions'] if r['review_status']!='rejected'])
        # Build the current review projection without changing the stored historical analysis.
        graph=json.loads(encoded(result['payload'].get('graph',{'nodes':[],'edges':[]})))
        lookup={r['id']:r for r in result['extractions']}
        rejected={r['entity_id'] for r in result['extractions'] if r['review_status']=='rejected'}
        for node in graph['nodes']:
            if node.get('entity_id') in rejected or lookup.get(node.get('extraction_id'),{}).get('review_status')=='rejected':
                node['label']='[审核拒绝] '+node['label']
        for statement in result['statements']:
            key='human-statement:'+statement['id'];graph['nodes'].append({'id':key,'label':statement['payload']['text'],'kind':'EvidenceStatement','human_statement_id':statement['id']})
            for index,citation in enumerate(statement['payload']['citations']):
                graph['edges'].append({'id':key+':'+str(index),'source':citation['publication']['entity_id'],'target':key,
                                       'label':{'supports':'人工标注支持','contradicts':'人工标注矛盾','context':'背景'}[citation['relationship']],
                                       'source_id':citation['source_id'],'quote':citation['quote'],'extraction_id':citation['extraction_id']})
        result['current_graph']=graph
        return result

    def list(self):
        with self.research.connect() as db: return [dict(r) for r in db.execute('SELECT id,question,status,created_at,finished_at FROM ei_analyses ORDER BY created_at DESC LIMIT 100')]

    def appraisal(self,identity,body):
        record=self.extraction(identity);data=quality.validate(record['payload'],body);key=uuid.uuid4().hex
        with self.research.connect() as db:
            db.execute('INSERT INTO ei_appraisals VALUES (?,?,?,?,?)',(key,identity,encoded(data),body['actor'].strip(),now()))
            version=revision(db,'evidence_appraisal',key,data)
            review(db,'evidence_appraisal',key,version,body['actor'],'recorded',body['rationale'] or '人工逐域评价记录',{'extraction_id':identity})
        return {'id':key,**data}

    def statement(self,identity,body):
        if not body['confirm_human_review'] or not body['actor'].strip(): raise ValueError('多论文结论需要人工审核确认')
        analysis=self.get(identity);sources={r['id']:r for r in analysis['extractions']};citations=[];seen=set()
        for item in body['citations']:
            row=sources.get(item['extraction_id'])
            if not row or row['review_status']=='rejected': raise ValueError('引用必须来自本次分析中未被拒绝的论文抽取')
            key=(item['extraction_id'],item['quote'],item['relationship'])
            if key in seen: raise ValueError('重复引用')
            seen.add(key)
            citations.append({**item,'span':locate(row['payload']['source']['abstract'],item['quote']),
                              'publication':row['payload']['publication'],'source_id':row['source_id'],'extraction_revision_id':row['revision_id']})
        key=uuid.uuid4().hex;data={**body,'citations':citations,'verification_status':'human_reviewed','source_scope':'abstract',
                                 'generation':{'origin':'human','model_name':'not_applicable','model_version':'not_applicable'},'release_id':analysis['payload']['release_id']}
        with self.research.connect() as db:
            db.execute('INSERT INTO ei_statements VALUES (?,?,?,?,?)',(key,identity,encoded(data),body['actor'].strip(),now()))
            version=revision(db,'evidence_statement',key,data)
            review(db,'evidence_statement',key,version,body['actor'],'accepted',body['reason'])
        return {'id':key,**data}

    def graph(self,identity,question,release,rows):
        p=release['payload'];medical={n['id']:n for n in p['nodes']};ids=set(match_entities(p,question));pubs={r['entity_id'] for r in rows};ids|=pubs
        edges=[e for e in eligible_edges(p,False) if e['source_entity'] in pubs or e['target_entity'] in pubs]
        for e in edges: ids.update([e['source_entity'],e['target_entity']])
        nodes=[{'id':n,'label':medical[n]['standard_name'],'kind':medical[n]['entity_type'],'entity_id':n} for n in sorted(ids)]
        links=[{'id':e['id'],'source':e['source_entity'],'target':e['target_entity'],'label':e['label'],'relation_id':e['id'],'evidence_source':e['evidence_source']} for e in edges]
        nodes.extend([{'id':'question:'+identity,'label':question,'kind':'ResearchQuestion'},{'id':'pico:'+identity,'label':'问题 PICO','kind':'PICO'}])
        links.append({'id':'parse:'+identity,'source':'question:'+identity,'target':'pico:'+identity,'label':'原文解析'})
        for entity in match_entities(p,question): links.append({'id':'pico:'+entity,'source':'pico:'+identity,'target':entity,'label':'问题提及（非因果）'})
        for row in rows:
            for finding in row['payload']['findings']:
                key=row['id']+':'+finding['id'];nodes.append({'id':key,'label':finding['outcome']+' · 作者原文','kind':'EvidenceStatement','extraction_id':row['id'],'finding_id':finding['id']})
                links.append({'id':'statement:'+key,'source':row['entity_id'],'target':key,'label':'摘要陈述'})
        return {'nodes':nodes,'edges':links,'note':'医学节点和关系引用共用图谱；问题、PICO、摘要陈述是分析视图，不新增重复医学实体。'}

    async def cancel(self,identity):
        self.get(identity)
        task=self.tasks.get(identity)
        if task: task.cancel();await task
        return self.get(identity)

    async def close(self):
        tasks=list(self.tasks.values())
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
