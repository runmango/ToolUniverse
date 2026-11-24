"""Small durable conversation store, separate from upstream SDK data."""
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
import uuid


class ConversationStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, thread_id TEXT,
                    model TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, tools TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'completed', created_at REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS message_provenance (
                    message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, title="新对话"):
        item = dict(id=uuid.uuid4().hex, title=title, thread_id=None, model=None,
                    created_at=time.time(), updated_at=time.time())
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (:id,:title,:thread_id,:model,:created_at,:updated_at)", item)
        return item

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 300")]

    def get(self, session_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError("会话不存在")
            item = dict(row)
            item["messages"] = [dict(message) for message in db.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY created_at, rowid", (session_id,))]
        for message in item["messages"]:
            message["tools"] = json.loads(message["tools"])
            if message['role']=='assistant':
                with self.connect() as db:
                    provenance=db.execute('SELECT payload FROM message_provenance WHERE message_id=?',(message['id'],)).fetchone()
                message['generation']=json.loads(provenance[0]) if provenance else {'origin':'ai','model_name':'unknown','model_version':'unknown','verification_status':'unverified','metadata_status':'incomplete'}
        return item

    def update(self, session_id, **changes):
        changes = {key: value for key, value in changes.items() if key in {"title", "thread_id", "model"}}
        changes["updated_at"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE sessions SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?",
                       [*changes.values(), session_id])

    def message(self, session_id, role, content, tools=None, status="completed", generation=None):
        item = dict(id=uuid.uuid4().hex, session_id=session_id, role=role, content=content,
                    tools=tools or [], status=status, created_at=time.time())
        with self.connect() as db:
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (
                item["id"], session_id, role, content, json.dumps(item["tools"], ensure_ascii=False),
                status, item["created_at"]))
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
            if role=='assistant':
                metadata={'origin':'ai','model_name':'unknown','model_version':'unknown','verification_status':'unverified','metadata_status':'incomplete',**(generation or {})}
                db.execute('INSERT INTO message_provenance VALUES (?,?)',(item['id'],json.dumps(metadata,ensure_ascii=False)))
                item['generation']=metadata
        return item

    def delete(self, session_id):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
