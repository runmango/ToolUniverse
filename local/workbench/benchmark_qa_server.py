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

@asynccontextmanager
async def lifespan(app):
    with tempfile.TemporaryDirectory(prefix='medical-benchmark-TEST-') as directory:
        app.state.benchmark=BenchmarkStore(Path(directory)/'TEST-only.sqlite')
        app.state.benchmark_runner=ExperimentRunner(app.state.benchmark)
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
