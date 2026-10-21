"""Small immutable contracts shared by retrieval, workflow, and evaluation."""

from dataclasses import dataclass
from typing import Literal

Role = Literal["employee", "it_admin"]
Status = Literal["evidence_found", "insufficient_evidence"]
ROLES = frozenset({"employee", "it_admin"})


@dataclass(frozen=True)
class RequestContext:
    """Trusted application context; a future server must derive it from auth."""

    tenant_id: str
    role: Role = "employee"

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if self.role not in ROLES:
            raise ValueError("unsupported role")


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    tenant_id: str
    roles: tuple[Role, ...]
    content: str

    def __post_init__(self) -> None:
        for field in (self.id, self.title, self.tenant_id, self.content):
            if not isinstance(field, str) or not field.strip():
                raise ValueError("document fields must be non-empty strings")
        if not self.roles or any(role not in ROLES for role in self.roles):
            raise ValueError("document roles must contain supported roles")


@dataclass(frozen=True)
class SearchHit:
    document: Document
    score: float


@dataclass(frozen=True)
class Citation:
    document_id: str
    title: str
    quote: str


@dataclass(frozen=True)
class TraceEvent:
    stage: str
    elapsed_ms: float
    document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Answer:
    run_id: str
    status: Status
    text: str
    citations: tuple[Citation, ...]
    trace: tuple[TraceEvent, ...]
    mode: str = "offline_extractive_baseline"
