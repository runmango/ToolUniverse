CREATE TABLE IF NOT EXISTS ei_analyses (
 id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL,
 config TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS ei_extractions (
 id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL REFERENCES ei_analyses(id),
 entity_id TEXT NOT NULL REFERENCES kg_entities(id), source_id TEXT NOT NULL REFERENCES kg_sources(id),
 payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ei_appraisals (
 id TEXT PRIMARY KEY, extraction_id TEXT NOT NULL REFERENCES ei_extractions(id),
 payload TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ei_statements (
 id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL REFERENCES ei_analyses(id),
 payload TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ei_extractions_immutable_update BEFORE UPDATE ON ei_extractions BEGIN SELECT RAISE(ABORT,'immutable extraction'); END;
CREATE TRIGGER IF NOT EXISTS ei_extractions_immutable_delete BEFORE DELETE ON ei_extractions BEGIN SELECT RAISE(ABORT,'immutable extraction'); END;
CREATE TRIGGER IF NOT EXISTS ei_appraisals_immutable_update BEFORE UPDATE ON ei_appraisals BEGIN SELECT RAISE(ABORT,'append-only appraisal'); END;
CREATE TRIGGER IF NOT EXISTS ei_appraisals_immutable_delete BEFORE DELETE ON ei_appraisals BEGIN SELECT RAISE(ABORT,'append-only appraisal'); END;
CREATE TRIGGER IF NOT EXISTS ei_statements_immutable_update BEFORE UPDATE ON ei_statements BEGIN SELECT RAISE(ABORT,'immutable statement'); END;
CREATE TRIGGER IF NOT EXISTS ei_statements_immutable_delete BEFORE DELETE ON ei_statements BEGIN SELECT RAISE(ABORT,'immutable statement'); END;
