"""Run small, versioned regression fixtures; never label these a real benchmark."""

import json
from dataclasses import dataclass
from importlib.resources import files

from .knowledge import KnowledgeBase
from .models import RequestContext
from .workflow import answer_question


@dataclass(frozen=True)
class CaseResult:
    id: str
    passed: bool
    status: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationReport:
    suite: str
    total: int
    passed: int
    pass_rate: float
    cases: tuple[CaseResult, ...]
    limitation: str = "Synthetic smoke cases only; not production quality or an LLM benchmark."


def evaluate(knowledge_base: KnowledgeBase) -> EvaluationReport:
    cases = json.loads(
        files("agent_workbench").joinpath("data/eval_cases.json").read_text(encoding="utf-8")
    )
    if not cases:
        raise ValueError("the evaluation suite must not be empty")
    results = []
    for case in cases:
        context = RequestContext(case["tenant_id"], case["role"])
        answer = answer_question(case["question"], context, knowledge_base)
        actual_ids = tuple(citation.document_id for citation in answer.citations)
        # Exact set equality catches unwanted extra citations as well as omissions.
        passed = answer.status == case["expected_status"] and set(actual_ids) == set(
            case["expected_citation_ids"]
        )
        results.append(CaseResult(case["id"], passed, answer.status, actual_ids))
    passed_count = sum(result.passed for result in results)
    return EvaluationReport(
        suite="m0_synthetic_smoke_v1",
        total=len(results),
        passed=passed_count,
        pass_rate=passed_count / len(results),
        cases=tuple(results),
    )
