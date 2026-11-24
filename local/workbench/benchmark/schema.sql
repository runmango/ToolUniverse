PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS benchmark_datasets (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL, description TEXT NOT NULL,
 current_version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_versions (
 id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES benchmark_datasets(id),
 version INTEGER NOT NULL, content_hash TEXT NOT NULL, corpus_hash TEXT NOT NULL,
 payload TEXT NOT NULL, change_note TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(dataset_id, version)
);
CREATE TABLE IF NOT EXISTS benchmark_models (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL, method TEXT NOT NULL,
 config TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_experiments (
 id TEXT PRIMARY KEY, group_id TEXT NOT NULL, dataset_version_id TEXT NOT NULL REFERENCES benchmark_versions(id),
 model_snapshot TEXT NOT NULL, config TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, finished_at TEXT, metrics TEXT, runtime TEXT, error TEXT,
 parent_id TEXT REFERENCES benchmark_experiments(id)
);
CREATE TABLE IF NOT EXISTS benchmark_results (
 experiment_id TEXT NOT NULL REFERENCES benchmark_experiments(id), query_id TEXT NOT NULL,
 ranking TEXT NOT NULL, metrics TEXT NOT NULL, trace TEXT NOT NULL,
 PRIMARY KEY(experiment_id, query_id)
);
CREATE TABLE IF NOT EXISTS benchmark_audit (
 id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES benchmark_datasets(id),
 version_id TEXT NOT NULL REFERENCES benchmark_versions(id), query_id TEXT,
 actor TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmark_negatives (
 id TEXT PRIMARY KEY, dataset_version_id TEXT NOT NULL REFERENCES benchmark_versions(id),
 query_id TEXT NOT NULL, document_id TEXT NOT NULL, method TEXT NOT NULL,
 category TEXT NOT NULL, score REAL, status TEXT NOT NULL DEFAULT 'candidate',
 review TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
 UNIQUE(dataset_version_id, query_id, document_id, method)
);
CREATE TABLE IF NOT EXISTS benchmark_assessments (
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES benchmark_experiments(id), query_id TEXT NOT NULL,
 payload TEXT NOT NULL, metrics TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS benchmark_exp_version ON benchmark_experiments(dataset_version_id,created_at);
CREATE INDEX IF NOT EXISTS benchmark_assessment_query ON benchmark_assessments(experiment_id,query_id,created_at);
