"""Retrieve -> quote -> validate, with a minimal in-memory execution trace."""

from time import perf_counter
from uuid import uuid4

from .knowledge import KnowledgeBase
from .models import Answer, Citation, RequestContext, SearchHit, TraceEvent


def validate_citations(citations: tuple[Citation, ...], hits: tuple[SearchHit, ...]) -> None:
    """Check provenance; this does NOT establish semantic answer correctness."""
    documents = {hit.document.id: hit.document for hit in hits}
    if not citations:
        raise ValueError("an evidence_found result must include evidence")
    for citation in citations:
        document = documents.get(citation.document_id)
        if document is None:
            raise ValueError("citation must reference a retrieved document")
        if citation.title != document.title:
            raise ValueError("citation title does not match its source")
        if not citation.quote.strip() or citation.quote not in document.content:
            raise ValueError("citation quote must occur verbatim in its source")


def answer_question(
    question: str, context: RequestContext, knowledge_base: KnowledgeBase
) -> Answer:
    started = perf_counter()
    run_id = uuid4().hex
    hits = knowledge_base.search(question, context)
    trace = [
        TraceEvent(
            stage="retrieve",
            elapsed_ms=round((perf_counter() - started) * 1000, 3),
            document_ids=tuple(hit.document.id for hit in hits),
        )
    ]
    if not hits:
        trace.append(TraceEvent("abstain", round((perf_counter() - started) * 1000, 3)))
        return Answer(
            run_id=run_id,
            status="insufficient_evidence",
            text="在当前可访问的资料中没有找到足够依据，请补充信息或联系人工支持。",
            citations=(),
            trace=tuple(trace),
        )

    citations = tuple(
        Citation(hit.document.id, hit.document.title, hit.document.content) for hit in hits
    )
    validate_citations(citations, hits)
    trace.append(TraceEvent("validate_citations", round((perf_counter() - started) * 1000, 3)))
    # Verbatim evidence keeps the first baseline honest and reproducible.
    # A later LLM adapter will synthesize a reply, then pass stricter review.
    text = "检索到以下原文（离线基线，请自行核对是否适用）：\n\n" + "\n\n".join(
        f"[{citation.document_id}] {citation.title}\n{citation.quote}" for citation in citations
    )
    return Answer(run_id, "evidence_found", text, citations, tuple(trace))
