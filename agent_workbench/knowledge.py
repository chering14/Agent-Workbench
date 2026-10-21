"""A deliberately simple, permission-filtered lexical search baseline."""

import json
import re
import unicodedata
from importlib.resources import files

from .models import Document, RequestContext, SearchHit

_STOP_WORDS = frozenset({"怎么", "如何", "什么", "请问", "一下", "是否", "可以"})


def tokenize(text: str) -> frozenset[str]:
    """Use Latin words and Chinese bigrams, without downloading a tokenizer."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        tokens.update(sequence[i : i + 2] for i in range(len(sequence) - 1))
    return frozenset(tokens - _STOP_WORDS)


def load_demo_documents() -> tuple[Document, ...]:
    """Load only synthetic fixtures packaged with this application."""
    content = files("agent_workbench").joinpath("data/knowledge.json").read_text(encoding="utf-8")
    records = json.loads(content)
    return tuple(
        Document(
            id=record["id"],
            title=record["title"],
            tenant_id=record["tenant_id"],
            roles=tuple(record["roles"]),
            content=record["content"],
        )
        for record in records
    )


class KnowledgeBase:
    def __init__(self, documents: tuple[Document, ...]) -> None:
        ids = [document.id for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document ids must be globally unique")
        self._documents = documents

    def search(
        self, question: str, context: RequestContext, *, top_k: int = 2
    ) -> tuple[SearchHit, ...]:
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            raise ValueError("question must contain 1 to 2000 characters")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 5:
            raise ValueError("top_k must be an integer between 1 and 5")

        terms = tokenize(question)
        if not terms:
            return ()

        hits: list[SearchHit] = []
        for document in self._documents:
            # Filter BEFORE scoring or passing content to any future model.
            if document.tenant_id != context.tenant_id or context.role not in document.roles:
                continue
            # A shared generic word in the body (e.g. "失败") is weak evidence.
            # Weight the title twice to favor the document's actual subject.
            title_terms = tokenize(document.title)
            content_terms = tokenize(document.content)
            score = (2 * len(terms & title_terms) + len(terms & content_terms)) / (3 * len(terms))
            if score >= 0.2:
                hits.append(SearchHit(document, round(score, 6)))

        hits.sort(key=lambda hit: (-hit.score, hit.document.id))
        return tuple(hits[:top_k])
