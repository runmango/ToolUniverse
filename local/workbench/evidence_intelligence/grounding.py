"""Exact source spans and conservative numerical parsing, separate from model prompts."""
import re
import math
from .models import FIELDS, DESIGNS, ExtractionProposal
from ..medical_core.db import digest

NUMBER = r'(?:\d+(?:[.·]\d+)?|[.·]\d+)(?:[eE][+-]?\d+)?'
CI_PREFIX = r'95\s*%\s*(?:CI|confidence interval)(?:\s*\[CI\])?'
def numeric(value):
    result=float(value.replace('·','.'))
    if not math.isfinite(result): raise ValueError('来源数值超出有限数值范围，必须人工核验')
    return result
ENDPOINTS = {
    'OS':r'overall survival|总生存(?:期|率)?|\bOS\b',
    'PFS':r'progression.free survival|无进展生存|\bPFS\b',
    'EFS':r'event.free survival|无事件生存|\bEFS\b',
    'CR':r'complete (?:remission|response)|完全缓解|\bCR\b',
    'ORR':r'overall response rate|objective response rate|总缓解率|\bORR\b',
    'TI':r'transfusion independence|transfusion.independen\w*|输血独立|脱离输血|RBC.TI',
}

def locate(text, quote):
    if not isinstance(quote,str) or not quote.strip() or quote not in text:
        raise ValueError('证据引文必须逐字存在于提供的原文中')
    start=text.index(quote)
    return {'quote':quote,'start':start,'end':start+len(quote),'offset_unit':'unicode_codepoints'}

def field(text, proposal):
    value,quote=proposal.get('value'),proposal.get('quote')
    if value is None and quote is None:
        return {'value':None,'status':'not_reported','display':'Not Reported','reason':'提供文本中未提取到；仍需人工核验','span':None}
    if not isinstance(value,str) or not value.strip() or value not in (quote or ''):
        raise ValueError('字段值必须是引文的原始子串，不能改写数值或补充条件')
    return {'value':value,'status':'reported','span':locate(text,quote),'verification_status':'unverified'}

def sentences(text):
    # Keep decimals and abbreviations such as "vs." inside their evidence context.
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[A-Z])|\n',text) if s.strip()]

def endpoints(text):
    return [key for key,pattern in ENDPOINTS.items() if re.search(pattern,text,re.I)]

def numbers(quote):
    """Return source literals, never calculated sample totals, ratios or p values."""
    ratios=[]
    pattern=rf'\b((?-i:HR|OR|RR)|hazard ratio|odds ratio|risk ratio|relative risk)\s*(?:[=:,]|of)?\s*({NUMBER})'
    found=list(re.finditer(pattern,quote,re.I))
    for index,match in enumerate(found):
        kind={'HAZARD RATIO':'HR','ODDS RATIO':'OR','RISK RATIO':'RR','RELATIVE RISK':'RR'}.get(match[1].upper(),match[1].upper())
        value=numeric(match[2]);tail=quote[match.end():found[index+1].start() if index+1<len(found) else len(quote)]
        tail=re.split(r'(?<=[.!?])\s+(?=[A-Z])',tail)[0]
        ci=re.search(rf'{CI_PREFIX}\s*[:=,\[(]*\s*({NUMBER})\s*(?:[-–—−]|to|,)\s*({NUMBER})',tail,re.I)
        item={'metric':kind,'value':value,'literal':match[0],'ci95':None,'validation_issues':[]}
        if value<=0: item['validation_issues'].append('nonpositive_ratio')
        if ci:
            low,high=numeric(ci[1]),numeric(ci[2]);item['ci95']={'lower':low,'upper':high,'literal':ci[0]}
            if not 0<low<=value<=high: item['validation_issues'].append('inconsistent_confidence_interval')
        ratios.append(item)
    pvalues=[{'operator':m[1],'value':numeric(m[2]),'literal':m[0],'valid':0<=numeric(m[2])<=1} for m in re.finditer(rf'\bp(?:\s*[- ]?value)?\s*([<>=≤≥])\s*({NUMBER})',quote,re.I)]
    intervals=[{'lower':numeric(m[1]),'upper':numeric(m[2]) if m[2].upper()!='NR' else None,'upper_literal':m[2],'literal':m[0]} for m in re.finditer(rf'{CI_PREFIX}\s*[:=,\[(]*\s*({NUMBER})\s*(?:[-–—−]|to|,)\s*({NUMBER}|NR)',quote,re.I)]
    counts=[{'value':int(m[1].replace(',','')),'literal':m[0],'scope':'source_literal_not_total'} for m in re.finditer(r'\bn\s*=\s*(\d[\d,]*)(?![\d,]|\.\d)',quote,re.I)]
    return {'ratios':ratios,'p_values':pvalues,'sample_counts':counts,'confidence_intervals':intervals,
            'ci_mentions':[s for s in sentences(quote) if re.search(CI_PREFIX,s,re.I)],
            'numeric_scope':'literal_source_values; interval/endpoint/arm association requires review'}

def direction(quote, proposed='unclear'):
    negative=bool(re.search(r'no\s+(?:statistically\s+)?significant|not\s+(?:statistically\s+)?significant|did not\s+(?:significantly\s+)?(?:improve|prolong)|failed to\s+(?:improve|show)|未(?:观察到|显示).*?(?:获益|改善)|无显著',quote,re.I))
    if negative: return 'no_significant_benefit'
    if re.search(r'significantly\s+(?:improv|greater|higher|longer|prolong)|significant\s+(?:improvement|benefit)|显著(?:改善|提高|延长)',quote,re.I): return 'benefit'
    if re.search(r'significantly\s+(?:worse|shorter)|显著(?:恶化|缩短)',quote,re.I): return 'harm'
    # Semantic candidate is kept separately; no unsupported benefit badge is displayed.
    return 'unclear'

def validate_extraction(raw, abstract, title='', publication_types=()):
    proposal=ExtractionProposal.model_validate(raw).model_dump()
    if set(proposal['fields'])-set(FIELDS): raise ValueError('未知证据字段')
    fields={key:field(abstract,proposal['fields'].get(key,{})) for key in FIELDS}
    for key,label in [('primary_outcomes','primary'),('secondary_outcomes','secondary')]:
        if fields[key]['value'] and not re.search(r'\b'+label+r'\b|'+('主要' if label=='primary' else '次要'),fields[key]['span']['quote'],re.I):
            raise ValueError('未注明主要/次要的结局不能升级为该级别终点')
    design=proposal['design']
    if design not in DESIGNS: raise ValueError('未知研究设计')
    design_span=None
    if design!='not_reported':
        proof=proposal['design_quote']
        if proof in publication_types: design_span={'quote':proof,'field':'publication_types','source_scope':'metadata'}
        else: design_span={**locate(title+'\n'+abstract,proof),'field':'title_and_abstract'}
        if design=='randomized_controlled_trial' and proof not in publication_types:
            context=(title+'\n'+abstract)[max(0,design_span['start']-5):design_span['end']]
            if not re.search(r'randomi[sz]',proof,re.I) or re.search(r'non[- ]?random|not random',context,re.I):
                raise ValueError('研究设计引文不能支持随机试验标签')
    findings=[]
    for item in proposal['findings']:
        span=locate(abstract,item['quote'])
        if re.search(r'will (?:assess|evaluate|measure|compare)|aims? to (?:assess|evaluate)|ongoing trial',item['quote'],re.I):
            raise ValueError('计划或进行中的试验不能抽取为已观察到的效果')
        if item['outcome'] not in item['quote'] and item['outcome'] not in endpoints(item['quote']):
            raise ValueError('结局名称没有出现在同一证据片段中')
        numeric=numbers(item['quote']);resolved=direction(item['quote'],item['direction'])
        # A statistically nonsignificant estimate cannot become a benefit assertion.
        uncertain=any(p['valid'] and p['operator'] in ('=','>','≥') and p['value']>=.05 for p in numeric['p_values'])
        uncertain|=any(r['ci95'] and r['ci95']['lower']<=1<=r['ci95']['upper'] for r in numeric['ratios'])
        if uncertain and resolved=='benefit': resolved='unclear'
        findings.append({'id':digest({'quote':item['quote'],'outcome':item['outcome']})[:24],
                         'outcome':item['outcome'],'endpoint_keys':endpoints(item['outcome']),
                         'statement':item['quote'],'span':span,'direction':resolved,
                         'proposed_direction':item['direction'],'numbers':numeric,
                         'verification_status':'unverified','interpretation':'作者原文陈述；未升格为系统诊疗建议'})
    return {'fields':fields,'design':{'type':design,'label':DESIGNS[design],'span':design_span,'verification_status':'unverified'},
            'findings':findings,'numbers':numbers(abstract),'source_scope':'abstract',
            'full_text_verified':False,'quality_status':'awaiting_appraisal'}
