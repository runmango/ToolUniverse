"""One normalization boundary. Keep original values in source snapshots."""
import re
from urllib.parse import unquote

def identifier(scheme, value):
    aliases={'pmid':'PMID','doi':'DOI','nct':'NCT','nct_id':'NCT','mesh':'MeSH','chembl':'ChEMBL','uniprot':'UniProt','ncbigene':'NCBIGene'}
    scheme=aliases.get(str(scheme).strip().lower(),str(scheme).strip())
    value=str(value).strip()
    if scheme=='PMID':
        value=re.sub(r'^PMID\s*:\s*','',value,flags=re.I)
        if not re.fullmatch(r'[0-9]+',value) or int(value)<=0: raise ValueError('PMID 必须为正整数编号')
        value=str(int(value))
    elif scheme=='NCT':
        value=re.sub(r'^NCT:(?=NCT)','',value,flags=re.I).upper()
        if not re.fullmatch(r'NCT[0-9]{8}',value): raise ValueError('NCT ID 格式不正确')
    elif scheme=='DOI':
        value=unquote(re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)','',value,flags=re.I)).lower()
        if not re.fullmatch(r'10\.[0-9]{4,9}/\S+',value): raise ValueError('DOI 格式不正确')
    if not scheme or not value: raise ValueError('标识体系和值不能为空')
    return scheme,value

def identifiers(values):
    output={}
    for scheme,value in (values or {}).items():
        scheme,value=identifier(scheme,value)
        if scheme in output and output[scheme]!=value: raise ValueError('同一标识体系出现冲突值')
        output[scheme]=value
    return output

def document_id(record):
    explicit=record.get('document_id')
    values=identifiers(record.get('identifiers',{}))
    for key,scheme in [('pmid','PMID'),('nct_id','NCT'),('doi','DOI')]:
        if record.get(key):
            _,value=identifier(scheme,record[key])
            if scheme in values and values[scheme]!=value: raise ValueError('文献标识符冲突')
            values[scheme]=value
    canonical=None
    if explicit:
        if not isinstance(explicit,str) or not explicit.strip() or len(explicit)>160: raise ValueError('document_id 格式无效')
        explicit=explicit.strip()
        if explicit.upper().startswith(('PMID:','DOI:')):
            scheme,value=identifier(*explicit.split(':',1));canonical=scheme+':'+value
        elif re.match(r'^NCT(?:\d|:)',explicit,re.I):
            scheme,value=identifier('NCT',explicit);canonical=value
        if canonical:
            if scheme in values and values[scheme]!=value: raise ValueError('document_id 与原始编号不一致')
            return canonical
        return explicit  # Explicit internal IDs remain compatible; external IDs bind separately.
    if 'PMID' in values: return 'PMID:'+values['PMID']
    if 'NCT' in values: return values['NCT']
    if 'DOI' in values: return 'DOI:'+values['DOI']
    raise ValueError('文献需要 document_id、PMID、DOI 或 NCT ID')
