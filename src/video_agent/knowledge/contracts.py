from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class KnowledgeStatus(StrEnum):
    READY = "ready"
    NO_RESULTS = "no_results"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EmbeddingInfo:
    model_id: str
    model_version: str
    dimensions: int


@dataclass(frozen=True, slots=True)
class KnowledgeQuery:
    text: str
    top_k: int = 5

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("knowledge query text is required")
        if not 1 <= self.top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    text: str
    document_id: str
    document_name: str
    page: int | None
    material_version: str
    score: float | None = None
    embedding: EmbeddingInfo | None = None

    def __post_init__(self) -> None:
        if not all((self.text.strip(), self.document_id.strip(), self.document_name.strip(), self.material_version.strip())):
            raise ValueError("knowledge hit source fields cannot be empty")
        if self.page is not None and self.page <= 0:
            raise ValueError("page must be positive or None")


@dataclass(frozen=True, slots=True)
class KnowledgeResult:
    status: KnowledgeStatus
    hits: tuple[KnowledgeHit, ...] = ()
    message: str | None = None

    def __post_init__(self) -> None:
        if self.status is KnowledgeStatus.READY and not self.hits:
            raise ValueError("ready knowledge result requires hits")
        if self.status is not KnowledgeStatus.READY and self.hits:
            raise ValueError("non-ready knowledge result cannot contain hits")


class KnowledgeSearch(Protocol):
    def search(self, query: KnowledgeQuery) -> KnowledgeResult: ...


class UnavailableKnowledgeSearch:
    def search(self, query: KnowledgeQuery) -> KnowledgeResult:
        return KnowledgeResult(
            status=KnowledgeStatus.UNAVAILABLE,
            message="PDF 지식 자료 없음/검색기 미연결",
        )
