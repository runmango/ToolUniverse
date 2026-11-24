PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO kg_meta VALUES ('schema_version', '1');
CREATE TABLE IF NOT EXISTS kg_entities (
 id TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
 standard_name TEXT NOT NULL, english_name TEXT, normalized_name TEXT NOT NULL,
 properties TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kg_aliases (
 entity_id TEXT NOT NULL REFERENCES kg_entities(id), raw_name TEXT NOT NULL,
 normalized_name TEXT NOT NULL, source TEXT NOT NULL,
 PRIMARY KEY(entity_id, raw_name, source)
);
CREATE INDEX IF NOT EXISTS kg_alias_lookup ON kg_aliases(normalized_name);
CREATE TABLE IF NOT EXISTS kg_identifiers (
 scheme TEXT NOT NULL, value TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES kg_entities(id),
 source TEXT NOT NULL, PRIMARY KEY(scheme, value)
);
CREATE TABLE IF NOT EXISTS kg_sources (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, source_id TEXT NOT NULL, source_url TEXT NOT NULL,
 source_updated_at TEXT, fetched_at TEXT NOT NULL, checksum TEXT NOT NULL,
 payload TEXT NOT NULL, UNIQUE(provider, source_id, checksum)
);
CREATE TABLE IF NOT EXISTS kg_entity_sources (
 entity_id TEXT NOT NULL REFERENCES kg_entities(id), source_id TEXT NOT NULL REFERENCES kg_sources(id),
 PRIMARY KEY(entity_id, source_id)
);
CREATE TABLE IF NOT EXISTS kg_edges (
 id TEXT PRIMARY KEY, relation_type TEXT NOT NULL,
 source_entity TEXT NOT NULL REFERENCES kg_entities(id), target_entity TEXT NOT NULL REFERENCES kg_entities(id),
 confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 evidence_source TEXT NOT NULL REFERENCES kg_sources(id), evidence_type TEXT NOT NULL,
 verification_status TEXT NOT NULL CHECK(verification_status IN ('source_asserted','inferred','unverified','rejected')),
 evidence_text TEXT NOT NULL, evidence_field TEXT NOT NULL, qualifiers TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS kg_edges_source ON kg_edges(source_entity, active);
CREATE INDEX IF NOT EXISTS kg_edges_target ON kg_edges(target_entity, active);
CREATE INDEX IF NOT EXISTS kg_edges_evidence ON kg_edges(evidence_source);
CREATE TABLE IF NOT EXISTS kg_sync_runs (
 id TEXT PRIMARY KEY, query TEXT NOT NULL, status TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, report TEXT NOT NULL DEFAULT '{}'
);
