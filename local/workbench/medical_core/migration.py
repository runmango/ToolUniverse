"""Copy legacy benchmark tables once, preserving exact rows, IDs and hashes."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
from .db import initialize, now, encoded, digest

TABLES=('benchmark_datasets','benchmark_versions','benchmark_models','benchmark_experiments','benchmark_results','benchmark_audit','benchmark_negatives','benchmark_assessments')

def rows(db,table): return sorted([list(r) for r in db.execute('SELECT * FROM '+table)],key=lambda r:encoded(r))

def migrate(runtime):
    runtime=Path(runtime);target=runtime/'knowledge-graph.sqlite';source=runtime/'benchmark.sqlite'
    if not target.exists(): return {'status':'new_install'}
    with closing(sqlite3.connect(target)) as db, db:
        present=db.execute("SELECT 1 FROM sqlite_master WHERE name='research_migrations'").fetchone()
        if present and db.execute("SELECT 1 FROM research_migrations WHERE version='unified-research-v1'").fetchone(): return {'status':'already_applied'}
    backups=runtime/'backups'/now().replace(':','-');backups.mkdir(parents=True,exist_ok=True)
    for location in (target,source,runtime/'conversations.sqlite'):
        if location.exists():
            with closing(sqlite3.connect(location.as_uri()+'?mode=ro',uri=True)) as original,closing(sqlite3.connect(backups/location.name)) as backup: original.backup(backup)
    manifest={'backups':str(backups),'tables':{}}
    with closing(sqlite3.connect(target)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        initialize(db)
        db.executescript((Path(__file__).parents[1]/'benchmark/schema.sql').read_text(encoding='utf-8'))
        db.execute('BEGIN IMMEDIATE');db.execute('PRAGMA defer_foreign_keys=ON')
        if source.exists():
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as legacy:
                for table in TABLES:
                    original=rows(legacy,table)
                    existing=rows(db,table)
                    if existing and existing!=original: raise ValueError('目标库已有不同数据，拒绝覆盖 '+table)
                    if not existing and original:
                        db.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in original[0])+')',original)
                    if rows(db,table)!=original: raise ValueError('迁移校验失败 '+table)
                    manifest['tables'][table]={'rows':len(original),'hash':digest(original)}
        for event in db.execute('SELECT * FROM benchmark_audit').fetchall():
            key,dataset,version,query,actor,action,details,created=event
            db.execute('INSERT OR IGNORE INTO research_reviews VALUES (?,?,?,?,?,?,?,?,?)',
                       ('legacy:'+key,'benchmark_version',dataset,version,actor,'legacy_recorded',action,details,created))
        for event in db.execute('SELECT * FROM benchmark_assessments').fetchall():
            key,experiment,query,payload,metrics,actor,created=event
            db.execute('INSERT OR IGNORE INTO research_reviews VALUES (?,?,?,?,?,?,?,?,?)',
                       ('legacy-assessment:'+key,'benchmark_assessment',key,digest(json.loads(payload)),actor,'legacy_recorded','迁移既有人工评价',encoded({'experiment_id':experiment,'query_id':query}),created))
        if db.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('迁移后外键检查失败')
        db.execute('INSERT INTO research_migrations VALUES (?,?,?)',('unified-research-v1',now(),encoded(manifest)))
    return {'status':'applied',**manifest}
