"""Evidence chains reference immutable graph statements; no clinical conclusions inferred."""
from collections import deque
import json
import uuid
from ..medical_core.db import encoded, now, digest, revision, review
from ..medical_core.service import eligible_edges, match_entities

def retrieve_graph(payload,query,depth=2,include_inferred=False):
    centers=match_entities(payload,query)
    if not centers: return {'entity_ids':[],'relation_ids':[],'paths':[],'status':'no_matches'}
    edges=eligible_edges(payload,include_inferred); seen=set(centers); selected={}; paths={c:[] for c in centers}
    queue=deque((c,0) for c in centers)
    while queue:
        current,distance=queue.popleft()
        if distance>=depth: continue
        for edge in edges:
            if current not in (edge['source_entity'],edge['target_entity']): continue
            other=edge['target_entity'] if current==edge['source_entity'] else edge['source_entity']
            selected[edge['id']]=edge
            if other not in seen:
                seen.add(other);paths[other]=paths[current]+[edge['id']];queue.append((other,distance+1))
    return {'entity_ids':sorted(seen),'relation_ids':sorted(selected),'paths':[{'target':k,'relation_ids':v} for k,v in paths.items()],
            'status':'completed','traversal':'connectivity_not_causality'}

def build_chain(payload,start,target=None,max_hops=4,include_inferred=False,directed=True):
    nodes={n['id']:n for n in payload['nodes']}
    if start not in nodes or (target and target not in nodes): raise ValueError('实体不属于指定图谱版本')
    if not 1<=max_hops<=6: raise ValueError('max_hops 必须为1–6')
    edges=eligible_edges(payload,include_inferred);queue=deque([(start,[])]);seen={start};selected=[]
    while queue:
        current,path=queue.popleft()
        if target and current==target: selected=path;break
        if len(path)>=max_hops: continue
        for edge in edges:
            other=None
            if edge['source_entity']==current: other=edge['target_entity']
            elif not directed and edge['target_entity']==current: other=edge['source_entity']
            if other is None: continue
            if not target and edge not in selected: selected.append(edge)
            if other not in seen: seen.add(other);queue.append((other,path+[edge]))
    if len(selected)>150: raise ValueError('证据过多，请缩小实体或路径范围')
    steps=[]
    for edge in selected:
        source=payload['sources'].get(edge['evidence_source'])
        if not source: raise ValueError('关系缺少冻结来源快照')
        steps.append({'relation_id':edge['id'],'relation_revision_id':edge['revision_id'],
                      'source_entity':edge['source_entity'],'target_entity':edge['target_entity'],
                      'statement':nodes[edge['source_entity']]['standard_name']+' → '+edge['label']+' → '+nodes[edge['target_entity']]['standard_name'],
                      'relation_type':edge['relation_type'],'evidence_quote':edge['evidence_text'],
                      'evidence_field':edge['evidence_field'],'source_snapshot_id':source['id'],
                      'source_url':source['source_url'],'source_provider':source['provider'],
                      'source_identifier':source['source_id'],'verification_status':edge['verification_status'],
                      'qualifiers':edge['qualifiers'],'human_review_status':next((r['decision'] for r in reversed(payload.get('reviews',[])) if r['revision_id']==edge['revision_id']),'unreviewed')})
    referenced={start}|{s[k] for s in steps for k in ('source_entity','target_entity')}
    return {'status':'assembled_unreviewed' if steps else 'insufficient_evidence','steps':steps,
            'nodes':[nodes[k] for k in sorted(referenced)],'start_entity':start,'target_entity':target,
            'max_hops':max_hops,'include_inferred':include_inferred,'directed':directed,
            'generation':{'origin':'rule','rule_version':'graph-evidence-chain-v1','model_name':'not_applicable','model_version':'not_applicable','verification_status':'unverified'},
            'note':'此链保留来源关系语义，不自动形成诊疗建议。连通、提及或研究用药不代表因果关系或有效性。'}

class EvidenceService:
    def __init__(self,research): self.research=research
    def create(self,release_id,query,start,target=None,max_hops=4,include_inferred=False,directed=True):
        release=self.research.release(release_id)
        payload=build_chain(release['payload'],start,target,max_hops,include_inferred,directed)
        payload.update(release_hash=release['content_hash'])
        identity=uuid.uuid4().hex
        with self.research.connect() as db:
            db.execute('INSERT INTO evidence_chains VALUES (?,?,?,?,?)',(identity,release_id,query,encoded(payload),now()))
        return self.get(identity)
    def get(self,identity):
        with self.research.connect() as db:
            row=db.execute('SELECT * FROM evidence_chains WHERE id=?',(identity,)).fetchone()
            if not row: raise KeyError('证据链不存在')
            return {**dict(row),'payload':json.loads(row['payload'])}
    def list(self):
        with self.research.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,release_id,query,created_at FROM evidence_chains ORDER BY created_at DESC LIMIT 200')]
