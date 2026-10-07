"""모델 호출 없이 분할 기준, 수정문 선택, 진행률, 입력 검증만 확인한다."""

from __future__ import annotations

import unittest
from io import BytesIO

from app import app
from review.config import (
    CONTRACT_CHUNK_OVERLAP,
    CONTRACT_CHUNK_SIZE,
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    SEPARATORS,
)
from review.pipeline import build_splitter, pick_revision
from review.progress import Progress


class SplitterTests(unittest.TestCase):
    def test_settings_match_requested_values(self):
        rag = build_splitter(RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP)
        contract = build_splitter(CONTRACT_CHUNK_SIZE, CONTRACT_CHUNK_OVERLAP)

        self.assertEqual(SEPARATORS, ["\n", "\n\n"])
        self.assertEqual(rag._chunk_size, 30)
        self.assertEqual(rag._chunk_overlap, 5)
        self.assertEqual(contract._chunk_size, 30)
        self.assertEqual(contract._chunk_overlap, 0)
        self.assertEqual(rag._separators, ["\n", "\n\n"])
        self.assertEqual(contract._separators, ["\n", "\n\n"])

    def test_short_lines_overlap_only_on_rag_splitter(self):
        text = "\n".join("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        rag_chunks = build_splitter(30, 5).split_text(text)
        contract_chunks = build_splitter(30, 0).split_text(text)

        self.assertGreater(len(rag_chunks), 1)
        self.assertTrue(rag_chunks[1].startswith("N"))
        self.assertTrue(contract_chunks[1].startswith("P"))
        self.assertGreater(len(rag_chunks[1]), len(contract_chunks[1]))

    def test_line_without_newline_stays_together(self):
        # 지정한 구분자는 줄바꿈뿐이라, 줄바꿈이 없는 긴 문장은 한 덩어리로 남는다.
        chunks = build_splitter(30, 0).split_text("가" * 80)
        self.assertEqual(chunks, ["가" * 80])


class RevisionTests(unittest.TestCase):
    def test_revision_is_kept_only_when_text_changes(self):
        original = "도급인과 수급인은 협의하여 이를 감액 또는 증액 조정할 수 없다."
        revised = "도급인과 수급인은 협의하여 이를 감액 또는 증액 조정할 수 있다."

        self.assertEqual(pick_revision(original, True, revised), revised)
        self.assertIsNone(pick_revision(original, False, revised))
        self.assertIsNone(pick_revision(original, True, original))
        self.assertIsNone(pick_revision(original, True, "  "))


class ProgressTests(unittest.TestCase):
    def test_tqdm_bar_reports_percent_and_counts(self):
        progress = Progress(total=4, desc="가이드라인 임베딩")
        try:
            first = progress.event()
            progress.advance(2)
            mid = progress.event()
        finally:
            progress.close()

        self.assertIn("가이드라인 임베딩", first["bar"])
        self.assertEqual(first["percent"], 0)
        self.assertIn("50%", mid["bar"])
        self.assertIn("2/4", mid["bar"])
        self.assertEqual(mid["percent"], 50)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_home_has_upload_actions_and_hides_review(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("RAG 파일 업로드", html)
        self.assertIn("계약서 업로드", html)
        self.assertIn('id="reviewBtn" hidden', html)
        self.assertIn("일반적인 질문을 입력하세요", html)

    def test_empty_question_does_not_call_model(self):
        response = self.client.post("/api/chat", json={"question": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("질문", response.get_json()["error"])

    def test_non_pdf_upload_is_rejected(self):
        response = self.client.post(
            "/api/rag/upload",
            data={"files": (BytesIO(b"hello"), "memo.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_contract_upload_requires_one_pdf(self):
        response = self.client.post("/api/contract/upload", data={})
        self.assertEqual(response.status_code, 400)

    def test_review_before_upload_is_rejected(self):
        response = self.client.post("/api/contract/review")
        self.assertEqual(response.status_code, 400)
        self.assertIn("계약서", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
