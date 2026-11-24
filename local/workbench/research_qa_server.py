"""Isolated UI-test server: synthetic fixtures never enter the workbench database."""
import argparse
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from workbench.app import local_boundary, value_error, runtime_error
from workbench.benchmark.api import router
from workbench.benchmark.store import BenchmarkStore
from workbench.benchmark.experiments import ExperimentRunner
from workbench.knowledge_graph.store import GraphStore
from workbench.knowledge_graph.ingest import GraphImporter
from workbench.medical_core.service import ResearchService
from workbench.medical_core.api import router as core_router
from workbench.medical_core.http import install, versioned
from workbench.evidence_chain.service import EvidenceService
from workbench.evidence_chain.api import router as evidence_router
from test_research_platform import paper, trial

@asynccontextmanager
async def lifespan(app):
    with tempfile.TemporaryDirectory(prefix='medical-benchmark-TEST-') as directory:
        path=Path(directory)/'TEST-only.sqlite'
        app.state.graph=GraphStore(path)
        importer=GraphImporter(app.state.graph,None)
        importer.import_trial(trial());importer.import_publication(paper())
        app.state.research=ResearchService(path)
        app.state.evidence=EvidenceService(app.state.research)
        app.state.research.create_release('TEST ONLY 非临床来源夹具')
        app.state.benchmark=BenchmarkStore(path)
        app.state.benchmark_runner=ExperimentRunner(app.state.benchmark,app.state.research)
        dataset=app.state.benchmark.create_dataset({'name':'TEST ONLY 非临床评测夹具','domain':'MDS','description':'Isolated browser-test fixtures, not real evidence or medical Gold.'})
        docs=[{'document_id':'TEST_A','title':'TEST ONLY MDS therapy','text':'TEST fixture evidence statement.'},
              {'document_id':'TEST_B','title':'TEST ONLY transplantation','text':'TEST unrelated population.'},
              {'document_id':'TEST_C','title':'TEST ONLY donor lymphocyte infusion','text':'TEST other trial.'}]
        app.state.benchmark.import_jsonl(dataset['id'],'\n'.join(json.dumps(d) for d in docs),'documents')
        app.state.benchmark.import_jsonl(dataset['id'],json.dumps({'query_id':'TEST_001','query':'TEST MDS therapy','domain':'MDS','query_type':'clinical_research','relevant_documents':[],'annotation_source':'ai','verification_status':'candidate'}),'queries')
        yield
        await app.state.benchmark_runner.close()

app=FastAPI(lifespan=lifespan)
app.middleware('http')(local_boundary)
app.add_exception_handler(ValueError,value_error)
app.add_exception_handler(RuntimeError,runtime_error)
app.include_router(router)
for r in (router,core_router,evidence_router):
    if r is not router: app.include_router(r)
    app.include_router(versioned(r))
install(app)

@app.get('/api/health')
def health(): return {'status':'healthy','service':'benchmark-TEST-only'}

@app.get('/api/status')
def status(): return {'tools':{'connected':False,'loaded_tools_count':0},'connection':{'installed':False,'connected':False},'version':'TEST-only'}

@app.get('/api/sessions')
def sessions(): return {'sessions':[]}

@app.get('/api/models')
def models(): return {'models':[]}

@app.post('/api/test/shutdown')
def shutdown():
    server.should_exit=True
    return {'stopping':True}

app.mount('/',StaticFiles(directory=Path(__file__).resolve().parents[2]/'frontend/dist',html=True),name='test-frontend')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8092)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=parser.parse_args().port,log_level='warning'))
    server.run()
