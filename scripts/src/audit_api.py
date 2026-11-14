import json
import urllib.error
import urllib.parse
import urllib.request


# ##################################################################
# read response
# Keep API errors contextual without exposing response bodies or credentials.
def get(api, path):
    try:
        with urllib.request.urlopen(api.rstrip("/") + path, timeout=30) as response:
            value = json.load(response)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise ValueError(f"API request failed at {path.split('?')[0]} (HTTP {status})") from None
    except (OSError, ValueError) as error:
        raise ValueError(f"API request failed at {path.split('?')[0]} ({type(error).__name__})") from None
    if not isinstance(value, dict):
        raise TypeError(f"API object required at {path.split('?')[0]}")
    return value


# ##################################################################
# conversation selection
# Include archived records and validate ordering before selecting recent completions.
def started_at(row):
    # Fail closed: a row without a parseable created_at cannot be placed in a time window.
    from datetime import datetime

    stamp = row.get("created_at")
    try:
        moment = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise ValueError(f"Conversation {row.get('id')} has no timezone-aware created_at")
    return moment


def conversations(api, count, skip, page_size=100, exclude_origins=(), started_after=None):
    skipped = {skip} if isinstance(skip, str) else set(skip)
    cutoff = started_at({"created_at": started_after}) if started_after else None
    selected, seen, previous = [], set(), None
    offset = 0
    while offset < 100000:
        page = get(api, f"/v1/conversations?limit={page_size}&offset={offset}&sort=updated_at&order=desc")
        rows = page.get("conversations")
        if not isinstance(rows, list) or not isinstance(page.get("has_more"), bool):
            raise TypeError(f"Malformed conversation page at offset {offset}")
        for row in rows:
            if not isinstance(row, dict) or not row.get("id") or not row.get("updated_at"):
                raise ValueError(f"Malformed conversation row at offset {offset}")
            stamp = row["updated_at"]
            if row["id"] in seen or (previous is not None and stamp > previous):
                raise ValueError(f"Unstable conversation pagination at offset {offset}; retry audit")
            seen.add(row["id"])
            previous = stamp
            if (
                row.get("completed") is True
                and row["id"] not in skipped
                and row.get("origin_ref") not in exclude_origins
                and (cutoff is None or started_at(row) >= cutoff)
            ):
                selected.append(row)
        if len(selected) >= count or not page["has_more"]:
            return selected[:count]
        if not rows:
            raise ValueError(f"Empty conversation page with has_more at offset {offset}")
        offset += len(rows)
    raise ValueError(f"Conversation pagination exceeded safety bound at offset {offset}")


# ##################################################################
# conversation detail
# Explicit ids are quoted and identity checked before any event analysis.
def detail(api, conversation):
    row = get(api, "/v1/conversations/" + urllib.parse.quote(conversation, safe=""))
    if row.get("id") != conversation or not isinstance(row.get("last_seq"), int):
        raise ValueError(f"Malformed conversation detail for {conversation}")
    return row


# ##################################################################
# event pagination
# Drain to an empty page and prove that the captured high-water mark was reached.
def events(api, conversation, last_seq, page_size=1000):
    after, rows = 0, []
    path = "/v1/conversations/" + urllib.parse.quote(conversation, safe="") + "/events"
    while len(rows) <= 200000:
        page = get(api, f"{path}?limit={page_size}&after={after}")
        chunk, cursor = page.get("events"), page.get("next_after")
        if page.get("conversation_id") != conversation or not isinstance(chunk, list) or not isinstance(cursor, int):
            raise ValueError(f"Malformed event page for {conversation} after {after}")
        if not chunk:
            if cursor != after:
                raise ValueError(f"Invalid empty-page cursor for {conversation} after {after}")
            if after < last_seq:
                raise ValueError(f"Incomplete events for {conversation}: reached {after}, expected {last_seq}")
            return rows
        for event in chunk:
            if not isinstance(event, dict) or not isinstance(event.get("seq"), int) or event["seq"] != after + 1:
                raise ValueError(f"Non-increasing event sequence for {conversation} after {after}")
            if event.get("conversation_id") != conversation or not isinstance(event.get("type"), str):
                raise ValueError(f"Malformed event identity for {conversation} after {after}")
            after = event["seq"]
            rows.append(event)
        if cursor != after:
            raise ValueError(f"Invalid next_after for {conversation} at {after}")
    raise ValueError(f"Event pagination exceeded safety bound for {conversation} at {after}")
