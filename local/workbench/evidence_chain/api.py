import json
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

router=APIRouter(prefix='/api/evidence-chains',tags=['科研证据链'])

class CreateChain(BaseModel):
    release_id:str
    query:str=Field(default='',max_length=3000)
    start_entity:str
    target_entity:str|None=None
    max_hops:int=Field(default=3,ge=1,le=6)
    include_inferred:bool=False
    directed:bool=True

@router.get('')
def chains(request:Request): return {'chains':request.app.state.evidence.list()}

@router.post('',status_code=201)
def create(request:Request,body:CreateChain):
    return request.app.state.evidence.create(body.release_id,body.query,body.start_entity,body.target_entity,body.max_hops,body.include_inferred,body.directed)

@router.get('/{identity}')
def chain(request:Request,identity:str): return request.app.state.evidence.get(identity)

@router.get('/{identity}/export')
def export(request:Request,identity:str):
    record=request.app.state.evidence.get(identity)
    record['release']=request.app.state.research.release(record['release_id'])
    return Response(json.dumps(record,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="evidence-{identity}.json"'})
