"""Pure evaluation functions. Retrieval never receives these labels.

Binary relevance defaults to >=2; nDCG uses graded gain 2**grade-1.
Unjudged results count as zero gain with judgment coverage disclosed.
MRR and MAP are truncated at the saved retrieval depth (not unbounded).
"""
import math
from statistics import mean
from ..medical_core.model import domain_for, normalize

KS = (5, 10, 20)
METRIC_VERSION = 'medical-ir-v1'


def human_verified(item):
    return (item.get('verification_status') == 'verified'
            and item.get('annotation_source') == 'human'
            and bool(item.get('annotator', '').strip()))


def gold_labels(query):
    return {x['document_id']: x['relevance'] for x in query.get('relevant_documents', []) if human_verified(x)}


def evaluate_query(query, ranking, *, threshold=2, ks=KS):
    labels = gold_labels(query)
    reviewed = human_verified(query) and query.get('review_complete') is True
    # No labels is not evidence of a no-relevant-document query.
    unique = list(dict.fromkeys(x['document_id'] if isinstance(x, dict) else x for x in ranking))
    positives = {key for key, grade in labels.items() if grade >= threshold}
    ready = bool(positives) or reviewed
    out = {'evaluation_status': 'scored' if ready else 'awaiting_gold',
           'gold_document_count': len(labels), 'relevant_document_count': len(positives),
           'returned_count': len(unique), 'judgments_complete': reviewed,
           'metric_version': METRIC_VERSION, 'binary_relevance_threshold': threshold,
           'no_relevant_documents': ready and not positives,
           'MRR': None, 'MAP': None, 'at_k': {}}
    if ready:
        hits = [i for i, doc in enumerate(unique, 1) if doc in positives]
        out['MRR'] = 1 / hits[0] if hits else 0.0
        out['MAP'] = sum((number + 1) / rank for number, rank in enumerate(hits)) / len(positives) if positives else 0.0
    for k in ks:
        docs = unique[:k]
        hit_count = len(set(docs) & positives)
        dcg = sum((2**labels.get(doc, 0) - 1) / math.log2(rank + 1) for rank, doc in enumerate(docs, 1))
        ideal = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(sorted(labels.values(), reverse=True)[:k], 1))
        out['at_k'][str(k)] = {
            'Precision': hit_count / k if ready else None,
            'Recall': (hit_count / len(positives) if positives else 0.0) if ready else None,
            'NDCG': (dcg / ideal if ideal else 0.0) if ready else None,
            'HitRate': float(bool(hit_count)) if ready else None,
            'judged_fraction': sum(d in labels for d in docs) / len(docs) if docs else 0.0,
            'unjudged_count': sum(d not in labels for d in docs),
        }
    return out


def aggregate(results, top_k):
    values = [x['metrics'] if 'metrics' in x else x for x in results]
    scored = [v for v in values if v['evaluation_status'] == 'scored']
    avg = lambda seq: mean(seq) if seq else None
    return {'metric_version': METRIC_VERSION, 'query_count': len(values), 'scored_query_count': len(scored),
            'unscored_query_count': len(values) - len(scored),
            'complete_judgment_query_count': sum(v['judgments_complete'] for v in scored),
            'no_relevant_query_count': sum(v['no_relevant_documents'] for v in scored),
            'scope': f'MRR/MAP truncated at top_{top_k}; macro mean over scored queries; no-relevant queries count as zero',
            'MRR': avg([v['MRR'] for v in scored]), 'MAP': avg([v['MAP'] for v in scored]),
            'at_k': {str(k): {metric: avg([v['at_k'][str(k)][metric] for v in scored])
                               for metric in ('Precision', 'Recall', 'NDCG', 'HitRate', 'judged_fraction')} for k in KS}}


def entity_key(item):
    if item.get('entity_id'): return str(item['entity_id']).strip().casefold()
    name = item.get('name', '')
    domain = domain_for(name)
    return domain['key'] if domain and item.get('type') in {'Disease','Intervention','ClinicalEvent'} else normalize(name)


def evaluate_entities(query, ranked_documents, ks=KS):
    gold = query.get('medical_gold', {})
    output = {'kind': 'retrieval_entity_coverage', 'at_k': {}}
    for k in ks:
        docs = ranked_documents[:k]
        verified_docs = [d for d in docs if human_verified(d.get('entity_annotation', {}))]
        values = {'document_annotation_coverage': len(verified_docs) / len(docs) if docs else 1.0}
        for kind, name in [('Disease', 'DiseaseEntityRecall'), ('Drug', 'DrugEntityRecall')]:
            expected = {entity_key(e) for e in gold.get('entities', []) if e['type'] == kind}
            observed = {entity_key(e) for d in verified_docs for e in d.get('entities', []) if e['type'] == kind}
            values[name] = (len(expected & observed) / len(expected)
                            if expected and human_verified(gold) and len(verified_docs) == len(docs) else None)
        output['at_k'][str(k)] = values
    return output


def evaluate_assessment(payload):
    # Separate denominators for linking, citation validity, and claim support.
    # Omitted/unreviewed judgments never become zero or automatic truth.
    out = {}
    for group, metric, field in [('trial_matches', 'ClinicalTrialMatchingAccuracy', 'correct'),
                                  ('citations', 'CitationAccuracy', 'exists'),
                                  ('claims', 'EvidenceSupportRate', 'supported')]:
        items = payload.get(group, [])
        judged = [x for x in items if type(x.get(field)) is bool]
        out[metric] = {'value': sum(x[field] for x in judged) / len(judged) if judged else None,
                       'judged': len(judged), 'total': len(items),
                       'coverage': len(judged) / len(items) if items else None,
                       'kind': 'linking' if group == 'trial_matches' else 'generation'}
    return out
