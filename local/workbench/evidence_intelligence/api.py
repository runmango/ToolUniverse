import json
from fastapi import APIRouter, Request, Query
from fastapi.responses import Response
from .models import AnalyzeRequest, ReviewRequest, AppraisalRequest, StatementRequest, FIELDS, DESIGNS, VERSION
from .quality import TOOLS

router=APIRouter(prefix='/api/evidence-intelligence',tags=['Evidence Intelligence：摘要证据分析'])

def service(request): return request.app.state.intelligence

@router.get('/schema')
def schema(): return {'version':VERSION,'fields':FIELDS,'designs':DESIGNS,'appraisal_tools':TOOLS,'scope':'abstract_only'}

@router.post('/pico')
async def parse(request:Request,body:AnalyzeRequest): return await service(request).parse_pico(body.model_dump())

@router.get('/analyses')
def analyses(request:Request): return {'analyses':service(request).list()}

@router.post('/analyses',status_code=202)
async def start(request:Request,body:AnalyzeRequest): return service(request).start(body.model_dump())

@router.get('/analyses/{identity}')
def analysis(request:Request,identity:str): return service(request).get(identity)

@router.post('/analyses/{identity}/cancel')
async def cancel(request:Request,identity:str): return await service(request).cancel(identity)

@router.get('/analyses/{identity}/export')
def export(request:Request,identity:str,format:str=Query('json',pattern='^(json|markdown)$')):
    record=service(request).get(identity)
    release=record['payload'].get('release_id')
    if release: record['release']=service(request).research.release(release)
    if format=='json': content=json.dumps(record,ensure_ascii=False,indent=2)
    else:
        content='# 科研证据分析报告\n\n'+record['question']+'\n\n仅 PubMed 摘要与书目元数据。未核验全文；自动抽取未代替人工医学审核。\n\n'
        content+='## 问题 PICO\n\n'
        for key,field in record['payload'].get('pico',{}).items(): content+=f"- {key}: {field['value'] or 'Not Reported'}\n"
        for row in record['extractions']:
            p=row['payload'];meta=p['publication']
            content+=f"\n## {meta['title']}\n\nPMID: {meta['pmid']} · DOI: {meta.get('doi') or 'Not Reported'}\n\n来源：{meta['url']}\n\n抽取审核：{row['review_status']} · 设计：{p['design']['label']} · 质量：待专业评价（详见完整记录）\n\n"
            for key,field in p['fields'].items(): content+=f"- {FIELDS[key]}: {field['value'] or 'Not Reported'}\n"
            for finding in p['findings']: content+='\n作者摘要陈述（非系统建议）：\n\n> '+finding['statement'].replace('\n','\n> ')+'\n'
        content+='\n## 版本、数值、逐项审核及差异完整记录\n\n```json\n'+json.dumps({k:v for k,v in record.items() if k!='release'},ensure_ascii=False,indent=2)+'\n```\n'
    return Response(content,media_type='application/json' if format=='json' else 'text/markdown; charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="evidence-analysis-{identity}.{"json" if format=="json" else "md"}"'})

@router.post('/extractions/{identity}/review',status_code=201)
def review(request:Request,identity:str,body:ReviewRequest):
    if not body.confirm_human_review: raise ValueError('需要明确人工审核确认')
    row=service(request).extraction(identity)
    return service(request).research.review_revision(row['revision_id'],body.actor,body.decision,body.reason)

@router.post('/extractions/{identity}/appraisals',status_code=201)
def appraisal(request:Request,identity:str,body:AppraisalRequest): return service(request).appraisal(identity,body.model_dump())

@router.post('/analyses/{identity}/statements',status_code=201)
def statement(request:Request,identity:str,body:StatementRequest): return service(request).statement(identity,body.model_dump())
