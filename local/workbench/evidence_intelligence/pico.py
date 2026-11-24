import re
from .models import PICOProposal
from .grounding import field, ENDPOINTS
from ..medical_core.model import DOMAINS

def rules(question):
    """Conservative source-text fallback; does not fill implicit C or O."""
    result={key:{'value':None,'quote':None} for key in PICOProposal.model_fields}
    for domain in DOMAINS:
        for alias in [domain['name'],domain['english'],*domain['aliases']]:
            match=re.search(r'(?<![a-z])'+re.escape(alias)+r'(?![a-z])',question,re.I)
            if match:
                key='population' if domain['type']=='Disease' else 'intervention' if domain['type']=='Intervention' else 'outcome'
                result[key]={'value':match[0],'quote':match[0]};break
    intervention=re.search(r'(?:接受|使用|应用|给予)(.+?)(?:治疗|的效果|疗效|效果|[？?。]|$)',question)
    if intervention: result['intervention']={'value':intervention[1],'quote':intervention[1]}
    comparison=re.search(r'(?:相比于|相比|对照为|versus\s+|\bvs\.?\s+)(.+?)(?:的效果|效果|[，。？?]|$)',question,re.I)
    if comparison: result['comparison']={'value':comparison[1],'quote':comparison[1]}
    for pattern in ENDPOINTS.values():
        match=re.search(pattern,question,re.I)
        if match: result['outcome']={'value':match[0],'quote':match[0]};break
    return result

def validate(question,proposal):
    data=PICOProposal.model_validate(proposal).model_dump()
    result={key:field(question,value) for key,value in data.items()}
    if (result['outcome']['value'] or '').strip().casefold() in {'效果','疗效','治疗效果','efficacy','effectiveness','effect','effects'}:
        result['outcome']={**field(question,{}),'reason':'仅泛称疗效，未指定具体研究结局','raw_candidate':result['outcome']['value']}
    return result

def search_terms(question,pico):
    terms=[]
    for domain in DOMAINS:
        if any(re.search(r'(?<![a-z])'+re.escape(alias)+r'(?![a-z])',question,re.I) for alias in [domain['name'],domain['english'],*domain['aliases']]):
            terms.append('"'+domain['english']+'"')
    intervention=pico['intervention']['value'] or ''
    words=re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}',intervention)
    terms.extend(w for w in words if w.lower() not in {'and','with','plus','therapy','treatment','patients'})
    return ' AND '.join(dict.fromkeys(terms)) or question
