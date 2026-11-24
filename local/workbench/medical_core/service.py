import json
from pathlib import Path
import uuid
from .db import SQLiteStore, initialize, revision, digest, encoded, now, review
from .registry import EntityRegistry
from .model import RELATIONS, normalize, domain_for
from .identifiers import identifier

class ResearchService(EntityRegistry, SQLiteStore):
    def __init__(self,path):
        self.db_path=Path(path)
        with self.connect() as db: initialize(db)

    def create_release(self,note='冻结科研图谱版本'):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            nodes=[];edges=[]
            for row in db.execute('SELECT * FROM kg_entities ORDER BY id').fetchall():
                item=self._entity(db,row)
                item['revision_id']=revision(db,'entity',item['id'],item)
                nodes.append(item)
            for row in db.execute('SELECT * FROM kg_edges WHERE active=1 ORDER BY id').fetchall():
                item=dict(row);item['qualifiers']=json.loads(item['qualifiers']);item['label']=RELATIONS[item['relation_type']][0]
                item['revision_id']=revision(db,'relation',item['id'],item)
                edges.append(item)
            sources={r['id']:{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM kg_sources ORDER BY id')}
            reviews=[dict(r) for r in db.execute("SELECT * FROM research_reviews WHERE object_type IN ('entity','relation') ORDER BY created_at,id")]
            payload={'nodes':nodes,'edges':edges,'sources':sources,'reviews':reviews,'schema_version':'medical-core-v1',
                     'history_scope':'captured_snapshot; unavailable pre-migration history is not reconstructed'}
            content_hash=digest(payload);identity=uuid.uuid4().hex
            db.execute('INSERT INTO research_releases VALUES (?,?,?,?,?)',(identity,content_hash,encoded(payload),now(),note))
        return self.release(identity)

    def releases(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,content_hash,created_at,note FROM research_releases ORDER BY created_at DESC')]

    def release(self,identity):
        with self.connect() as db:
            row=db.execute('SELECT * FROM research_releases WHERE id=?',(identity,)).fetchone()
            if not row: raise KeyError('科研版本不存在')
            return {**dict(row),'payload':json.loads(row['payload'])}

    def reviews(self,object_id=None):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM research_reviews'+(' WHERE object_id=?' if object_id else '')+' ORDER BY created_at DESC', (object_id,) if object_id else ())]

    def review_revision(self,identity,actor,decision,reason):
        if decision not in {'accepted','rejected','needs_review'}: raise ValueError('审核决定无效')
        with self.connect() as db:
            row=db.execute('SELECT * FROM research_revisions WHERE id=?',(identity,)).fetchone()
            if not row: raise KeyError('审核对象版本不存在')
            key=review(db,row['object_type'],row['object_id'],identity,actor,decision,reason)
        return {'id':key,'revision_id':identity,'decision':decision}

    def bind_benchmark(self,version_id,payload):
        with self.connect() as db: bind_documents(db,version_id,payload)


def bind_documents(db,version_id,payload):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='kg_entities'").fetchone(): return
    for doc in payload['documents']:
        candidates=set()
        for scheme,value in doc.get('identifiers',{}).items():
            scheme,value=identifier(scheme,value)
            row=db.execute('SELECT entity_id FROM kg_identifiers WHERE scheme=? AND value=?',(scheme,value)).fetchone()
            if row: candidates.add(row[0])
        linked=doc.get('source',{}).get('graph_entity_id')
        if linked: candidates.add(linked)
        if len(candidates)>1: raise ValueError('文献绑定存在标识冲突')
        if not candidates: continue
        identity=next(iter(candidates));row=db.execute('SELECT * FROM kg_entities WHERE id=?',(identity,)).fetchone()
        if not row: raise ValueError('文献引用的实体不存在')
        if row['entity_type'] not in {'Publication','ClinicalTrial'}: raise ValueError('文献绑定类型错误')
        item=EntityRegistry()._entity(db,row)
        version=revision(db,'entity',identity,item)
        db.execute('INSERT OR IGNORE INTO research_bindings VALUES (?,?,?,?)',(version_id,doc['document_id'],identity,version))


def latest_decisions(payload):
    result={}
    for event in payload.get('reviews',[]): result[event['revision_id']]=event['decision']
    return result

def eligible_edges(payload,include_inferred=False):
    decisions=latest_decisions(payload)
    return [e for e in payload['edges'] if e.get('active',1) and
            e['verification_status'] in ({'source_asserted','inferred'} if include_inferred else {'source_asserted'}) and
            decisions.get(e['revision_id'])!='rejected']

def match_entities(payload,text):
    term=normalize(text);domain=domain_for(text)
    if domain: term=normalize(domain['name'])
    exact=[];partial=[]
    for node in payload['nodes']:
        names={normalize(x) for x in [node['standard_name'],node.get('english_name') or '',*node.get('synonyms',[])] if x}
        if term in names: exact.append(node['id'])
        elif term and any(term in name or (len(name)>2 and name in term) for name in names): partial.append(node['id'])
    return exact or partial
