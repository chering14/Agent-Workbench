import json
import subprocess
import sys
import unittest
from dataclasses import asdict

from agent_workbench.evaluation import evaluate
from agent_workbench.knowledge import KnowledgeBase, load_demo_documents
from agent_workbench.models import Citation, Document, RequestContext
from agent_workbench.workflow import answer_question, validate_citations


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase(load_demo_documents())
        self.employee = RequestContext("acme")

    def test_answer_preserves_original_quote_and_stable_source(self):
        result = answer_question("VPN 连接失败", self.employee, self.kb)
        self.assertEqual(result.status, "evidence_found")
        self.assertEqual([c.document_id for c in result.citations], ["IT-001"])
        self.assertIn(result.citations[0].quote, result.text)
        validate_citations(result.citations, self.kb.search("VPN", self.employee))

    def test_missing_evidence_abstains_without_citations(self):
        result = answer_question("火星基地停机坪坐标", self.employee, self.kb)
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertFalse(result.citations)

    def test_tenant_isolation_happens_before_results_leave_retrieval(self):
        self.assertEqual(self.kb.search("猎户计划客户名单", self.employee), ())
        hits = self.kb.search("猎户计划客户名单", RequestContext("beta"))
        self.assertEqual([hit.document.id for hit in hits], ["BETA-001"])

    def test_role_filter_prevents_reading_admin_document(self):
        self.assertEqual(self.kb.search("生产密钥轮换", self.employee), ())
        hits = self.kb.search("生产密钥轮换", RequestContext("acme", "it_admin"))
        self.assertEqual([hit.document.id for hit in hits], ["SEC-001"])

    def test_invalid_questions_are_rejected(self):
        for question in ("", "  ", "a" * 2001, None):
            with self.subTest(question_length=len(question) if question else 0):
                with self.assertRaises(ValueError):
                    self.kb.search(question, self.employee)

    def test_invalid_context_and_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            RequestContext("")
        with self.assertRaises(ValueError):
            RequestContext("acme", "superuser")
        for top_k in (0, 6, 1.5, True):
            with self.subTest(top_k=top_k):
                with self.assertRaises(ValueError):
                    self.kb.search("VPN", self.employee, top_k=top_k)

    def test_forged_citations_are_rejected(self):
        hits = self.kb.search("VPN", self.employee)
        original = hits[0].document
        invalid = (
            (),
            (Citation("fake-id", original.title, original.content),),
            (Citation(original.id, "wrong title", original.content),),
            (Citation(original.id, original.title, "捏造的回答"),),
            (Citation(original.id, original.title, ""),),
        )
        for citations in invalid:
            with self.subTest(citations=citations):
                with self.assertRaises(ValueError):
                    validate_citations(citations, hits)

    def test_duplicate_document_ids_are_rejected(self):
        document = Document("a", "title", "acme", ("employee",), "body")
        with self.assertRaises(ValueError):
            KnowledgeBase((document, document))

    def test_no_raw_question_or_content_is_added_to_trace(self):
        question = "VPN secret-marker-629"
        result = answer_question(question, self.employee, self.kb)
        serialized_trace = json.dumps(asdict(result)["trace"])
        self.assertNotIn("secret-marker-629", serialized_trace)
        self.assertNotIn("vpn.example.invalid", serialized_trace)
        self.assertTrue(result.trace)
        self.assertGreaterEqual(result.trace[0].elapsed_ms, 0)

    def test_full_width_and_case_normalization(self):
        hits = self.kb.search("ＶＰＮ", self.employee)
        self.assertEqual([hit.document.id for hit in hits], ["IT-001"])

    def test_synthetic_regression_suite(self):
        report = evaluate(self.kb)
        self.assertEqual(report.total, 10)
        self.assertEqual(report.passed, report.total, report)

    def test_cli_returns_valid_json(self):
        completed = subprocess.run(
            [sys.executable, "-m", "agent_workbench", "ask", "VPN", "--json"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["mode"], "offline_extractive_baseline")
        self.assertEqual(result["citations"][0]["document_id"], "IT-001")

    def test_cli_invalid_input_exits_with_usage_error(self):
        completed = subprocess.run(
            [sys.executable, "-m", "agent_workbench", "ask", "   "],
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()
