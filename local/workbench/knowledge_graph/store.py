import csv
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import uuid

from .model import DOMAINS, ENTITY_TYPES, RELATIONS, domain_for, normalize

from ..medical_core.db import SQLiteStore, now, encoded, stable, initialize, revision, digest
from ..medical_core.registry import EntityRegistry

class GraphStore(EntityRegistry, SQLiteStore):
    def __init__(self, path):
        self.db_path = Path(path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            initialize(db)
            for domain in DOMAINS:
                self.entity(db, domain['type'], domain['name'], english=domain['english'],
                            aliases=domain['aliases'], properties={'domain': domain['key'], 'vocabulary_only': True})
            # Legacy IDs remain stable; merges require explicit reviewed migrations.

    def source(self, db, provider, accession, url, payload, updated=None):
        raw = encoded(payload)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        identity = stable(provider, accession, digest)
        db.execute('INSERT INTO kg_sources VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET fetched_at=excluded.fetched_at',
                   (identity, provider, accession, url, updated, now(), digest, raw))
        # A successful re-import replaces claims from this source, preserving old snapshots.
        db.execute('UPDATE kg_edges SET active=0 WHERE evidence_source IN (SELECT id FROM kg_sources WHERE provider=? AND source_id=?)', (provider, accession))
        for row in db.execute('SELECT * FROM kg_edges WHERE evidence_source IN (SELECT id FROM kg_sources WHERE provider=? AND source_id=?)',(provider,accession)):
            revision(db,'relation',row['id'],self._edge(row))
        return identity

    def edge(self, db, relation, start, end, source, field, text, *, status='source_asserted', confidence=1.0, qualifiers=None):
        if relation not in RELATIONS or not text or status not in {'source_asserted', 'inferred', 'unverified'}:
            raise ValueError('关系必须具有有效类型、状态和证据文本')
        kinds = {r['id']: r['entity_type'] for r in db.execute('SELECT id,entity_type FROM kg_entities WHERE id IN (?,?)', (start, end))}
        _, allowed_start, allowed_end = RELATIONS[relation]
        if kinds.get(start) not in allowed_start or kinds.get(end) not in allowed_end:
            raise ValueError('关系的实体类型不匹配')
        qualifiers={**(qualifiers or {}),'extraction':{'origin':'rule' if status=='inferred' else 'source',
                    'rule_version':'medical-graph-extraction-v1','model_version':'not_applicable','verification_status':'unverified'}}
        record = db.execute('SELECT provider,source_id FROM kg_sources WHERE id=?', (source,)).fetchone()
        if not record:
            raise ValueError('关系缺少可追溯的来源记录')
        identity = stable(relation, start, end, *record, field)
        timestamp = now()
        evidence_type = (qualifiers or {}).get('evidence_type', 'structured_record' if status == 'source_asserted' else 'rule_inference')
        db.execute('''INSERT INTO kg_edges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
            confidence=excluded.confidence,evidence_source=excluded.evidence_source,evidence_type=excluded.evidence_type,
            verification_status=excluded.verification_status,evidence_text=excluded.evidence_text,
            qualifiers=excluded.qualifiers,updated_at=excluded.updated_at,active=1''',
            (identity, relation, start, end, confidence, source, evidence_type, status, text, field, encoded(qualifiers or {}), timestamp, timestamp))
        revision(db,"relation",identity,self._edge(db.execute("SELECT * FROM kg_edges WHERE id=?",(identity,)).fetchone()))
        return identity

    def get(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM kg_entities WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise KeyError('实体不存在')
            return self._entity(db, row)

    def search(self, query='', types=(), limit=30):
        if any(t not in ENTITY_TYPES for t in types):
            raise ValueError('未知实体分类')
        term = normalize(query)
        sql = '''SELECT DISTINCT e.* FROM kg_entities e JOIN kg_aliases a ON a.entity_id=e.id
                 LEFT JOIN kg_identifiers i ON i.entity_id=e.id
                 WHERE (instr(a.normalized_name,?)>0 OR instr(lower(i.value),?)>0)'''
        params = [term] * 2
        if types:
            sql += ' AND e.entity_type IN (' + ','.join('?' for _ in types) + ')'
            params.extend(types)
        sql += ' ORDER BY CASE WHEN e.normalized_name=? THEN 0 WHEN a.normalized_name=? THEN 1 ELSE 2 END,e.standard_name LIMIT ?'
        params.extend([term, term, limit])
        with self.connect() as db:
            return [self._entity(db, row) for row in db.execute(sql, params)]

    def _review_decisions(self,db):
        return {r['revision_id']:r['decision'] for r in db.execute("SELECT revision_id,decision FROM research_reviews WHERE object_type='relation' ORDER BY created_at,id")}

    def _edge(self, row, decisions=None):
        item = dict(row)
        item.pop('start_type',None);item.pop('end_type',None)
        item['qualifiers'] = json.loads(item['qualifiers'])
        item['label'] = RELATIONS[item['relation_type']][0]
        item['revision_id']=stable('revision','relation',item['id'],digest(item))
        item['human_review_status']=(decisions or {}).get(item['revision_id'],'unreviewed')
        return item

    def evidence(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM kg_edges WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise KeyError('关系不存在')
            item = self._edge(row,self._review_decisions(db))
            source = dict(db.execute('SELECT * FROM kg_sources WHERE id=?', (item['evidence_source'],)).fetchone())
            source['payload'] = json.loads(source['payload'])
            return {**item, 'source_record': source}

    def neighborhood(self, center, depth=1, types=(), include_inferred=True, limit=160):
        self.get(center)
        if depth not in (1, 2) or not 1 <= limit <= 400:
            raise ValueError('仅支持一度、二度展开及最多400个节点')
        if any(t not in ENTITY_TYPES for t in types):
            raise ValueError('未知实体分类')
        visited, frontier, chosen, truncated = {center}, {center}, {}, False
        with self.connect() as db:
            decisions=self._review_decisions(db)
            for _ in range(depth):
                following = set()
                for current in sorted(frontier):
                    rows = db.execute('''SELECT e.*,a.entity_type AS start_type,b.entity_type AS end_type FROM kg_edges e
                        JOIN kg_entities a ON a.id=e.source_entity JOIN kg_entities b ON b.id=e.target_entity
                        WHERE e.active=1 AND (e.source_entity=? OR e.target_entity=?)
                        AND e.verification_status NOT IN ('unverified','rejected') ORDER BY e.confidence DESC,e.id''', (current, current))
                    for row in rows:
                        item=self._edge(row,decisions)
                        if item['human_review_status']=='rejected': continue
                        if not include_inferred and row['verification_status'] != 'source_asserted':
                            continue
                        neighbor = row['target_entity'] if row['source_entity'] == current else row['source_entity']
                        neighbor_type = row['end_type'] if row['source_entity'] == current else row['start_type']
                        if types and neighbor != center and neighbor_type not in types:
                            continue
                        if neighbor not in visited:
                            if len(visited) >= limit:
                                truncated = True
                                continue
                            visited.add(neighbor)
                            following.add(neighbor)
                        chosen[row['id']] = item
                frontier = following
            nodes = [self._entity(db, db.execute('SELECT * FROM kg_entities WHERE id=?', (key,)).fetchone()) for key in sorted(visited)]
        return {'nodes': nodes, 'edges': list(chosen.values()), 'center': center, 'depth': depth, 'truncated': truncated,
                'data_mode': 'live_records' if chosen else 'vocabulary_only', 'generated_at': now()}

    def path(self, start, end, max_hops=5, include_inferred=False):
        self.get(start); self.get(end)
        queue, visited, previous = deque([(start, 0)]), {start}, {}
        truncated = False
        with self.connect() as db:
            decisions=self._review_decisions(db)
            while queue and end not in visited:
                current, distance = queue.popleft()
                if distance >= max_hops:
                    continue
                for row in db.execute('SELECT * FROM kg_edges WHERE active=1 AND (source_entity=? OR target_entity=?) ORDER BY confidence DESC,id', (current, current)):
                    item=self._edge(row,decisions)
                    if item['human_review_status']=='rejected': continue
                    if row['verification_status'] not in ({'source_asserted', 'inferred'} if include_inferred else {'source_asserted'}):
                        continue
                    other = row['target_entity'] if row['source_entity'] == current else row['source_entity']
                    if other in visited:
                        continue
                    if len(visited) >= 5000:
                        truncated = True
                        break
                    visited.add(other); previous[other] = (current, item); queue.append((other, distance + 1))
                if truncated:
                    break
            if end not in visited:
                return {'nodes': [], 'edges': [], 'found': False, 'truncated': truncated, 'max_hops': max_hops}
            keys, edges, cursor = [end], [], end
            while cursor != start:
                cursor, edge = previous[cursor]
                keys.append(cursor); edges.append(edge)
            nodes = [self._entity(db, db.execute('SELECT * FROM kg_entities WHERE id=?', (key,)).fetchone()) for key in reversed(keys)]
            return {'nodes': nodes, 'edges': list(reversed(edges)), 'found': True, 'truncated': False,
                    'max_hops': max_hops, 'traversal': 'undirected_connectivity', 'note': '连接路径不代表因果关系或治疗建议；箭头保留原关系方向。'}

    def stats(self):
        with self.connect() as db:
            return {'entity_types': ENTITY_TYPES, 'relations': {k: v[0] for k,v in RELATIONS.items()},
                    'counts': {r[0]: r[1] for r in db.execute('SELECT entity_type,count(*) FROM kg_entities GROUP BY entity_type')},
                    'edge_count': db.execute('SELECT count(*) FROM kg_edges WHERE active=1').fetchone()[0],
                    'source_count': db.execute('SELECT count(*) FROM kg_sources').fetchone()[0],
                    'inferred_count': db.execute("SELECT count(*) FROM kg_edges WHERE active=1 AND verification_status='inferred'").fetchone()[0],
                    'runs': [{**dict(r), 'report': json.loads(r['report'])} for r in db.execute('SELECT * FROM kg_sync_runs ORDER BY started_at DESC LIMIT 10')],
                    'domains': [{'key': d['key'], 'name': d['name'], 'entity_type': d['type']} for d in DOMAINS]}

    @staticmethod
    def csv_export(graph):
        out = io.StringIO(newline='')
        fields = ['record_type','id','entity_type','standard_name','english_name','identifiers','synonyms','sources','relation_type','source_entity','target_entity','confidence','verification_status','evidence_source','evidence_type','evidence_text','evidence_field','qualifiers','created_at','updated_at']
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for kind, items in [('entity', graph['nodes']), ('relation', graph['edges'])]:
            for item in items:
                row = {'record_type': kind, **item}
                for key, value in row.items():
                    if isinstance(value, (dict, list)):
                        row[key] = encoded(value)
                    elif isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                        row[key] = "'" + value
                writer.writerow(row)
        return '\ufeff' + out.getvalue()
