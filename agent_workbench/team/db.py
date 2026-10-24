"""Versioned local state; transactional transitions and budget reservations."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + "_" + uuid4().hex[:16]


def dump(value):
    return json.dumps(value, ensure_ascii=False)


class TeamStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=20000")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self):
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("数据库版本高于当前程序，请升级程序")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS entities_kind ON entities(kind);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, actor TEXT NOT NULL,
                  payload TEXT NOT NULL, created TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_project ON events(project_id,seq);
                CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                  task_id TEXT NOT NULL, member_id TEXT NOT NULL, tokens INTEGER NOT NULL,
                  cost REAL NOT NULL, settled INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
        # A process loss may have consumed a provider call. Keep its reservation as an estimate.
        for task in self.list("task"):
            if task["status"] in {"running", "queued", "waiting_approval"}:
                self.update(
                    task["id"],
                    lambda t: t.update(
                        status="interrupted",
                        pending=None,
                        error="应用重启，未自动重放工具；检查工作副本后可重试或交接。",
                    ),
                )
        with self.connect() as con:
            con.execute("UPDATE calls SET settled=1 WHERE settled=0")

    def create(self, kind, data, id=None):
        data = dict(data, id=id or uid(kind), created=now(), updated=now())
        with self.connect() as con:
            con.execute("INSERT INTO entities VALUES(?,?,?)", (data["id"], kind, dump(data)))
        return data

    def get(self, id, kind=None):
        with self.connect() as con:
            row = con.execute("SELECT kind,data FROM entities WHERE id=?", (id,)).fetchone()
        if not row or (kind and row["kind"] != kind):
            raise KeyError(id)
        return json.loads(row["data"])

    def list(self, kind):
        with self.connect() as con:
            return [
                json.loads(r[0])
                for r in con.execute(
                    "SELECT data FROM entities WHERE kind=? ORDER BY rowid", (kind,)
                )
            ]

    def update(self, id, fn):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT data FROM entities WHERE id=?", (id,)).fetchone()
            if not row:
                raise KeyError(id)
            data = json.loads(row[0])
            fn(data)
            data["updated"] = now()
            con.execute("UPDATE entities SET data=? WHERE id=?", (dump(data), id))
        return data

    def event(self, project, kind, actor, payload, task=None):
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO events(project_id,task_id,kind,actor,payload,created) VALUES(?,?,?,?,?,?)",
                (project, task, kind, actor, dump(payload), now()),
            )
            return cur.lastrowid

    def events(self, project, after=0, limit=500):
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM events WHERE project_id=? AND seq>? ORDER BY seq LIMIT ?",
                (project, after, limit),
            ).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]

    def recent_events(self, project, limit=30):
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM events WHERE project_id=? ORDER BY seq DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in reversed(rows)]

    def accept_plan(self, parent_id, children):
        """Create the complete approved plan and mark acceptance in one transaction."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            parent = json.loads(
                con.execute("SELECT data FROM entities WHERE id=?", (parent_id,)).fetchone()[0]
            )
            if parent["plan_accepted"]:
                raise ValueError("计划已经采用")
            for child in children:
                con.execute(
                    "INSERT INTO entities VALUES(?,?,?)", (child["id"], "task", dump(child))
                )
            parent.update(
                plan_accepted=True, planned_ids=[c["id"] for c in children], updated=now()
            )
            con.execute("UPDATE entities SET data=? WHERE id=?", (dump(parent), parent_id))

    def reserve(self, project_id, task_id, member, tokens, estimated_cost):
        id = uid("call")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            project = json.loads(
                con.execute("SELECT data FROM entities WHERE id=?", (project_id,)).fetchone()[0]
            )
            used = con.execute(
                "SELECT COALESCE(SUM(tokens),0),COALESCE(SUM(cost),0) FROM calls WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if used[0] + tokens > project["token_budget"]:
                raise ValueError("项目 Token 预算不足，请提高预算或缩小任务")
            if project.get("budget_usd") is not None:
                if estimated_cost is None:
                    raise ValueError("项目启用金额预算，请先填写成员的输入和输出单价")
                if used[1] + estimated_cost > project["budget_usd"]:
                    raise ValueError("项目金额预算不足，已停止安排新调用")
            con.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,0,?)",
                (
                    id,
                    project_id,
                    task_id,
                    member["id"],
                    tokens,
                    estimated_cost or 0,
                    dump({"estimated": True, "price_known": estimated_cost is not None}),
                ),
            )
        return id

    def settle(self, id, input_tokens, output_tokens, member, known):
        with self.connect() as con:
            row = con.execute("SELECT * FROM calls WHERE id=?", (id,)).fetchone()
            if not row or row["settled"]:
                return
            price_known = (
                member.get("input_price") is not None and member.get("output_price") is not None
            )
            cost = (
                (
                    (input_tokens * member["input_price"] + output_tokens * member["output_price"])
                    / 1_000_000
                )
                if price_known
                else 0
            )
            if not known:
                input_tokens, output_tokens, cost = row["tokens"], 0, row["cost"]
            detail = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated": not known,
                "price_known": price_known,
            }
            con.execute(
                "UPDATE calls SET tokens=?,cost=?,settled=1,detail=? WHERE id=?",
                (input_tokens + output_tokens, cost, dump(detail), id),
            )

    def usage(self, project):
        with self.connect() as con:
            rows = [
                dict(r) | {"detail": json.loads(r["detail"])}
                for r in con.execute("SELECT * FROM calls WHERE project_id=?", (project,))
            ]
        return {
            "tokens": sum(r["tokens"] for r in rows),
            "cost": sum(r["cost"] for r in rows),
            "calls": len(rows),
            "estimated": any(r["detail"].get("estimated") for r in rows),
            "price_known": all(r["detail"].get("price_known") for r in rows),
            "ledger": rows,
        }
