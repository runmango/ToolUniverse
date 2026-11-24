"""Infrastructure shared by research modules; legacy serialization stays stable."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

def now(): return datetime.now(timezone.utc).isoformat()
def encoded(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
def digest(value): return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()
def stable(*parts): return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded(parts)))

class SQLiteStore:
    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db: yield db
        finally: db.close()

def initialize(db):
    db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))

def revision(db, kind, identity, payload):
    if kind in {'entity','relation'}:
        payload={k:v for k,v in payload.items() if k not in {'revision_id','human_review_status','start_type','end_type'}}
    fingerprint = digest(payload)
    key = stable('revision',kind,identity,fingerprint)
    db.execute('INSERT OR IGNORE INTO research_revisions VALUES (?,?,?,?,?,?)',
               (key,kind,identity,fingerprint,encoded(payload),now()))
    return key

def review(db, kind, identity, version, actor, decision, reason, details=None):
    if not isinstance(actor,str) or not actor.strip() or not isinstance(reason,str) or not reason.strip():
        raise ValueError('审核必须记录审核人和依据')
    identity_event=uuid.uuid4().hex
    db.execute('INSERT INTO research_reviews VALUES (?,?,?,?,?,?,?,?,?)',
               (identity_event,kind,identity,version,actor.strip(),decision,reason,encoded(details or {}),now()))
    return identity_event
