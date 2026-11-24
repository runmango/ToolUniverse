"""Manual extraction Gold evaluated after analysis; never passed to the LLM."""
import uuid
from ..medical_core.db import now,encoded,revision,review
from .models import FIELDS,DESIGNS

def evaluate(service,body):
    if not body.get('confirm_human_review') or not str(body.get('actor','')).strip(): raise ValueError('评价需要人工 Gold 确认和审核人')
    analysis=service.get(body['analysis_id'])
    if analysis['status'] not in {'completed','partial','empty'}: raise ValueError('请先完成证据分析')
    rows={r['id']:r for r in analysis['extractions']};gold=body.get('labels',[])
    if not isinstance(gold,list) or len(gold)>200: raise ValueError('人工标签最多200项')
    results=[];seen=set()
    for item in gold:
        identity,key=item.get('extraction_id'),item.get('field')
        if identity not in rows or key not in {*FIELDS,'design'} or (identity,key) in seen: raise ValueError('标签对象无效或重复')
        seen.add((identity,key));expected=item.get('expected')
        if expected is not None and not isinstance(expected,str): raise ValueError('expected 必须为原文字符串或 null')
        if key=='design' and expected not in DESIGNS: raise ValueError('设计 Gold 必须是合法类别')
        if not str(item.get('reason','')).strip(): raise ValueError('人工 Gold 需要标注依据')
        record=rows[identity]['payload']
        actual=record['design']['type'] if key=='design' else record['fields'][key]['value']
        results.append({**item,'actual':actual,'correct':actual==expected,'source_id':rows[identity]['source_id'],'revision_id':rows[identity]['revision_id']})
    data={'analysis_id':analysis['id'],'gold':gold,'results':results,'metrics':{'FieldExactMatch':sum(r['correct'] for r in results)/len(results) if results else None,
           'judged_fields':len(results),'available_fields':len(rows)*(len(FIELDS)+1)},'task_version':'evidence-extraction-eval-v1',
          'evaluation_status':'scored' if results else 'awaiting_gold','annotation_source':'human','actor':body['actor'],
          'release_hash':analysis['payload']['release_hash'],'scope':'literal_field_exact_match; not clinical quality or treatment efficacy'}
    key=uuid.uuid4().hex
    with service.research.connect() as db:
        db.execute('INSERT INTO research_evaluations VALUES (?,?,?,?,?,?)',(key,analysis['payload']['release_id'],'evidence_extraction',encoded(data),body['actor'],now()))
        version=revision(db,'research_evaluation',key,data);review(db,'research_evaluation',key,version,body['actor'],'recorded','人工证据抽取 Gold 评价')
    return {'id':key,**data}
