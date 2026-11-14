import json
import subprocess
from pathlib import Path

import pytest

import audit
import audit_api as api

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8620"


# ##################################################################
# real historical entry point
# Exercise venv delegation, bounded JSON, targeted inspection, and actual API failures.
# The live CLI runs two full audits plus an inspect; each audit takes ~13s even on an
# idle machine, and CI-runner load on this host pushes the sequence past the global
# 30s pytest-timeout (measured load average 547-734 during a failing run). One live
# test, one explicit bound: 90s.
@pytest.mark.timeout(90)
def test_live_cli():
    completed = subprocess.run(
        [str(ROOT / "audit-conversations.sh"), "--count", "2", "--json"], check=False, capture_output=True, text=True
    )
    report = json.loads(completed.stdout)
    # Exit 2 is the documented provisional result: real recorded streams can carry coverage gaps.
    # The report must say so, and the exit code must match the status.
    gaps = {item["id"]: item["coverage"] for item in report["conversations"] if item["coverage"]}
    assert report["status"] == ("partial" if gaps else "complete")
    assert completed.returncode == (2 if gaps else 0), completed.stderr
    assert report["sample_count"] == 2
    assert len(report["operations"]) <= 5
    assert len(completed.stdout) < 40000
    evidence = report["operations"][0]["evidence"][0]
    reference = f"{evidence['conversation']}:{evidence['start_event']}"
    inspected = subprocess.run(
        [str(ROOT / "audit-conversations.sh"), "--inspect", reference, "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    # Inspect shares the report contract: a conversation with a recorded gap exits 2 (provisional).
    inspect_report = json.loads(inspected.stdout)
    inspect_gaps = {item["id"]: item["coverage"] for item in inspect_report["conversations"] if item["coverage"]}
    assert inspected.returncode == (2 if inspect_gaps else 0), inspected.stderr
    row = inspect_report["inspection"]
    assert row["start_event"] == evidence["start_event"]
    assert row["conversation"] == evidence["conversation"]
    assert "arguments" not in row and "output" not in row
    text_inspected = subprocess.run(
        [str(ROOT / "audit-conversations.sh"), "--conversation", evidence["conversation"], "--inspect", reference],
        check=False,
        capture_output=True,
        text=True,
    )
    assert text_inspected.returncode == inspected.returncode, text_inspected.stderr
    assert "Ranked tools" not in text_inspected.stdout and len(text_inspected.stdout) < 2000
    assert "raw payloads are not printed" in text_inspected.stdout
    failed = subprocess.run(
        [str(ROOT / "audit-conversations.sh"), "--api", "http://127.0.0.1:1"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
    assert not failed.stdout and "audit incomplete" in failed.stderr
    assert audit.usage({}) == {}
    assert "Ranked operations" in audit.text_report(report)


# ##################################################################
# live exclusion filters
# Multiple excluded ids and scheduled origins operate on real list records.
def test_live_exclusions():
    recent = api.conversations(BASE, 3, "")
    excluded = api.conversations(BASE, 1, [recent[0]["id"], recent[1]["id"]])
    assert excluded[0]["id"] == recent[2]["id"]
    origin = recent[0].get("origin_ref")
    if origin:
        selected = api.conversations(BASE, 2, [], exclude_origins=[origin])
        assert all(row.get("origin_ref") != origin for row in selected)
