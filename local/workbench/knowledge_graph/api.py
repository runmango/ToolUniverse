from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix='/api/graph', tags=['医学知识图谱'])

def store(request):
    return request.app.state.graph

def types(value):
    return tuple(x for x in value.split(',') if x)

def require(call):
    try:
        return call()
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from None

@router.get('/stats')
def stats(request: Request):
    return store(request).stats()

@router.get('/entities')
def entities(request: Request, q: str = Query('', max_length=200), entity_types: str = Query('', max_length=200), limit: int = Query(30, ge=1, le=100)):
    return {'entities': store(request).search(q, types(entity_types), limit)}

@router.get('/entities/{entity_id}')
def entity(request: Request, entity_id: str):
    return require(lambda: store(request).get(entity_id))

@router.get('/neighborhood')
def neighborhood(request: Request, center: str, depth: int = Query(1, ge=1, le=2), entity_types: str = Query('', max_length=200), include_inferred: bool = True, limit: int = Query(160, ge=1, le=400)):
    return require(lambda: store(request).neighborhood(center, depth, types(entity_types), include_inferred, limit))

@router.get('/edges/{edge_id}')
def edge(request: Request, edge_id: str):
    return require(lambda: store(request).evidence(edge_id))

@router.get('/paths')
def paths(request: Request, source: str, target: str, max_hops: int = Query(5, ge=1, le=6), include_inferred: bool = False):
    return require(lambda: store(request).path(source, target, max_hops, include_inferred))

class SyncRequest(BaseModel):
    query: str = Field(min_length=1, max_length=160)
    limit: int = Field(default=5, ge=1, le=10)
    molecular: bool = True

@router.post('/sync', status_code=202)
async def sync(request: Request, body: SyncRequest):
    if not body.query.strip():
        raise HTTPException(422, '请输入同步主题或原始编号')
    importer = request.app.state.graph_importer
    if importer.tasks:
        raise HTTPException(409, '已有图谱同步正在进行，请等待完成')
    return importer.start(body.query.strip(), body.limit, body.molecular)

@router.get('/sync/{run_id}')
def sync_run(request: Request, run_id: str):
    return require(lambda: request.app.state.graph_importer.run(run_id))

@router.get('/export')
def export(request: Request, center: str, depth: int = Query(2, ge=1, le=2), format: str = Query('json', pattern='^(json|csv)$'),
           entity_types: str = Query('', max_length=200), include_inferred: bool = True):
    graph = require(lambda: store(request).neighborhood(center, depth, types(entity_types), include_inferred, 400))
    if format == 'csv':
        return Response(store(request).csv_export(graph), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename="medical-knowledge-graph.csv"'})
    # JSON includes source snapshots, so evidence remains auditable off-line.
    graph['evidence'] = {e['evidence_source']:store(request).evidence(e['id'])['source_record'] for e in graph['edges']}
    return JSONResponse(graph, headers={'Content-Disposition':'attachment; filename="medical-knowledge-graph.json"'})
