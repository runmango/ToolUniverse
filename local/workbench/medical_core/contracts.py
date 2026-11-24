"""Shared entity references and immutable publication projections."""
import copy
from .model import ENTITY_TYPES
from .identifiers import document_id, identifiers, identifier
from .provenance import generation

def text(value, name, maximum=200, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{name} 必须是非空文本，最多{maximum}字符')
    return value.strip()


def entities(items):
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError('entities 必须是最多100项的数组')
    result = []
    for item in items:
        if not isinstance(item, dict) or item.get('type') not in ENTITY_TYPES:
            raise ValueError('实体 type 无效')
        result.append({'name': text(item.get('name'), '实体名称'), 'type': item['type'],
                       **({'entity_id': text(item['entity_id'], 'entity_id')} if item.get('entity_id') else {})})
    return result


def provenance(record, *, attest=False, actor='', parent=None):
    parent = parent or {}
    source = text(record.get('annotation_source', parent.get('annotation_source', 'imported')), 'annotation_source', 40).casefold()
    status = text(record.get('verification_status', parent.get('verification_status', 'candidate')), 'verification_status', 40).casefold()
    if source not in {'human', 'ai', 'model', 'rule', 'synthetic', 'imported'}:
        raise ValueError('annotation_source 必须为 human、ai、model、rule、synthetic 或 imported')
    if status not in {'verified', 'candidate', 'unverified', 'pending'}:
        raise ValueError('verification_status 无效')
    # A string saying "verified" is not a human review action. AI stays a candidate.
    human = attest and source not in {'ai', 'model', 'rule', 'synthetic'} and status == 'verified'
    return {'annotation_source': 'human' if human else source,
            'verification_status': 'verified' if human else 'candidate',
            'annotator': text(actor, '审核人', 120) if human else str(record.get('annotator', parent.get('annotator', '')))[:120], **generation(record,parent)}


def document_record(raw, *, attest=False, actor=''):
    if not isinstance(raw, dict):
        raise ValueError('Document 必须是 JSON 对象')
    if any(not isinstance(raw.get(key,{}),dict) for key in ('source','identifiers','entity_annotation')):
        raise ValueError('source、identifiers、entity_annotation 必须是 JSON 对象')
    result = {'document_id': document_id(raw), 'title': text(raw.get('title'), 'title', 5000),
              'text': text(raw.get('text', raw.get('abstract', '')), '文献文本', 100000, required=False),
              'entities': entities(raw.get('entities', [])), 'source': copy.deepcopy(raw.get('source', {})),
              'identifiers': identifiers(raw.get('identifiers', {})),
              'entity_annotation': provenance(raw.get('entity_annotation', {}), attest=attest, actor=actor)}
    if raw.get('pmid'):
        result['identifiers']['PMID'] = identifier('PMID',raw['pmid'])[1]
    if raw.get('nct_id'):
        result['identifiers']['NCT'] = identifier('NCT',raw['nct_id'])[1]
    if raw.get('doi'):
        result['identifiers']['DOI'] = identifier('DOI',raw['doi'])[1]
    result['identifier_inputs']=copy.deepcopy(raw.get('identifier_inputs') or {key:raw[key] for key in ('identifiers','pmid','doi','nct_id') if key in raw})
    return result


