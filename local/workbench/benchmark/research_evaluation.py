"""Separate reviewed graph/evidence evaluation; labels never enter retrieval."""
import uuid
import json
from ..medical_core.db import encoded,now,review,digest
from ..evidence_chain.service import retrieve_graph
from ..medical_core.service import match_entities

def ratio(a,b): return a/b if b else None

def evaluate(research,evidence,body):
    actor=body['actor'].strip()
    if not actor or body.get('confirm_human_review') is not True: raise ValueError('评测标注需要人工明确确认和审核人')
    release=research.release(body['release_id']);payload=release['payload'];kind=body['task_type']
    if kind=='graph':
        query=body.get('query','').strip()
        if not query: raise ValueError('需要查询文本')
        # Compute the run BEFORE reading any gold fields.
        result=retrieve_graph(payload,query,body.get('depth',2),body.get('include_inferred',False))
        gold=body.get('gold',{})
        if not isinstance(gold,dict): raise ValueError('Gold 必须为 JSON 对象')
        metrics={}
        for field,label,available in [('entity_ids','Entity', {n['id'] for n in payload['nodes']}),('relation_ids','Relation',{e['id'] for e in payload['edges']})]:
            expected=gold.get(field,[])
            if not isinstance(expected,list) or any(not isinstance(x,str) for x in expected) or not set(expected)<=available: raise ValueError('Gold ID 不属于固定图谱版本')
            expected=set(expected);observed=set(result[field]);hits=len(expected&observed)
            metrics[label+'Precision']=(hits/len(observed) if observed else 0.0) if expected else None
            metrics[label+'Recall']=ratio(hits,len(expected))
        expected_paths=gold.get('paths',[])
        if not isinstance(expected_paths,list): raise ValueError('路径 Gold 必须为数组')
        edge_lookup={e['id']:e for e in payload['edges']};edge_ids=set(edge_lookup)
        if any(not isinstance(p,list) or not p or any(not isinstance(e,str) or e not in edge_ids for e in p) for p in expected_paths): raise ValueError('路径 Gold 包含无效关系')
        centers=set(match_entities(payload,query))
        for path in expected_paths:
            current=centers.copy()
            for key in path:
                edge=edge_lookup[key];following=set()
                if edge['source_entity'] in current: following.add(edge['target_entity'])
                if edge['target_entity'] in current: following.add(edge['source_entity'])
                current=following
            if not current: raise ValueError('路径 Gold 不构成从查询实体出发的连续关系')
        paths={tuple(p['relation_ids']) for p in result['paths']}
        unique_paths={tuple(p) for p in expected_paths}
        metrics['PathHitRate']=ratio(len(unique_paths&paths),len(unique_paths))
        saved={'query':query,'gold':gold,'result':result,'metrics':metrics,'task_version':'graph-review-v1'}
    elif kind=='evidence_chain':
        chain=evidence.get(body['chain_id'])
        if chain['release_id']!=release['id']: raise ValueError('证据链与图谱版本不一致')
        judgments=body.get('judgments',[]);steps={s['relation_id']:s for s in chain['payload']['steps']}
        if not isinstance(judgments,list): raise ValueError('judgments 必须为数组')
        seen=set()
        for item in judgments:
            if not isinstance(item,dict) or item.get('relation_id') not in steps or item['relation_id'] in seen: raise ValueError('审核关系无效或重复')
            seen.add(item['relation_id'])
            if not isinstance(item.get('reason'),str) or not item['reason'].strip(): raise ValueError('每项审核需要依据')
            for field in ('citation_exists','supports_statement','trial_match_correct'):
                if item.get(field) is not None and type(item[field]) is not bool: raise ValueError('评价仅接受 true、false 或 null')
            if item.get('supports_statement') is True and item.get('citation_exists') is not True:
                raise ValueError('支持性判断需要先确认引用真实存在')
            if item.get('trial_match_correct') is not None:
                step=steps[item['relation_id']]
                types={n['id']:n['entity_type'] for n in chain['payload']['nodes']}
                if not any(types.get(step[key])=='ClinicalTrial' for key in ('source_entity','target_entity')):
                    raise ValueError('试验关联评价仅适用于包含 ClinicalTrial 的关系')
        metrics={}
        for field,name in [('citation_exists','CitationAccuracy'),('supports_statement','EvidenceSupportRate'),('trial_match_correct','ClinicalTrialMatchingAccuracy')]:
            judged=[i[field] for i in judgments if type(i.get(field)) is bool]
            metrics[name]={'value':ratio(sum(judged),len(judged)),'judged':len(judged),'total_steps':len(steps),'coverage':ratio(len(judged),len(steps))}
        saved={'chain_id':chain['id'],'chain':chain,'judgments':judgments,'metrics':metrics,'task_version':'chain-review-v1'}
    else: raise ValueError('task_type 必须为 graph 或 evidence_chain')
    values=[v.get('value') if isinstance(v,dict) else v for v in metrics.values()]
    saved.update(annotation_source='human',verification_status='verified',actor=actor,release_hash=release['content_hash'],
                 evaluation_status='scored' if any(v is not None for v in values) else 'awaiting_gold')
    identity=uuid.uuid4().hex
    with research.connect() as db:
        db.execute('INSERT INTO research_evaluations VALUES (?,?,?,?,?,?)',(identity,release['id'],kind,encoded(saved),actor,now()))
        review(db,'research_evaluation',identity,digest(saved),actor,'recorded','人工审核图谱或证据链评测')
    return {'id':identity,'task_type':kind,'release_id':release['id'],**saved}

def evaluations(research):
    with research.connect() as db:
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM research_evaluations ORDER BY created_at DESC LIMIT 200')]
