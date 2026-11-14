import pytest

import audit_api as api

# ##################################################################
# started-after window
# Offline: a fake daemon page proves the cohort filter keeps only completed conversations created inside the window.
ROWS = [
    {
        "id": "conv-old",
        "completed": True,
        "created_at": "2026-11-10T02:00:00+00:00",
        "updated_at": "2026-11-10T05:00:00+00:00",
        "origin_ref": "",
    },
    {
        "id": "conv-new",
        "completed": True,
        "created_at": "2026-11-10T04:00:00+00:00",
        "updated_at": "2026-11-10T05:00:00+00:00",
        "origin_ref": "",
    },
    {
        "id": "conv-running",
        "completed": False,
        "created_at": "2026-11-10T04:30:00+00:00",
        "updated_at": "2026-11-10T05:00:00+00:00",
        "origin_ref": "",
    },
    {
        "id": "conv-sched",
        "completed": True,
        "created_at": "2026-11-10T04:10:00+00:00",
        "updated_at": "2026-11-10T05:00:00+00:00",
        "origin_ref": "sched:x",
    },
]


def fake_get(rows):
    def get(_api, path):
        assert path.startswith("/v1/conversations?")
        return {"conversations": rows, "has_more": False}

    return get


def test_started_after_keeps_only_completed_in_window(monkeypatch):
    monkeypatch.setattr(api, "get", fake_get(ROWS))
    selected = api.conversations("http://fake", 10, "", started_after="2026-11-10T03:11:00+00:00")
    assert [row["id"] for row in selected] == ["conv-new", "conv-sched"]


def test_no_window_keeps_all_completed(monkeypatch):
    monkeypatch.setattr(api, "get", fake_get(ROWS))
    selected = api.conversations("http://fake", 10, "")
    assert [row["id"] for row in selected] == ["conv-old", "conv-new", "conv-sched"]


def test_window_respects_origin_exclusion(monkeypatch):
    monkeypatch.setattr(api, "get", fake_get(ROWS))
    selected = api.conversations(
        "http://fake", 10, "", exclude_origins=("sched:x",), started_after="2026-11-10T01:00:00+00:00"
    )
    assert [row["id"] for row in selected] == ["conv-old", "conv-new"]


def test_malformed_created_at_fails_closed(monkeypatch):
    bad = [dict(ROWS[1], created_at="not-a-time")]
    monkeypatch.setattr(api, "get", fake_get(bad))
    with pytest.raises(ValueError, match="timezone-aware created_at"):
        api.conversations("http://fake", 10, "", started_after="2026-11-10T03:11:00+00:00")
