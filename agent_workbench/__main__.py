"""Run from the repository: python -m agent_workbench ask 'VPN 连接失败'."""

import argparse
import json
import sys
from dataclasses import asdict

from .evaluation import evaluate
from .knowledge import KnowledgeBase, load_demo_documents
from .models import RequestContext
from .workflow import answer_question


def main() -> int:
    # Keep Chinese output readable in UTF-8 terminals and redirected CI output.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Agent Workbench: local software teams")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="Search synthetic knowledge and preserve citations")
    ask.add_argument("question")
    ask.add_argument("--tenant", default="acme", help="Local demo context; not authentication")
    ask.add_argument("--role", choices=("employee", "it_admin"), default="employee")
    ask.add_argument("--json", action="store_true", help="Include structured result and trace")
    commands.add_parser("eval", help="Run synthetic smoke cases; returns nonzero on failure")
    commands.add_parser("app-eval", help="Run complete synthetic application acceptance checks")
    commands.add_parser(
        "check-model", help="Make one real model tool call (may incur provider charges)"
    )
    serve = commands.add_parser("serve", help="Start the complete web application")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument(
        "--container",
        action="store_true",
        help="Permit 0.0.0.0 inside a container whose host port is bound to loopback",
    )
    args = parser.parse_args()
    if args.command == "app-eval":
        from .acceptance import evaluate_application

        report = evaluate_application()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] == report["total"] else 1
    if args.command == "check-model":
        from .config import Settings
        from .providers import CompatibleProvider
        from .schemas import REVIEW_TOOLS

        settings = Settings.from_env()
        if settings.mode != "live":
            parser.error("check-model requires APP_MODE=live with configured credentials")
        reply = CompatibleProvider(settings).complete(
            [
                {
                    "role": "system",
                    "content": "Call submit_review with approved=true and reason='protocol check'.",
                },
                {"role": "user", "content": "Check tool calling protocol."},
            ],
            REVIEW_TOOLS,
        )
        print(
            json.dumps(
                {
                    "ok": reply.calls[0].name == "submit_review",
                    "model": settings.model,
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                },
                ensure_ascii=False,
            )
        )
        return 0 if reply.calls[0].name == "submit_review" else 1
    if args.command == "serve":
        import os

        import uvicorn

        from .team.api import create_team_app

        application = create_team_app()
        host = args.host or os.getenv("APP_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost", "::1"} and not (
            args.container and host == "0.0.0.0"
        ):
            parser.error("Local Teams requires a loopback host: 127.0.0.1, localhost or ::1")
        uvicorn.run(
            application,
            host=host,
            port=args.port or int(os.getenv("APP_PORT", "8765")),
        )
        return 0
    knowledge_base = KnowledgeBase(load_demo_documents())

    if args.command == "eval":
        report = evaluate(knowledge_base)
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        return 0 if report.total == report.passed else 1

    try:
        result = answer_question(
            args.question, RequestContext(args.tenant, args.role), knowledge_base
        )
    except ValueError as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(result.text)
        print(f"\nmode={result.mode} | status={result.status} | run_id={result.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
