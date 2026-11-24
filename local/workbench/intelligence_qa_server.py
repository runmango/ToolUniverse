"""Temporary TEST ONLY server; synthetic evidence never reaches production."""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from workbench.app import local_boundary
from workbench.medical_core.http import install,versioned
from workbench.medical_core.service import ResearchService
from workbench.knowledge_graph.store import GraphStore
from workbench.knowledge_graph.ingest import GraphImporter
from workbench.benchmark.store import BenchmarkStore
from workbench.benchmark.api import router as benchmark_router
from workbench.evidence_intelligence.service import IntelligenceService
from workbench.evidence_intelligence.api import router
from test_evidence_intelligence import paper

@asynccontextmanager
async def lifespan(app):
    with tempfile.TemporaryDirectory(prefix='evidence-intelligence-TEST-') as directory:
        path=Path(directory)/'TEST-only.sqlite';graph=GraphStore(path);importer=GraphImporter(graph,None)
        importer.import_publication(paper());importer.import_publication(paper('99999992',True))
        app.state.research=ResearchService(path);app.state.benchmark=BenchmarkStore(path)
        app.state.intelligence=IntelligenceService(app.state.research,importer,app.state.benchmark,None)
        yield
        await app.state.intelligence.close();await importer.close()

app=FastAPI(lifespan=lifespan)
app.middleware('http')(local_boundary)
for r in (router,benchmark_router): app.include_router(r);app.include_router(versioned(r))
install(app)
@app.get('/api/health')
def health(): return {'status':'healthy','service':'intelligence-TEST-only'}
@app.get('/api/status')
def status(): return {'tools':{'connected':False,'loaded_tools_count':0},'connection':{'installed':False,'connected':False},'version':'TEST-only'}
@app.get('/api/sessions')
def sessions(): return {'sessions':[]}
@app.get('/api/models')
def models(): return {'models':[]}
@app.post('/api/test/shutdown')
def shutdown(): server.should_exit=True;return {'stopping':True}
app.mount('/',StaticFiles(directory=Path(__file__).resolve().parents[2]/'frontend/dist',html=True),name='test-frontend')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8093)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=parser.parse_args().port,log_level='warning'));server.run()
