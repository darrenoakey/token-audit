#!/usr/bin/env bash
# One-command per-model time/token efficiency ranking for agentd3.
# Replaces the ~200k-token "Ranking models by time-token efficiency" recipe
# (145 bash calls, 72k polled events) with 1-2 API calls.
#
# Usage:
#   rank-models.sh [--days N] [--min-convs N] [--json]
#
# Data source: paginated GET /v1/conversations for ids+timestamps (the list
# view omits last_usage), then GET /v1/conversations/{id} for usage+cost of
# completed conversations in the window. No /v1/events polling.
#
# Env:
#   AGENTD3_API   base URL, default http://127.0.0.1:8620
#
# Exit codes:
#   0  table printed
#   1  API unreachable
set -euo pipefail

API="${AGENTD3_API:-http://127.0.0.1:8620}"
DAYS=30
MIN_CONVS=2
JSON=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) DAYS="$2"; shift 2 ;;
    --min-convs) MIN_CONVS="$2"; shift 2 ;;
    --json) JSON=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

python3 - "$API" "$DAYS" "$MIN_CONVS" "$JSON" <<'PY'
import json, sys, statistics, datetime, urllib.request

api, days, min_convs, want_json = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] == "1"

def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)

MAX_DETAILS = 500
convs, offset = [], 0
try:
    while True:
        page = get(f"{api}/v1/conversations?limit=200&offset={offset}")
        convs.extend(page.get("conversations", []))
        if not page.get("has_more"):
            break
        offset += len(page.get("conversations", [])) or 200
except Exception as e:
    print(f"ERROR: could not list conversations: {e}", file=sys.stderr)
    sys.exit(1)

cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

in_window = []
for c in convs:
    if not c.get("completed"):
        continue
    updated = parse_ts(c.get("updated_at"))
    if not updated or updated < cutoff:
        continue
    in_window.append(c)
in_window.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
in_window = in_window[:MAX_DETAILS]

groups = {}
for c in in_window:
    detail = get(f"{api}/v1/conversations/{c['id']}")
    lu = detail.get("last_usage")
    if not lu:
        continue
    created = parse_ts(c.get("created_at"))
    updated = parse_ts(c.get("updated_at"))
    model = c.get("effective_model") or c.get("model") or "unknown"
    dur_h = (updated - created).total_seconds() / 3600.0 if created else 0.0
    g = groups.setdefault(model, {"n": 0, "tok": 0, "cost": 0.0, "tok_list": [], "hours": []})
    g["n"] += 1
    g["tok"] += lu.get("totalTokens", 0)
    g["cost"] += (lu.get("cost") or {}).get("total", 0.0)
    g["tok_list"].append(lu.get("totalTokens", 0))
    if dur_h > 0:
        g["hours"].append(dur_h)

rows = []
for model, g in groups.items():
    if g["n"] < min_convs:
        continue
    med = statistics.median(g["tok_list"]) if g["tok_list"] else 0
    med_h = statistics.median(g["hours"]) if g["hours"] else 0.0
    eff = g["tok"] / med_h if med_h > 0 else 0  # tokens per wall-clock hour
    rows.append((model, g["n"], g["tok"], g["cost"], med, med_h, eff))

rows.sort(key=lambda r: r[6])  # most efficient (fewest tokens/hour) first

if want_json:
    print(json.dumps([
        {"model": m, "conversations": n, "total_tokens": t, "total_cost_usd": round(cst, 4),
         "median_tokens": int(md), "median_hours": round(mh, 2), "tokens_per_hour": int(eff)}
        for m, n, t, cst, md, mh, eff in rows], indent=1))
    sys.exit(0)

print(f"Per-model efficiency, last {days}d (completed convs with usage, >= {min_convs} convs; "
      f"fewest tokens/wall-clock-hour first)")
print(f"{'model':<22} {'convs':>5} {'total_tok':>12} {'total_$':>9} {'med_tok':>10} {'med_h':>7} {'tok/hr':>10}")
for m, n, t, cst, md, mh, eff in rows:
    print(f"{m:<22} {n:>5} {t:>12} {cst:>9.4f} {md:>10} {mh:>7.2f} {eff:>10.0f}")
PY
