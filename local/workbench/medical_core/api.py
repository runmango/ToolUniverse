from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field
from .model import ENTITY_TYPES, RELATIONS
from .identifiers import identifier

router=APIRouter(prefix='/api/research',tags=['统一科研模型与版本'])

def service(request): return request.app.state.research

@router.get('/schema')
def schema(): return {'version':'medical-core-v1','entity_types':ENTITY_TYPES,'relations':RELATIONS}

@router.get('/releases')
def releases(request:Request): return {'releases':service(request).releases()}

class ReleaseRequest(BaseModel): note:str=Field(default='人工冻结科研图谱版本',max_length=500)

@router.post('/releases',status_code=201)
def freeze(request:Request,body:ReleaseRequest): return service(request).create_release(body.note)

@router.get('/releases/{identity}')
def release(request:Request,identity:str): return service(request).release(identity)

@router.get('/resolve')
def resolve(request:Request,scheme:str,value:str):
    scheme,value=identifier(scheme,value)
    with service(request).connect() as db:
        row=db.execute('SELECT entity_id FROM kg_identifiers WHERE scheme=? AND value=?',(scheme,value)).fetchone()
        return {'scheme':scheme,'value':value,'entity_id':row[0] if row else None,'resolved':bool(row)}

@router.get('/reviews')
def reviews(request:Request,object_id:str|None=None): return {'reviews':service(request).reviews(object_id)}

class ReviewRequest(BaseModel):
    revision_id:str
    actor:str=Field(min_length=1,max_length=120)
    decision:str=Field(pattern='^(accepted|rejected|needs_review)$')
    reason:str=Field(min_length=1,max_length=2000)
    confirm_human_review:bool=False

@router.post('/reviews',status_code=201)
def review(request:Request,body:ReviewRequest):
    if not body.confirm_human_review: raise HTTPException(422,'需要明确人工审核确认')
    return service(request).review_revision(body.revision_id,body.actor,body.decision,body.reason)

@router.get('/revisions/{identity}')
def revision_detail(request:Request,identity:str):
    import json
    with service(request).connect() as db:
        row=db.execute('SELECT * FROM research_revisions WHERE id=?',(identity,)).fetchone()
        if not row: raise HTTPException(404,'版本不存在')
        return {**dict(row),'payload':json.loads(row['payload'])}
