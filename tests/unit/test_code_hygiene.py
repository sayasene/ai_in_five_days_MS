"""Static code hygiene checks: zero bare print(), zero hardcoded secrets, full docstrings."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from pursuit_auditor.tools import (
    audit_executive_aesthetic_and_density,
    certify_deliverable_for_external_release,
    fetch_authoritative_deal_baseline,
    inspect_commercial_posture_and_leak_guardrails,
    reconcile_financial_rollups_and_rate_cards,
    record_numbers_lineage_citation,
    request_human_waiver_signoff,
    retrieve_historical_audit_precedents,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "pursuit_auditor"

_SECRET_LITERAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"sk-[0-9A-Za-z]{24,}"),
    re.compile(r"ghp_[0-9A-Za-z]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
)


def test_zero_bare_print_calls_in_application_code() -> None:
    """Enforce structured JSON logging everywhere in `pursuit_auditor/`."""
    violations: list[str] = []
    for py_file in PACKAGE_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                violations.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno}")
    assert violations == [], f"Found prohibited bare print() calls: {violations}"


def test_zero_hardcoded_secrets_across_repository() -> None:
    """Ensure no real API keys or private keys exist anywhere in tracked source files."""
    violations: list[str] = []
    scan_dirs = [PACKAGE_DIR, REPO_ROOT / "deployment"]
    for directory in scan_dirs:
        if not directory.exists():
            continue
        for file_path in directory.rglob("*"):
            if not file_path.is_file() or file_path.suffix not in {".py", ".tf", ".tfvars", ".json", ".yml", ".yaml"}:
                continue
            text = file_path.read_text(encoding="utf-8")
            for pattern in _SECRET_LITERAL_PATTERNS:
                if pattern.search(text):
                    violations.append(str(file_path.relative_to(REPO_ROOT)))
    assert violations == [], f"Potential hardcoded secret detected in: {violations}"


def test_every_tool_has_comprehensive_docstring_and_descriptive_name() -> None:
    """Verify Rubric 1.1 & 1.2: specific snake_case tool names and Args/Returns docstrings."""
    tools = [
        fetch_authoritative_deal_baseline,
        reconcile_financial_rollups_and_rate_cards,
        inspect_commercial_posture_and_leak_guardrails,
        audit_executive_aesthetic_and_density,
        retrieve_historical_audit_precedents,
        record_numbers_lineage_citation,
        request_human_waiver_signoff,
        certify_deliverable_for_external_release,
    ]
    for fn in tools:
        doc = inspect.getdoc(fn) or ""
        assert len(fn.__name__.split("_")) >= 3, f"Tool name '{fn.__name__}' is too generic."
        assert "Args:" in doc, f"Tool '{fn.__name__}' docstring missing Args section."
        assert "Returns:" in doc, f"Tool '{fn.__name__}' docstring missing Returns section."
        assert "tool_context:" not in doc, (
            f"ADK best practice: omit `tool_context` from LLM docstring in '{fn.__name__}'."
        )
