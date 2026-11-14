import importlib.machinery
import importlib.util
import subprocess
import tempfile
from pathlib import Path

LOADER = importlib.machinery.SourceFileLoader("md_context", str(Path.home() / "bin/md-context"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
md_context = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(md_context)

SCRIPT = Path.home() / "bin/md-context"


def invoke(query, file, *options):
    return subprocess.run(
        [str(SCRIPT), *options, query, str(file)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_caps_matched_section_and_reports_exact_omitted_lines():
    with tempfile.TemporaryDirectory() as directory:
        markdown = Path(directory) / "guide.md"
        markdown.write_text("# Guide\n" + "line of useful evidence\n" * 1200, encoding="utf-8")

        result = invoke("Guide", markdown, "--max-chars", "120")

        assert result.returncode == 0
        assert "[TRUNCATED: omitted " in result.stdout
        assert "omitted lines 6-1201" in result.stdout
        assert f"full section starts at {markdown}:1" in result.stdout
        assert len(result.stdout) < len(markdown.read_text(encoding="utf-8"))


def test_zero_limit_returns_complete_section():
    with tempfile.TemporaryDirectory() as directory:
        markdown = Path(directory) / "guide.md"
        content = "# Guide\n" + "line of useful evidence\n" * 1200
        markdown.write_text(content, encoding="utf-8")

        result = invoke("Guide", markdown, "--max-chars", "0")

        assert result.returncode == 0
        assert "[TRUNCATED:" not in result.stdout
        assert content.rstrip() in result.stdout


def test_index_lists_titles_without_bodies_for_focused_followup():
    with tempfile.TemporaryDirectory() as directory:
        markdown = Path(directory) / "guide.md"
        markdown.write_text("# Guide\nsecret body text\n## Setup\nmore secret text\n", encoding="utf-8")

        result = invoke("*", markdown, "--index")

        assert result.returncode == 0
        assert f"{markdown}:1 [Guide]" in result.stdout
        assert f"{markdown}:3 [Setup]" in result.stdout
        assert "secret body text" not in result.stdout
        assert "more secret text" not in result.stdout

        section = md_context.sections(markdown)[0]
        focused = invoke(section[1], markdown)
        assert focused.returncode == 0
        assert section[2] in focused.stdout


def test_missing_heading_remains_explicit_failure():
    with tempfile.TemporaryDirectory() as directory:
        markdown = Path(directory) / "guide.md"
        markdown.write_text("# Guide\ncontent\n", encoding="utf-8")

        result = invoke("Absent", markdown)

        assert result.returncode == 1
        assert "NO_MATCH" in result.stderr
