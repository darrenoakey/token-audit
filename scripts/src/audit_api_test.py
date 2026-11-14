import pytest

import audit_api as api

BASE = "http://127.0.0.1:8620"


# ##################################################################
# live pagination
# Small pages exercise actual offset and event cursor contracts against the daemon.
def test_live_pagination():
    recent = api.conversations(BASE, 2, "", page_size=2)
    assert len(recent) == 2
    assert recent[0]["updated_at"] >= recent[1]["updated_at"]
    skipped = api.conversations(BASE, 1, recent[0]["id"], page_size=2)
    assert skipped[0]["id"] == recent[1]["id"]
    detail = api.detail(BASE, recent[0]["id"])
    # Scale the small page so a 20k-event history is ~25 requests, not ~1,200; still multi-page.
    small_page = max(17, detail["last_seq"] // 25)
    small = api.events(BASE, detail["id"], detail["last_seq"], page_size=small_page)
    large = api.events(BASE, detail["id"], detail["last_seq"])
    assert small == large
    assert small[-1]["seq"] >= detail["last_seq"]
    with pytest.raises(ValueError, match="Incomplete events"):
        api.events(BASE, detail["id"], small[-1]["seq"] + 1)


# ##################################################################
# actual unavailable API
# A refused loopback endpoint and missing live identity must not return empty success.
def test_live_api_failures():
    with pytest.raises(ValueError, match="API request failed"):
        api.get("http://127.0.0.1:1", "/v1/conversations")
    with pytest.raises(ValueError, match="API request failed"):
        api.detail(BASE, "token-audit-nonexistent-conversation")
