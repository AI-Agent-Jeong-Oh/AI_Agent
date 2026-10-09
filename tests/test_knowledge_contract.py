from __future__ import annotations

import unittest

from video_agent.knowledge.contracts import (
    EmbeddingInfo,
    KnowledgeHit,
    KnowledgeQuery,
    KnowledgeResult,
    KnowledgeStatus,
    UnavailableKnowledgeSearch,
)


class KnowledgeContractTests(unittest.TestCase):
    def test_unconnected_search_returns_unavailable_without_fake_sources(self) -> None:
        result = UnavailableKnowledgeSearch().search(KnowledgeQuery("관련 지침"))
        self.assertEqual(result.status, KnowledgeStatus.UNAVAILABLE)
        self.assertFalse(result.hits)
        self.assertIn("미연결", result.message or "")

    def test_hit_carries_source_page_version_and_embedding(self) -> None:
        hit = KnowledgeHit(
            text="근거 본문",
            document_id="doc-1",
            document_name="안전 지침.pdf",
            page=3,
            material_version="2026-10-01",
            score=0.87,
            embedding=EmbeddingInfo("embed-model", "1.0", 768),
        )
        result = KnowledgeResult(KnowledgeStatus.READY, (hit,))
        self.assertEqual(result.hits[0].page, 3)


if __name__ == "__main__":
    unittest.main()
