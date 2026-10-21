"""SQLite is the source of truth for ownership, approval, and run recovery."""

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .auth import hash_password, token_hash
from .knowledge import load_demo_documents


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, settings):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES('schema_version','1');
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
                    tenant_id TEXT NOT NULL, role TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS documents(
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT NOT NULL,
                    content TEXT NOT NULL, roles TEXT NOT NULL, version INTEGER NOT NULL,
                    created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS document_versions(
                    document_id TEXT NOT NULL, version INTEGER NOT NULL, title TEXT NOT NULL,
                    content TEXT NOT NULL, roles TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(document_id,version));
                CREATE TABLE IF NOT EXISTS conversations(
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    title TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, payload TEXT, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    user_id TEXT NOT NULL REFERENCES users(id), status TEXT NOT NULL,
                    question TEXT NOT NULL, intent TEXT NOT NULL, result TEXT,
                    created_at REAL NOT NULL, finished_at REAL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(conversation_id)
                    WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    stage TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts(
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    run_id TEXT UNIQUE NOT NULL REFERENCES runs(id), tenant_id TEXT NOT NULL,
                    title TEXT NOT NULL, description TEXT NOT NULL, priority TEXT NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tickets(
                    id TEXT PRIMARY KEY, draft_id TEXT UNIQUE NOT NULL REFERENCES drafts(id),
                    user_id TEXT NOT NULL REFERENCES users(id), tenant_id TEXT NOT NULL,
                    title TEXT NOT NULL, description TEXT NOT NULL, priority TEXT NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audits(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    action TEXT NOT NULL, resource_id TEXT NOT NULL, created_at REAL NOT NULL);
            """)
            if (
                db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
                != "1"
            ):
                raise RuntimeError("unsupported database schema")
            if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                if settings.seed_demo:
                    users = (
                        ("employee", "AgentDemo!2026", "acme", "employee"),
                        ("admin", "AgentAdmin!2026", "acme", "it_admin"),
                        ("beta", "AgentBeta!2026", "beta", "employee"),
                    )
                else:
                    if not settings.admin_username or len(settings.admin_password) < 12:
                        raise ValueError(
                            "first startup requires APP_ADMIN_USERNAME and APP_ADMIN_PASSWORD (12+ characters)"
                        )
                    users = (
                        (
                            settings.admin_username,
                            settings.admin_password,
                            settings.admin_tenant,
                            "it_admin",
                        ),
                    )
                for username, password, tenant, role in users:
                    db.execute(
                        "INSERT INTO users VALUES(?,?,?,?,?)",
                        (new_id("usr"), username, hash_password(password), tenant, role),
                    )
            if settings.seed_demo:
                for document in load_demo_documents():
                    db.execute(
                        "INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?,?)",
                        (
                            document.id,
                            document.tenant_id,
                            document.title,
                            document.content,
                            encode(document.roles),
                            1,
                            time.time(),
                        ),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO document_versions VALUES(?,?,?,?,?,?)",
                        (
                            document.id,
                            1,
                            document.title,
                            document.content,
                            encode(document.roles),
                            time.time(),
                        ),
                    )
            interrupted = db.execute(
                "SELECT id FROM runs WHERE status IN ('queued','running')"
            ).fetchall()
            for run in interrupted:
                result = {
                    "status": "failed",
                    "error": "server_restarted",
                    "text": "服务重启中断了此次运行，请重新发送问题。",
                }
                db.execute(
                    "UPDATE runs SET status='failed',result=?,finished_at=? WHERE id=?",
                    (encode(result), time.time(), run[0]),
                )
                db.execute(
                    "INSERT INTO events(run_id,stage,payload,created_at) VALUES(?,?,?,?)",
                    (run[0], "failed", encode(result), time.time()),
                )
                conversation_id = db.execute(
                    "SELECT conversation_id FROM runs WHERE id=?", (run[0],)
                ).fetchone()[0]
                db.execute(
                    "INSERT INTO messages(conversation_id,role,content,payload,created_at) VALUES(?,?,?,?,?)",
                    (conversation_id, "assistant", result["text"], encode(result), time.time()),
                )
                db.execute(
                    "UPDATE drafts SET status='cancelled' WHERE run_id=? AND status='pending'",
                    (run[0],),
                )

    def user_by_name(self, username):
        with self.connection() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            return dict(row) if row else None

    def create_session(self, user_id, hours):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (token_hash(token), user_id, csrf, time.time() + hours * 3600),
            )
        return token, csrf

    def session(self, token):
        if not token:
            return None
        with self.connection() as db:
            row = db.execute(
                """SELECT u.id,u.username,u.tenant_id,u.role,s.csrf FROM sessions s
                JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?""",
                (token_hash(token), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    def documents(self, user):
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM documents WHERE tenant_id=? ORDER BY created_at DESC,id",
                (user["tenant_id"],),
            ).fetchall()
            return [
                dict(row) | {"roles": json.loads(row["roles"])}
                for row in rows
                if user["role"] in json.loads(row["roles"])
            ]

    def save_document(self, user, title, content, roles, document_id=None):
        document_id = document_id or new_id("doc")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM documents WHERE id=? AND tenant_id=?",
                (document_id, user["tenant_id"]),
            ).fetchone()
            if (
                previous is None
                and db.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone()
            ):
                raise KeyError("document not found")
            version = previous["version"] + 1 if previous else 1
            now = time.time()
            db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,content=excluded.content,roles=excluded.roles,version=excluded.version",
                (document_id, user["tenant_id"], title, content, encode(roles), version, now),
            )
            db.execute(
                "INSERT INTO document_versions VALUES(?,?,?,?,?,?)",
                (document_id, version, title, content, encode(roles), now),
            )
            db.execute(
                "INSERT INTO audits(user_id,action,resource_id,created_at) VALUES(?,?,?,?)",
                (user["id"], "document_saved", document_id, now),
            )
        return {"id": document_id, "version": version}

    def create_conversation(self, user, title):
        conversation = {"id": new_id("chat"), "title": title, "created_at": time.time()}
        with self.connection() as db:
            db.execute(
                "INSERT INTO conversations VALUES(?,?,?,?)",
                (conversation["id"], user["id"], title, conversation["created_at"]),
            )
        return conversation

    def conversations(self, user):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,title,created_at FROM conversations WHERE user_id=? ORDER BY created_at DESC",
                    (user["id"],),
                )
            ]

    def owns_conversation(self, conversation_id, user):
        with self.connection() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM conversations WHERE id=? AND user_id=?",
                    (conversation_id, user["id"]),
                ).fetchone()
            )

    def messages(self, conversation_id):
        with self.connection() as db:
            return [
                dict(row) | {"payload": json.loads(row["payload"]) if row["payload"] else None}
                for row in db.execute(
                    "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
                )
            ]

    def create_run(self, user, conversation_id, question, intent):
        run_id, now = new_id("run"), time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            personal = db.execute(
                "SELECT COUNT(*) FROM runs WHERE user_id=? AND status IN ('queued','running')",
                (user["id"],),
            ).fetchone()[0]
            if active >= 12 or personal >= 3:
                raise ValueError("active run capacity reached; wait for existing tasks")
            try:
                db.execute(
                    "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        conversation_id,
                        user["id"],
                        "queued",
                        question,
                        intent,
                        None,
                        now,
                        None,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("conversation already has an active run") from error
            db.execute(
                "INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                (conversation_id, "user", question, now),
            )
        self.event(run_id, "queued", {"message": "任务已入队"})
        return run_id

    def run(self, run_id, user=None):
        with self.connection() as db:
            query, params = "SELECT * FROM runs WHERE id=?", [run_id]
            if user:
                query += " AND user_id=?"
                params.append(user["id"])
            row = db.execute(query, params).fetchone()
            return (
                dict(row) | {"result": json.loads(row["result"]) if row["result"] else None}
                if row
                else None
            )

    def runs(self, conversation_id):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,status,created_at FROM runs WHERE conversation_id=? ORDER BY created_at DESC LIMIT 30",
                    (conversation_id,),
                )
            ]

    def start_run(self, run_id):
        with self.connection() as db:
            changed = db.execute(
                "UPDATE runs SET status='running' WHERE id=? AND status='queued'", (run_id,)
            ).rowcount
            return bool(changed)

    def event(self, run_id, stage, payload):
        with self.connection() as db:
            db.execute(
                "INSERT INTO events(run_id,stage,payload,created_at) VALUES(?,?,?,?)",
                (run_id, stage, encode(payload), time.time()),
            )

    def events(self, run_id, after=0):
        with self.connection() as db:
            return [
                dict(row) | {"payload": json.loads(row["payload"])}
                for row in db.execute(
                    "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, after)
                )
            ]

    def finish_run(self, run_id, result, failed=False):
        with self.connection() as db:
            run = db.execute("SELECT conversation_id FROM runs WHERE id=?", (run_id,)).fetchone()
            db.execute(
                "UPDATE runs SET status=?,result=?,finished_at=? WHERE id=?",
                ("failed" if failed else "completed", encode(result), time.time(), run_id),
            )
            db.execute(
                "INSERT INTO messages(conversation_id,role,content,payload,created_at) VALUES(?,?,?,?,?)",
                (run[0], "assistant", result["text"], encode(result), time.time()),
            )
            if failed:
                db.execute(
                    "UPDATE drafts SET status='cancelled' WHERE run_id=? AND status='pending'",
                    (run_id,),
                )
        self.event(run_id, "failed" if failed else "completed", {"status": result["status"]})

    def prepare_ticket(self, user, run_id, title, description, priority):
        with self.connection() as db:
            existing = db.execute("SELECT * FROM drafts WHERE run_id=?", (run_id,)).fetchone()
            if existing:
                return dict(existing)
            draft_id = new_id("draft")
            db.execute(
                "INSERT INTO drafts VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    draft_id,
                    user["id"],
                    run_id,
                    user["tenant_id"],
                    title,
                    description,
                    priority,
                    "pending",
                    time.time(),
                ),
            )
            return dict(db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())

    def decide_draft(self, draft_id, user, approve):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            draft = db.execute(
                "SELECT * FROM drafts WHERE id=? AND user_id=?", (draft_id, user["id"])
            ).fetchone()
            if not draft:
                raise KeyError("draft not found")
            ticket = db.execute("SELECT * FROM tickets WHERE draft_id=?", (draft_id,)).fetchone()
            if ticket:
                if not approve:
                    raise ValueError("approved ticket cannot be rejected")
                return dict(ticket)
            if draft["status"] != "pending":
                raise ValueError("draft is no longer pending")
            run = db.execute("SELECT status FROM runs WHERE id=?", (draft["run_id"],)).fetchone()
            if not run or run["status"] != "completed":
                raise ValueError("wait for the run to complete before confirming")
            if not approve:
                db.execute("UPDATE drafts SET status='rejected' WHERE id=?", (draft_id,))
                db.execute(
                    "INSERT INTO audits(user_id,action,resource_id,created_at) VALUES(?,?,?,?)",
                    (user["id"], "draft_rejected", draft_id, time.time()),
                )
                return {"id": draft_id, "status": "rejected"}
            ticket_id, now = new_id("ticket"), time.time()
            db.execute(
                "INSERT INTO tickets VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    ticket_id,
                    draft_id,
                    user["id"],
                    user["tenant_id"],
                    draft["title"],
                    draft["description"],
                    draft["priority"],
                    "open",
                    now,
                ),
            )
            db.execute("UPDATE drafts SET status='approved' WHERE id=?", (draft_id,))
            db.execute(
                "INSERT INTO audits(user_id,action,resource_id,created_at) VALUES(?,?,?,?)",
                (user["id"], "ticket_created", ticket_id, now),
            )
            return dict(db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())

    def tickets(self, user):
        with self.connection() as db:
            if user["role"] == "it_admin":
                return [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM tickets WHERE tenant_id=? ORDER BY created_at DESC",
                        (user["tenant_id"],),
                    )
                ]
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM tickets WHERE user_id=? ORDER BY created_at DESC", (user["id"],)
                )
            ]

    def cancel_drafts(self, run_id):
        with self.connection() as db:
            db.execute(
                "UPDATE drafts SET status='cancelled' WHERE run_id=? AND status='pending'",
                (run_id,),
            )

    def update_ticket(self, ticket_id, user, status):
        with self.connection() as db:
            changed = db.execute(
                "UPDATE tickets SET status=? WHERE id=? AND tenant_id=?",
                (status, ticket_id, user["tenant_id"]),
            ).rowcount
            if not changed:
                raise KeyError("ticket not found")
            db.execute(
                "INSERT INTO audits(user_id,action,resource_id,created_at) VALUES(?,?,?,?)",
                (user["id"], "ticket_" + status, ticket_id, time.time()),
            )
            return dict(db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())

    def drafts(self, user):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM drafts WHERE user_id=? ORDER BY created_at DESC", (user["id"],)
                )
            ]

    def audits(self, user):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audits WHERE user_id IN (SELECT id FROM users WHERE tenant_id=?) ORDER BY id DESC LIMIT 100",
                    (user["tenant_id"],),
                )
            ]
