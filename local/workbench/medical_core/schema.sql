CREATE TABLE IF NOT EXISTS research_migrations (
 version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, manifest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_revisions (
 id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
 content_hash TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(object_type,object_id,content_hash)
);
CREATE TABLE IF NOT EXISTS research_releases (
 id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, payload TEXT NOT NULL,
 created_at TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_reviews (
 id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
 revision_id TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT NOT NULL,
 reason TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS research_review_object ON research_reviews(object_type,object_id,created_at);
CREATE TABLE IF NOT EXISTS research_bindings (
 dataset_version_id TEXT NOT NULL REFERENCES benchmark_versions(id), document_id TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES kg_entities(id),
 entity_revision_id TEXT NOT NULL REFERENCES research_revisions(id),
 PRIMARY KEY(dataset_version_id,document_id)
);
CREATE TABLE IF NOT EXISTS research_source_fetches (
 id TEXT PRIMARY KEY, adapter_version TEXT NOT NULL, tool_name TEXT NOT NULL,
 arguments TEXT NOT NULL, response_hash TEXT, response TEXT, status TEXT NOT NULL,
 error TEXT, fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_chains (
 id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES research_releases(id),
 query TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_evaluations (
 id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES research_releases(id),
 task_type TEXT NOT NULL, payload TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS revisions_no_update BEFORE UPDATE ON research_revisions BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS revisions_no_delete BEFORE DELETE ON research_revisions BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS releases_no_update BEFORE UPDATE ON research_releases BEGIN SELECT RAISE(ABORT,'immutable release'); END;
CREATE TRIGGER IF NOT EXISTS releases_no_delete BEFORE DELETE ON research_releases BEGIN SELECT RAISE(ABORT,'immutable release'); END;
CREATE TRIGGER IF NOT EXISTS reviews_no_update BEFORE UPDATE ON research_reviews BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS reviews_no_delete BEFORE DELETE ON research_reviews BEGIN SELECT RAISE(ABORT,'append-only review'); END;
CREATE TRIGGER IF NOT EXISTS chains_no_update BEFORE UPDATE ON evidence_chains BEGIN SELECT RAISE(ABORT,'immutable evidence chain'); END;
CREATE TRIGGER IF NOT EXISTS chains_no_delete BEFORE DELETE ON evidence_chains BEGIN SELECT RAISE(ABORT,'immutable evidence chain'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_update BEFORE UPDATE ON research_evaluations BEGIN SELECT RAISE(ABORT,'immutable evaluation'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_delete BEFORE DELETE ON research_evaluations BEGIN SELECT RAISE(ABORT,'immutable evaluation'); END;
