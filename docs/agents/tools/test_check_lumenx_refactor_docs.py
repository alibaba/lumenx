"""Regression checks for public documentation validation; no app imports or writes."""

import importlib.util
import re
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "lumenx_public_docs_check", Path(__file__).with_name("check_lumenx_refactor_docs.py")
)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class PublicDocumentationChecks(unittest.TestCase):
    def test_public_documents_are_consistent(self):
        self.assertEqual(CHECKER.check(ROOT)["errors"], [])

    def test_invalid_documents_are_rejected(self):
        def claim_verified_with_navigation(text):
            return re.sub(
                r"^\| P0 \|[^\n]+$",
                "| P0 | 已验证 | public candidate | "
                "[navigation](../../deliverables/github-release-2026-10-30/README.md) |",
                text,
                flags=re.M,
            )

        cases = [
            ("missing link", "PRODUCT-CONTRACT.md", lambda text: text + "\n[missing](missing.md)\n", "Missing material"),
            ("unknown feature", "TEST-MATRIX.md", lambda text: text + "\nF99\n", "Unknown coverage"),
            ("phase mismatch", "EXECUTION-STATUS.md", lambda text: text.replace("| P2M |", "| P99 |"), "Plan/ledger"),
            ("invalid ADR", "0001-local-then-remote-before-collaboration.md", lambda text: text.replace("status: accepted", "status: proposed"), "ADR requires"),
            ("outside checkout", "PRODUCT-CONTRACT.md", lambda text: text + "\n[outside](../../../../../outside.md)\n", "Link outside"),
            ("absolute source", "PRODUCT-CONTRACT.md", lambda text: text + "\n[local](/private/missing.py)\n", "Nonportable absolute"),
            ("unknown history", "HISTORICAL-FINDINGS.md", lambda text: text + "\nARCH:R999\n", "Unknown coverage"),
            ("duplicate feature", "TEST-MATRIX.md", lambda text: text + "\n| F01 duplicate | source | tests | bounds |\n", "Duplicate feature"),
            ("finding owner", "EXECUTION-STATUS.md", lambda text: text.replace("| ARCH:R1 Studio 保存 | P2M |", "| ARCH:R1 Studio 保存 | P99 |"), "Finding without valid phase owner"),
            ("navigation is not run evidence", "EXECUTION-STATUS.md", claim_verified_with_navigation, "requires a run record"),
        ]
        original_read = Path.read_text
        for label, name, edit, expected_error in cases:
            with self.subTest(label=label):
                def altered(path, *args, **kwargs):
                    text = original_read(path, *args, **kwargs)
                    return edit(text) if path.name == name else text

                with patch.object(Path, "read_text", altered):
                    errors = CHECKER.check(ROOT)["errors"]
                self.assertTrue(any(expected_error in error for error in errors), errors)

    def test_missing_source_test_is_rejected(self):
        missing = ROOT / "tests/test_reparse_revision_contract.py"
        original_is_file = Path.is_file
        with patch.object(Path, "is_file", lambda path: False if path == missing else original_is_file(path)):
            errors = CHECKER.check(ROOT)["errors"]
        self.assertTrue(any(missing.name in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
