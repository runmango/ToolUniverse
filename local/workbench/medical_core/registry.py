"""Authoritative entity repository shared across research modules."""
import json
from .db import stable, encoded, now, revision
from .model import ENTITY_TYPES, domain_for, normalize
from .identifiers import identifiers as normalize_identifiers

class EntityRegistry:
    def entity(self, db, kind, name, *, english=None, aliases=(), identifiers=None, properties=None, source=None):
        if kind not in ENTITY_TYPES or not str(name).strip():
            raise ValueError('实体类型或名称无效')
        original = str(name).strip()
        domain = domain_for(original)
        if domain and kind in {'Disease', 'Intervention', 'ClinicalEvent'}:
            kind, name, english = domain['type'], domain['name'], domain['english']
            aliases = [*aliases, *domain['aliases'], original]
        key = normalize(name)
        identifiers = normalize_identifiers(identifiers)
        matches = set()
        for scheme, value in identifiers.items():
            row = db.execute('SELECT entity_id FROM kg_identifiers WHERE scheme=? AND value=?', (scheme, str(value))).fetchone()
            if row:
                matches.add(row[0])
        rows = db.execute('SELECT DISTINCT e.id FROM kg_entities e JOIN kg_aliases a ON a.entity_id=e.id WHERE e.entity_type=? AND a.normalized_name=?', (kind, key)).fetchall()
        if kind not in {'Publication', 'ClinicalTrial', 'Protein'} or not identifiers:
            matches.update(r[0] for r in rows)
        if len(matches) > 1:
            raise ValueError('术语标识冲突，需人工核验后合并')
        anchor = next(iter(identifiers.items()), None)
        identity = next(iter(matches), stable(kind, *anchor) if anchor and kind in {'Publication','ClinicalTrial','Protein'} else stable(kind, key))
        old = db.execute('SELECT * FROM kg_entities WHERE id=?', (identity,)).fetchone()
        if old and old['entity_type'] != kind:
            raise ValueError('标识符对应不同实体类型')
        props = {**(json.loads(old['properties']) if old else {}), **(properties or {})}
        if old and json.loads(old['properties']).get('metadata_pending') is False:
            props['metadata_pending'] = False
        if source or (old and db.execute('SELECT 1 FROM kg_entity_sources WHERE entity_id=? LIMIT 1', (identity,)).fetchone()):
            props.pop('vocabulary_only', None)
        timestamp = now()
        db.execute('''INSERT INTO kg_entities VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            english_name=COALESCE(excluded.english_name,kg_entities.english_name),properties=excluded.properties,updated_at=excluded.updated_at''',
            (identity, kind, name, english, key, encoded(props), timestamp, timestamp))
        for alias in {original, name, english, *aliases} - {None, ''}:
            db.execute('INSERT OR IGNORE INTO kg_aliases VALUES (?,?,?,?)', (identity, alias, normalize(alias), source or 'local_vocabulary'))
        for scheme, value in identifiers.items():
            old_identifier = db.execute('SELECT value FROM kg_identifiers WHERE entity_id=? AND scheme=?', (identity, scheme)).fetchone()
            if old_identifier and old_identifier[0] != str(value):
                raise ValueError('同名实体存在不同标准编号，拒绝自动合并')
            db.execute('INSERT OR IGNORE INTO kg_identifiers VALUES (?,?,?,?)', (scheme, str(value), identity, source or 'local_vocabulary'))
        if source:
            db.execute('INSERT OR IGNORE INTO kg_entity_sources VALUES (?,?)', (identity, source))
        revision(db,"entity",identity,self._entity(db,db.execute("SELECT * FROM kg_entities WHERE id=?",(identity,)).fetchone()))
        return identity

    def _entity(self, db, row):
        item = dict(row)
        item['properties'] = json.loads(item['properties'])
        item['synonyms'] = [r[0] for r in db.execute('SELECT DISTINCT raw_name FROM kg_aliases WHERE entity_id=? ORDER BY raw_name', (item['id'],))]
        item['identifiers'] = [dict(r) for r in db.execute('SELECT scheme,value,source FROM kg_identifiers WHERE entity_id=?', (item['id'],))]
        item['sources'] = [dict(r) for r in db.execute('''SELECT s.id,s.provider,s.source_id,s.source_url,s.source_updated_at,s.fetched_at FROM kg_sources s
            JOIN kg_entity_sources es ON es.source_id=s.id WHERE es.entity_id=? ORDER BY s.fetched_at DESC''', (item['id'],))]
        return item

