"""Validation shared by JSONL import and manual editing."""
import copy
import math
import re
from urllib.parse import urlparse

from ..medical_core.model import DOMAINS as DOMAIN_RECORDS
from ..medical_core.contracts import text, entities, provenance, document_record
from ..medical_core.identifiers import document_id
DOMAINS = tuple(d['key'] for d in DOMAIN_RECORDS)
METHODS = ('bm25', 'elasticsearch', 'embedding', 'embedding_reranker', 'hybrid', 'llm_assisted', 'graph')

def query_record(raw, *, attest=False, actor=''):
    if not isinstance(raw, dict):
        raise ValueError('Query 必须是 JSON 对象')
    domain = raw.get('domain')
    if domain not in DOMAINS:
        raise ValueError('domain 必须为 MDS、AML、HSCT、DLI 或 GVHD')
    result = {'query_id': text(raw.get('query_id'), 'query_id', 100),
              'query': text(raw.get('query'), 'query', 3000), 'domain': domain,
              'query_type': text(raw.get('query_type', 'clinical_research'), 'query_type', 100),
              'entities': entities(raw.get('entities', [])), 'relevant_documents': [],
              **provenance(raw, attest=attest, actor=actor)}
    result['review_complete'] = raw.get('review_complete') is True and result['verification_status'] == 'verified'
    seen = set()
    labels = raw.get('relevant_documents', [])
    if not isinstance(labels,list) or len(labels)>2000: raise ValueError('relevant_documents 必须是最多2000项的数组')
    for item in labels:
        if not isinstance(item,dict): raise ValueError('每条相关性标注必须是 JSON 对象')
        identity = document_id(item)
        if identity in seen:
            raise ValueError('同一 Query 的文献标注不能重复')
        seen.add(identity)
        grade = item.get('relevance')
        if type(grade) is not int or grade not in (0, 1, 2, 3):
            raise ValueError('relevance 必须是0、1、2或3整数')
        result['relevant_documents'].append({'document_id': identity, 'relevance': grade,
            **({'pmid': str(item['pmid'])} if item.get('pmid') else {}),
            **provenance(item, attest=attest, actor=actor, parent=raw),
            'note': str(item.get('note', ''))[:2000]})
    if raw.get('medical_gold'):
        medical = raw['medical_gold']
        if not isinstance(medical,dict): raise ValueError('medical_gold 必须是 JSON 对象')
        result['medical_gold'] = {**provenance(medical, attest=attest, actor=actor), 'entities': entities(medical.get('entities', []))}
    return result


def model_record(raw):
    result = {'name': text(raw.get('name'), '模型配置名称'), 'version': text(raw.get('version'), '模型版本'),
              'method': raw.get('method'), 'config': copy.deepcopy(raw.get('config', {}))}
    if result['method'] not in METHODS or not isinstance(result['config'], dict):
        raise ValueError('无效的检索方法或配置')
    def inspect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in {'api_key', 'token', 'password', 'authorization', 'secret'}:
                    raise ValueError('请使用 api_key_env 引用环境变量，不要在模型配置中保存密钥')
                if key in {'api_url', 'url'} and child:
                    parsed = urlparse(str(child))
                    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query:
                        raise ValueError('服务 URL 仅支持 HTTP/HTTPS，不能包含账号、密码或查询参数')
                if key.endswith('_env') and child and not re.fullmatch(r'[A-Z][A-Z0-9_]*', str(child)):
                    raise ValueError('环境变量名称格式无效')
                inspect(child)
        elif isinstance(value, list):
            for child in value: inspect(child)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('模型参数不能为 NaN 或无穷大')
    inspect(result['config'])
    return result
