"""Reproducible full-flow synthetic acceptance report, isolated from real user data."""

import tempfile
from pathlib import Path

from .agent import AgentService
from .config import Settings
from .retrieval import Retriever
from .storage import Store


def evaluate_application():
    checks = []

    def record(name, condition):
        checks.append({"id": name, "passed": bool(condition)})

    with tempfile.TemporaryDirectory(prefix="agent-workbench-eval-") as folder:
        settings = Settings(db_path=str(Path(folder) / "eval.db"))
        store = Store(settings.db_path)
        store.initialize(settings)
        service = AgentService(store, settings)
        employee, admin, beta = [store.user_by_name(name) for name in ("employee", "admin", "beta")]

        def run(question, user=employee, intent="answer"):
            conversation = store.create_conversation(user, question[:35])
            run_id = store.create_run(user, conversation["id"], question, intent)
            service.execute(run_id, user)
            return store.run(run_id)

        for name, question, user, expected, document_id in (
            ("vpn", "VPN 连接失败", employee, "evidence_found", "IT-001"),
            ("password", "账号密码重置", employee, "evidence_found", "IT-002"),
            ("leave", "年假申请流程", employee, "evidence_found", "HR-001"),
            ("missing", "火星基地停机坪坐标", employee, "insufficient_evidence", None),
            ("related_without_answer", "VPN 月费", employee, "insufficient_evidence", None),
            ("role_denied", "生产密钥轮换", employee, "insufficient_evidence", None),
            ("role_allowed", "生产密钥轮换", admin, "evidence_found", "SEC-001"),
            ("tenant_denied", "猎户计划客户名单", employee, "insufficient_evidence", None),
            ("tenant_allowed", "猎户计划客户名单", beta, "evidence_found", "BETA-001"),
        ):
            result = run(question, user)
            ids = [citation["document_id"] for citation in result["result"].get("citations", [])]
            record(
                name,
                result["status"] == "completed"
                and result["result"]["status"] == expected
                and (document_id in ids if document_id else not ids),
            )
        document = store.save_document(
            admin,
            "打印机排障",
            "打印机无法打印时，先检查打印纸和打印队列，然后联系服务台。",
            ["employee", "it_admin"],
        )
        record("document_import", bool(Retriever(store).search("打印机", employee)))
        record(
            "document_version",
            store.save_document(
                admin,
                "打印机排障",
                "打印机无法打印时，按照新流程检查网络和耗材。",
                ["employee", "it_admin"],
                document["id"],
            )["version"]
            == 2,
        )
        result = run("VPN 故障需要人工协助", intent="ticket")
        draft = result["result"].get("draft")
        record("draft_without_write", bool(draft) and not store.tickets(employee))
        ticket = store.decide_draft(draft["id"], employee, True)
        record("approval", ticket["status"] == "open")
        record(
            "idempotent_approval",
            store.decide_draft(draft["id"], employee, True)["id"] == ticket["id"]
            and len(store.tickets(employee)) == 1,
        )
        store.update_ticket(ticket["id"], admin, "resolved")
        record("admin_status", store.tickets(employee)[0]["status"] == "resolved")
        rejected = run("账号故障请协助", intent="ticket")["result"]["draft"]
        record(
            "reject", store.decide_draft(rejected["id"], employee, False)["status"] == "rejected"
        )
        record("audit", any(event["action"] == "ticket_created" for event in store.audits(admin)))
        record("trace", any(event["stage"] == "review" for event in store.events(result["id"])))
        store.initialize(settings)
        record("persistence", len(store.tickets(employee)) == 1)
        conversation = store.create_conversation(employee, "interrupted")
        interrupted = store.create_run(employee, conversation["id"], "VPN", "answer")
        store.initialize(settings)
        record("restart_recovery", store.run(interrupted)["result"]["error"] == "server_restarted")
    passed = sum(check["passed"] for check in checks)
    return {
        "suite": "application_synthetic_acceptance_v1",
        "mode": "demo",
        "total": len(checks),
        "passed": passed,
        "pass_rate": passed / len(checks),
        "checks": checks,
        "limitation": "Synthetic engineering checks only. No real LLM quality, production load, or cloud deployment is measured.",
    }
