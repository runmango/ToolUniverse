import json
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .experiments import mine_negatives, negatives, report, review_negative, save_assessment
from .metrics import gold_labels
from .spec import METHODS

router=APIRouter(prefix='/api/benchmark',tags=['医学检索 Benchmark'])


def store(request): return request.app.state.benchmark


def require(action):
    try: return action()
    except KeyError as exc: raise HTTPException(404,str(exc.args[0])) from None


@router.get('/stats')
def stats(request:Request):
    service=store(request); datasets=service.datasets(); docs=set()
    for dataset in datasets:
        docs.update(d['document_id'] for d in service.snapshot(dataset['version_id'])['payload']['documents'])
    models=service.models()
    return {'dataset_count':len(datasets),'annotated_query_count':sum(d['annotated_query_count'] for d in datasets),
            'query_count':sum(d['query_count'] for d in datasets),'document_count':len(docs),'model_count':len(models),
            'configured_model_count':sum(m['config'].get('enabled',True) for m in models),'methods':METHODS}


@router.get('/datasets')
def datasets(request:Request): return {'datasets':store(request).datasets()}


@router.post('/datasets',status_code=201)
def create_dataset(request:Request,body:dict): return store(request).create_dataset(body)


@router.get('/datasets/{identity}')
def dataset(request:Request,identity:str,version:int|None=Query(None,ge=1)):
    return require(lambda:store(request).dataset(identity,version))


class ImportRequest(BaseModel):
    kind:str=Field(pattern='^(queries|documents)$')
    content:str=Field(max_length=20_000_000)
    attest_human_gold:bool=False
    actor:str=Field(default='',max_length=120)


@router.post('/datasets/{identity}/import')
def import_jsonl(request:Request,identity:str,body:ImportRequest):
    return require(lambda:store(request).import_jsonl(identity,body.content,body.kind,attest=body.attest_human_gold,actor=body.actor))


@router.post('/datasets/{identity}/import-graph')
def import_graph(request:Request,identity:str):
    return require(lambda:store(request).import_graph(identity,request.app.state.graph))


class EditRequest(BaseModel):
    record:dict
    actor:str=Field(min_length=1,max_length=120)
    confirm_human_review:bool=False


@router.post('/datasets/{identity}/queries')
def query_edit(request:Request,identity:str,body:EditRequest):
    if not body.confirm_human_review: raise HTTPException(422,'人工维护需要明确确认审核动作')
    return require(lambda:store(request).save_query(identity,body.record,body.actor))


@router.post('/datasets/{identity}/documents')
def document_edit(request:Request,identity:str,body:EditRequest):
    if not body.confirm_human_review: raise HTTPException(422,'文献实体标注需要明确确认审核动作')
    return require(lambda:store(request).save_document(identity,body.record,body.actor))


@router.get('/datasets/{identity}/export')
def export_dataset(request:Request,identity:str,kind:str=Query('queries',pattern='^(queries|documents)$'),version:int|None=Query(None,ge=1)):
    data=require(lambda:store(request).dataset(identity,version))
    content='\n'.join(json.dumps(x,ensure_ascii=False) for x in data['snapshot']['payload'][kind])+'\n'
    return Response(content,media_type='application/x-ndjson',headers={'Content-Disposition':f'attachment; filename="benchmark-{kind}-v{data["snapshot"]["version"]}.jsonl"'})


@router.get('/datasets/{identity}/audit')
def audit(request:Request,identity:str): return {'audit':store(request).audit(identity)}


@router.get('/models')
def models(request:Request): return {'models':store(request).models()}


@router.post('/models',status_code=201)
def add_model(request:Request,body:dict): return store(request).add_model(body)


class RunRequest(BaseModel):
    dataset_version_id:str
    model_ids:list[str]=Field(min_length=1,max_length=8)
    top_k:int=Field(default=20,ge=20,le=200)
    relevance_threshold:int=Field(default=2,ge=1,le=3)


@router.post('/experiments',status_code=202)
async def run(request:Request,body:RunRequest):
    if request.app.state.benchmark_runner.tasks: raise HTTPException(409,'已有实验运行中')
    return require(lambda:request.app.state.benchmark_runner.start(body.dataset_version_id,body.model_ids,top_k=body.top_k,threshold=body.relevance_threshold))


@router.get('/experiments')
def experiments(request:Request,dataset_id:str|None=None): return {'experiments':store(request).experiments(dataset_id)}


@router.get('/experiments/{identity}')
def experiment(request:Request,identity:str): return require(lambda:store(request).experiment(identity))


@router.post('/experiments/{identity}/replay')
def replay(request:Request,identity:str): return require(lambda:request.app.state.benchmark_runner.replay(identity))


@router.post('/experiments/{identity}/rerun',status_code=202)
async def rerun(request:Request,identity:str):
    if request.app.state.benchmark_runner.tasks: raise HTTPException(409,'已有实验运行中')
    original=require(lambda:store(request).experiment(identity))
    return request.app.state.benchmark_runner.start(original['dataset_version_id'],[],top_k=original['config']['top_k'],threshold=original['config']['relevance_threshold'],rerun=original)


@router.get('/experiments/{identity}/report')
def export_report(request:Request,identity:str,format:str=Query('json',pattern='^(json|csv|markdown)$')):
    result=require(lambda:report(store(request),identity,format))
    mime={'json':'application/json','csv':'text/csv; charset=utf-8','markdown':'text/markdown; charset=utf-8'}[format]
    extension='md' if format=='markdown' else format
    return Response(result,media_type=mime,headers={'Content-Disposition':f'attachment; filename="benchmark-{identity}.{extension}"'})


@router.get('/compare')
def compare(request:Request,ids:str=Query(max_length=400),query_id:str=Query(max_length=100)):
    values=list(dict.fromkeys(ids.split(',')))
    if not 1<=len(values)<=8: raise HTTPException(422,'请选择1–8个实验')
    experiments=[require(lambda key=key:store(request).experiment(key)) for key in values]
    signatures={(e['content_hash'],e['corpus_hash'],e['config']['top_k'],e['config']['relevance_threshold'],e['config']['engine_hash'],e['config'].get('graph_release_hash')) for e in experiments}
    items=[]
    for item in experiments:
        snapshot=store(request).snapshot(item['dataset_version_id'])
        query=next((q for q in snapshot['payload']['queries'] if q['query_id']==query_id),None)
        row=next((r for r in item['results'] if r['query_id']==query_id),None)
        documents={d['document_id']:d for d in snapshot['payload']['documents']}
        items.append({'experiment':{k:v for k,v in item.items() if k not in ('results','assessments')},'query':query,
                      'result':row,'documents':[documents[r['document_id']] for r in row['ranking']] if row else [],
                      'assessments':[a for a in item['assessments'] if a['query_id']==query_id]})
    return {'comparable':len(signatures)==1,'reason':None if len(signatures)==1 else '数据、Gold、代码或评价配置不一致；不能直接比较模型优劣。','items':items}


class MiningRequest(BaseModel):
    dataset_version_id:str
    query_id:str
    method:str=Field(pattern='^(random|hard|in_batch)$')
    count:int=Field(default=10,ge=1,le=100)
    seed:int=42
    experiment_id:str|None=None


@router.post('/negatives/mine')
def mine(request:Request,body:MiningRequest):
    return {'negatives':require(lambda:mine_negatives(store(request),body.dataset_version_id,body.query_id,body.method,body.count,body.seed,body.experiment_id))}


@router.get('/negatives')
def get_negatives(request:Request,dataset_version_id:str): return {'negatives':negatives(store(request),dataset_version_id)}


class NegativeReview(BaseModel):
    actor:str=Field(min_length=1,max_length=120)
    accepted:bool
    category:str='unclassified'
    note:str=Field(min_length=1,max_length=2000)


@router.post('/negatives/{identity}/review')
def review(request:Request,identity:str,body:NegativeReview):
    return require(lambda:review_negative(store(request),identity,body.actor,body.accepted,body.category,body.note))


@router.post('/experiments/{identity}/queries/{query_id}/assessments')
def assess(request:Request,identity:str,query_id:str,body:EditRequest):
    if not body.confirm_human_review: raise HTTPException(422,'医学评价需要人工审核确认')
    return require(lambda:save_assessment(store(request),identity,query_id,body.record,body.actor))


class ResearchEvaluationRequest(BaseModel):
    task_type:str=Field(pattern='^(graph|evidence_chain)$')
    release_id:str
    query:str=Field(default='',max_length=3000)
    depth:int=Field(default=2,ge=1,le=6)
    include_inferred:bool=False
    chain_id:str|None=None
    gold:dict=Field(default_factory=dict)
    judgments:list[dict]=Field(default_factory=list,max_length=150)
    actor:str=Field(min_length=1,max_length=120)
    confirm_human_review:bool=False

@router.post('/research-evaluations',status_code=201)
def research_evaluation(request:Request,body:ResearchEvaluationRequest):
    from .research_evaluation import evaluate
    return evaluate(request.app.state.research,request.app.state.evidence,body.model_dump())

@router.get('/research-evaluations')
def research_evaluations(request:Request):
    from .research_evaluation import evaluations
    return {'evaluations':evaluations(request.app.state.research)}


class ExtractionEvaluationRequest(BaseModel):
    analysis_id:str
    labels:list[dict]=Field(default_factory=list,max_length=200)
    actor:str=Field(min_length=1,max_length=120)
    confirm_human_review:bool=False


@router.post('/evidence-extraction-evaluations',status_code=201)
def extraction_evaluation(request:Request,body:ExtractionEvaluationRequest):
    from ..evidence_intelligence.evaluation import evaluate
    return evaluate(request.app.state.intelligence,body.model_dump())
