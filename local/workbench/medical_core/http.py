"""Consistent error envelopes and versioned aliases with legacy compatibility."""
import logging
import re
import time
import uuid
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from ..codex_bridge import clean_error

logger=logging.getLogger('medical_lab.http')

def configure_logging(runtime):
    from logging.handlers import RotatingFileHandler
    parent=logging.getLogger('medical_lab')
    if not parent.handlers:
        handler=RotatingFileHandler(runtime/'research.requests.jsonl',maxBytes=5_000_000,backupCount=3,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    parent.propagate=False

def error(request,status,message,code):
    return JSONResponse({'detail':message,'error':{'code':code,'message':message,'request_id':getattr(request.state,'request_id',None)}},status_code=status)

def versioned(router):
    result=APIRouter()
    for route in router.routes:
        result.add_api_route(route.path.replace('/api/','/api/v1/',1),route.endpoint,methods=route.methods,
                             status_code=route.status_code,response_model=route.response_model,tags=route.tags)
    return result

def install(app):
    @app.exception_handler(HTTPException)
    async def http_error(request,exc): return error(request,exc.status_code,str(exc.detail),'HTTP_'+str(exc.status_code))
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        fields=', '.join('.'.join(str(p) for p in e['loc']) for e in exc.errors())
        return error(request,422,'请求字段格式无效：'+fields,'VALIDATION_ERROR')
    @app.exception_handler(KeyError)
    async def missing(request,exc): return error(request,404,str(exc.args[0]),'NOT_FOUND')
    @app.exception_handler(ValueError)
    async def invalid(request,exc): return error(request,400,clean_error(exc),'INVALID_ARGUMENT')
    @app.exception_handler(RuntimeError)
    async def upstream(request,exc): return error(request,502,clean_error(exc),'UPSTREAM_ERROR')
    @app.middleware('http')
    async def trace(request,call_next):
        request.state.request_id=uuid.uuid4().hex;started=time.perf_counter()
        path=request.url.path.replace('/api/v1/','/api/')
        if getattr(app.state,'intelligence_error',None) and path.startswith('/api/evidence-intelligence'):
            response=error(request,503,'证据分析暂不可用；其他工作台功能可继续使用。','EVIDENCE_UNAVAILABLE')
        elif getattr(app.state,'research_error',None) and any(path.startswith(p) for p in ('/api/graph','/api/benchmark','/api/research','/api/evidence-chains','/api/evidence-intelligence')):
            response=error(request,503,'科研模块暂不可用；对话和连接设置可继续使用。','RESEARCH_UNAVAILABLE')
        else:
            try: response=await call_next(request)
            except Exception as exc:
                import json
                logger.error(json.dumps({'event':'request_failed','request_id':request.state.request_id,'exception':type(exc).__name__}))
                response=error(request,500,'服务处理失败，请凭请求编号查看日志。','INTERNAL_ERROR')
        # Preserve old detail while adding the unified envelope to boundary rejections.
        response.headers['X-Request-ID']=request.state.request_id
        import json
        logger.info(json.dumps({'request_id':request.state.request_id,'method':request.method,'path':request.url.path,
                               'status':response.status_code,'duration_ms':round((time.perf_counter()-started)*1000,2)}))
        return response
