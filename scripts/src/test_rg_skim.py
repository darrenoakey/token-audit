import os
import subprocess
from pathlib import Path

TOOL = Path.home() / "bin" / "rg-skim"


def test_rg_skim_bounds_matches_and_reports_totals(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("needle " + "x" * 400 + "\n" + "needle short\n" + "other\n", encoding="utf-8")
    result = subprocess.run(
        [str(TOOL), "needle", str(tmp_path), "--max-matches", "1", "--max-line-chars", "30"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "MATCHES 2 across 1 files" in result.stdout
    assert "FILE " + str(source) + ": 2" in result.stdout
    assert "[line clipped]" in result.stdout
    assert "PREVIEW_TRUNCATED 1 matches omitted" in result.stdout


def test_rg_skim_preserves_no_match_and_error_status(tmp_path):
    no_match = subprocess.run([str(TOOL), "absent-needle", str(tmp_path)], capture_output=True, text=True, check=False)
    assert no_match.returncode == 1
    assert no_match.stdout.strip() == "NO_MATCH"

    missing = subprocess.run(
        [str(TOOL), "needle", str(tmp_path / "missing")], capture_output=True, text=True, check=False
    )
    assert missing.returncode == 2
    assert missing.stderr


def test_rg_skim_defaults_to_installed_executable():
    assert TOOL.is_file()
    assert os.access(TOOL, os.X_OK)
