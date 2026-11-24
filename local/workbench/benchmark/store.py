import copy
import hashlib
import json
from pathlib import Path
import threading
import uuid

from ..medical_core.db import SQLiteStore, encoded, now, initialize, review, revision
from ..medical_core.provenance import generation
from ..medical_core.service import bind_documents
from .metrics import gold_labels, human_verified
from .spec import DOMAINS, document_record, model_record, query_record, text


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


class BenchmarkStore(SQLiteStore):

    def __init__(self, path):
        self.db_path = Path(path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            initialize(db)
            db.execute("UPDATE benchmark_experiments SET status='interrupted',finished_at=?,error='服务重启，任务已中断' WHERE status IN ('queued','running')", (now(),))
        seeds = json.loads(Path(__file__).with_name('starter.json').read_text(encoding='utf-8'))
        existing = {x['name'] for x in self.datasets()}
        for item in seeds['datasets']:
            if item['name'] not in existing:
                self.create_dataset(item)
        if not self.models():
            for item in seeds['models']:
                self.add_model(item)

    def _save(self, db, dataset_id, payload, note, actor='system', query_id=None):
        json.dumps(payload,allow_nan=False)
        row = db.execute('SELECT * FROM benchmark_datasets WHERE id=?', (dataset_id,)).fetchone()
        if not row: raise KeyError('数据集不存在')
        if len(payload['queries']) > 500 or len(payload['documents']) > 2000:
            raise ValueError('第一阶段每个数据集最多500个 Query 和2000篇文献')
        payload['queries'].sort(key=lambda x: x['query_id'])
        payload['documents'].sort(key=lambda x: x['document_id'])
        corpus = [{k: d[k] for k in ('document_id', 'title', 'text')} for d in payload['documents']]
        identity, number = uuid.uuid4().hex, row['current_version'] + 1
        db.execute('INSERT INTO benchmark_versions VALUES (?,?,?,?,?,?,?,?)',
                   (identity, dataset_id, number, digest(payload), digest(corpus), encoded(payload), note, now()))
        db.execute('UPDATE benchmark_datasets SET current_version=? WHERE id=?', (number, dataset_id))
        db.execute('INSERT INTO benchmark_audit VALUES (?,?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex, dataset_id, identity, query_id, actor, note, encoded({'content_hash': digest(payload)}), now()))
        review(db,'benchmark_version',dataset_id,identity,actor,'recorded',note,{'content_hash':digest(payload)})
        bind_documents(db,identity,payload)
        return identity

    def create_dataset(self, record):
        name = text(record.get('name'), '数据集名称')
        domain = record.get('domain', 'MDS')
        if domain not in (*DOMAINS, 'MIXED'): raise ValueError('数据集领域无效')
        queries = [query_record(q) for q in record.get('queries', [])]
        if len({q['query_id'] for q in queries}) != len(queries): raise ValueError('query_id 重复')
        identity = uuid.uuid4().hex
        with self.lock, self.connect() as db:
            db.execute('INSERT INTO benchmark_datasets VALUES (?,?,?,?,0,?)', (identity, name, domain, str(record.get('description', ''))[:2000], now()))
            self._save(db, identity, {'queries': queries, 'documents': []}, '创建数据集')
        return self.dataset(identity)

    def datasets(self):
        with self.connect() as db:
            rows = db.execute('SELECT d.*,v.id AS version_id,v.content_hash,v.corpus_hash,v.payload FROM benchmark_datasets d JOIN benchmark_versions v ON v.dataset_id=d.id AND v.version=d.current_version ORDER BY d.created_at').fetchall()
            output = []
            for row in rows:
                item = dict(row); payload = json.loads(item.pop('payload'))
                item.update(query_count=len(payload['queries']), document_count=len(payload['documents']),
                            annotated_query_count=sum(bool(gold_labels(q)) or (human_verified(q) and q.get('review_complete')) for q in payload['queries']))
                output.append(item)
            return output

    def dataset(self, identity, version=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM benchmark_datasets WHERE id=?', (identity,)).fetchone()
            if not row: raise KeyError('数据集不存在')
            result = dict(row)
            snapshot = db.execute('SELECT * FROM benchmark_versions WHERE dataset_id=? AND version=?', (identity, version or row['current_version'])).fetchone()
            if not snapshot: raise KeyError('数据集版本不存在')
            result['snapshot'] = {**dict(snapshot), 'payload': json.loads(snapshot['payload'])}
            # Sidecar metadata keeps historical snapshot bytes and hashes unchanged.
            result['query_provenance']={q['query_id']:generation(q) for q in result['snapshot']['payload']['queries']}
            result['versions'] = [dict(v) for v in db.execute('SELECT id,version,content_hash,corpus_hash,change_note,created_at FROM benchmark_versions WHERE dataset_id=? ORDER BY version DESC', (identity,))]
            return result

    def snapshot(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM benchmark_versions WHERE id=?', (identity,)).fetchone()
            if not row: raise KeyError('数据集版本不存在')
            return {**dict(row), 'payload': json.loads(row['payload'])}

    def mutate(self, identity, action, note, actor='system', query_id=None):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT v.payload FROM benchmark_datasets d JOIN benchmark_versions v ON v.dataset_id=d.id AND v.version=d.current_version WHERE d.id=?', (identity,)).fetchone()
            if not row: raise KeyError('数据集不存在')
            payload = json.loads(row['payload'])
            original = {d['document_id']: (d['title'],d['text']) for d in payload['documents']}
            action(payload)
            current = {d['document_id']: (d['title'],d['text']) for d in payload['documents']}
            if original != current:
                changed = {key for key in original if original[key] != current.get(key)}
                for query in payload['queries']:
                    query['review_complete'] = False
                    for label in query['relevant_documents']:
                        if label['document_id'] in changed:
                            label['verification_status'] = 'candidate'
                            label['note'] = '文献文本改变，需重新人工审核'
            for query in payload['queries']:
                if query.get('review_complete') and set(gold_labels(query)) != set(current):
                    raise ValueError('完整审核要求所有候选文献都有人工等级，不能把未标注当作不相关')
            self._save(db, identity, payload, note, actor, query_id)
        return self.dataset(identity)

    def import_jsonl(self, identity, content, kind, *, attest=False, actor=''):
        if kind not in {'queries', 'documents'}: raise ValueError('导入类型仅支持 queries 或 documents')
        if len(content.encode('utf-8')) > 20_000_000: raise ValueError('JSONL 文件最多20MB')
        records, seen = [], set()
        key = 'query_id' if kind == 'queries' else 'document_id'
        parser = query_record if kind == 'queries' else document_record
        for line_number, line in enumerate(content.lstrip('\ufeff').splitlines(), 1):
            if not line.strip(): continue
            try:
                item = parser(json.loads(line), attest=attest, actor=actor)
                if item[key] in seen: raise ValueError('本次导入中 ID 重复')
                seen.add(item[key]); records.append(item)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError(f'第{line_number}行：{exc}') from None
        if not records: raise ValueError('JSONL 中没有有效记录')
        def merge(payload):
            existing = {x[key]: x for x in payload[kind]}
            for item in records:
                previous = existing.get(item[key])
                # Do not silently downgrade or overwrite reviewed data by importing candidates.
                if previous and kind == 'queries' and (gold_labels(previous) or human_verified(previous)):
                    raise ValueError(f'{item[key]} 已有人工标注，请通过人工维护接口编辑')
                if previous and kind == 'documents' and human_verified(previous.get('entity_annotation', {})):
                    raise ValueError(f'{item[key]} 已有实体标注，请先单独维护该文献')
                existing[item[key]] = item
            payload[kind] = list(existing.values())
        return self.mutate(identity, merge, f'导入 {len(records)} 条{kind}', actor or 'import')

    def save_query(self, identity, raw, actor):
        actor = text(actor, '审核人', 120)
        item = query_record(raw, attest=True, actor=actor)
        def change(payload):
            if item['review_complete'] and set(gold_labels(item)) != {d['document_id'] for d in payload['documents']}:
                raise ValueError('完整审核要求对固定语料中的每篇文献都给出人工相关性等级')
            old = next((q for q in payload['queries'] if q['query_id'] == item['query_id']), None)
            if old and old['query'] != item['query'] and gold_labels(old):
                # Changed question invalidates every old label; review must happen separately.
                if any(x['verification_status'] == 'verified' for x in item['relevant_documents']):
                    raise ValueError('修改已有 Gold 的问题文本时，先将标注改为候选，再重新人工审核')
            if old and old['query'] != item['query'] and human_verified(item.get('medical_gold', {})):
                raise ValueError('修改问题文本时，先将医学实体 Gold 改为候选，再重新人工审核')
            payload['queries'] = [q for q in payload['queries'] if q['query_id'] != item['query_id']] + [item]
        return self.mutate(identity, change, '人工维护 Query 和 Gold', actor, item['query_id'])

    def save_document(self, identity, raw, actor):
        actor = text(actor, '审核人', 120)
        item = document_record(raw, attest=True, actor=actor)
        def change(payload):
            payload['documents'] = [d for d in payload['documents'] if d['document_id'] != item['document_id']] + [item]
        return self.mutate(identity, change, '人工维护文献和实体标注', actor)

    def import_graph(self, identity, graph):
        docs = []
        for node in graph.search('', types=('Publication',), limit=2000):
            props = node['properties']
            if props.get('metadata_pending') or not props.get('pmid') or not props.get('title'): continue
            docs.append(document_record({'pmid': str(props['pmid']), 'title': props['title'], 'abstract': props.get('abstract') or '',
                         'identifiers': {x['scheme']: x['value'] for x in node['identifiers']},
                         'source': {'provider': 'PubMed', 'url': 'https://pubmed.ncbi.nlm.nih.gov/' + str(props['pmid']) + '/',
                                    'graph_entity_id': node['id'], 'records': node['sources']}}))
        if not docs: raise ValueError('知识图谱中没有可导入的 PubMed 详情，请先同步真实文献')
        def merge(payload):
            current = {d['document_id']: d for d in payload['documents']}
            # Keep the selected benchmark corpus immutable until an explicit import.
            for d in docs: current.setdefault(d['document_id'], d)
            payload['documents'] = list(current.values())
        return self.mutate(identity, merge, f'从图谱导入 {len(docs)} 篇真实 PubMed 文献')

    def models(self):
        with self.connect() as db:
            return [{**dict(r), 'config': json.loads(r['config'])} for r in db.execute('SELECT * FROM benchmark_models ORDER BY created_at')]

    def add_model(self, raw):
        item = model_record(raw); identity = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO benchmark_models VALUES (?,?,?,?,?,?)', (identity, item['name'], item['version'], item['method'], encoded(item['config']), now()))
        return next(m for m in self.models() if m['id'] == identity)

    def experiments(self, dataset_id=None):
        with self.connect() as db:
            sql = 'SELECT e.*,v.dataset_id,v.version AS dataset_version,v.content_hash,v.corpus_hash FROM benchmark_experiments e JOIN benchmark_versions v ON v.id=e.dataset_version_id'
            rows = db.execute(sql + (' WHERE v.dataset_id=?' if dataset_id else '') + ' ORDER BY e.created_at DESC LIMIT 200', (dataset_id,) if dataset_id else ()).fetchall()
            return [self._experiment(row) for row in rows]

    @staticmethod
    def _experiment(row):
        item = dict(row)
        for key in ('model_snapshot', 'config', 'metrics', 'runtime'):
            item[key] = json.loads(item[key]) if item.get(key) else None
        item.update(model_name=item['model_snapshot']['name'], model_version=item['model_snapshot']['version'],
                    retrieval_method=item['model_snapshot']['method'], top_k=item['config']['top_k'],
                    embedding_dimension=(item['runtime'] or {}).get('embedding_dimension'),
                    reranker_config=item['model_snapshot']['config'].get('reranker'), experiment_time=item['created_at'])
        return item

    def experiment(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT e.*,v.dataset_id,v.version AS dataset_version,v.content_hash,v.corpus_hash FROM benchmark_experiments e JOIN benchmark_versions v ON v.id=e.dataset_version_id WHERE e.id=?', (identity,)).fetchone()
            if not row: raise KeyError('实验不存在')
            item = self._experiment(row)
            item['results'] = [{**dict(r), **{k: json.loads(r[k]) for k in ('ranking', 'metrics', 'trace')}} for r in db.execute('SELECT * FROM benchmark_results WHERE experiment_id=? ORDER BY query_id', (identity,))]
            item['assessments'] = [{**dict(r), 'payload': json.loads(r['payload']), 'metrics': json.loads(r['metrics'])} for r in db.execute('SELECT * FROM benchmark_assessments WHERE experiment_id=? ORDER BY created_at DESC', (identity,))]
            return item

    def audit(self, identity):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM benchmark_audit WHERE dataset_id=? ORDER BY created_at DESC', (identity,))]
