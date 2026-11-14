import argparse
import json
import subprocess
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

import setproctitle

import audit_analysis as analysis
import audit_api as api


# ##################################################################
# usage projection
# Only documented numeric counters are exposed, with no inferred tool attribution.
def usage(value):
    value = value if isinstance(value, dict) else {}
    result = {
        key: value[key]
        for key in (
            "input",
            "output",
            "cacheRead",
            "cacheWrite",
            "reasoning",
            "totalTokens",
        )
        if isinstance(value.get(key), (int, float)) and not isinstance(value[key], bool)
    }
    if isinstance(value.get("cost"), dict) and isinstance(value["cost"].get("total"), (int, float)):
        result["cost_usd"] = value["cost"]["total"]
    return result


# ##################################################################
# one conversation
# Complete pagination is mandatory; result coverage is explicitly reported separately.
def inspect_conversation(base, conversation):
    detail = api.detail(base, conversation)
    if detail.get("completed") is not True:
        raise ValueError(f"Conversation {conversation} is not completed; select a stable completed conversation")
    events = api.events(base, conversation, detail["last_seq"])
    rows, issues, records = analysis.calls(conversation, events)
    usage_coverage = {
        "turn_usage_missing": issues.pop("turn_usage_missing", 0),
        "last_usage_missing": not bool(usage(detail.get("last_usage"))),
    }
    totals = {}
    for record in records:
        for key, value in usage(record["usage"]).items():
            totals[key] = totals.get(key, 0) + value
    return rows, {
        "id": conversation,
        "events": len(events),
        "snapshot_last_seq": detail["last_seq"],
        "calls": len(rows),
        "estimated_tokens": sum(row["estimated_tokens"] for row in rows),
        "coverage": issues,
        "usage_coverage": usage_coverage,
        "last_usage": usage(detail.get("last_usage")),
        "recorded_turn_usage_sum": totals,
        "recorded_usage_events": len(records),
        "stream_events_not_double_counted": sum(event["type"] == "tool.output" for event in events),
        "usage_evidence_events": [record["event"] for record in records[:5]],
    }


# ##################################################################
# audit report
# Return bounded ranked evidence rather than all fetched events or arbitrary titles.
def report(arguments):
    targets = (
        [{"id": arguments.conversation}]
        if arguments.conversation
        else api.conversations(
            arguments.api,
            arguments.count,
            arguments.skip,
            exclude_origins=arguments.exclude_origin,
            started_after=arguments.started_after,
        )
    )
    if not targets:
        raise ValueError("No completed conversations found; audit cannot establish an opportunity ranking")
    rows, summaries = [], []
    for target in targets:
        calls, summary = inspect_conversation(arguments.api, target["id"])
        rows.extend(calls)
        summaries.append(summary)
    partial = any(summary["coverage"] for summary in summaries)
    result = {
        "status": "partial" if partial else "complete",
        "requested_count": arguments.count,
        "sample_count": len(summaries),
        "sample_shortfall": len(targets) < arguments.count if not arguments.conversation else False,
        "method": "Estimated tokens = ceil((argument JSON chars + output content chars)/4), not billed tokens.",
        "limits": [
            "Recorded usage is separate: last_usage and sum of available turn.completed usage records; not full billing.",
            "No child-conversation spend attribution; archived conversations included in recent selection.",
            "Content envelopes counted once; images/binary/hidden outputs and provider context replay unmeasured.",
            "Nested wrapper calls can overlap; sequence windows are non-overlapping start-order triples, not causal traces.",
            "Candidate savings are unverified; inspect highest useful opportunity, including debugging/coding.",
        ],
        "conversations": summaries,
        "tools": analysis.ranking(rows, "tool", arguments.top),
        "operations": analysis.ranking(rows, "operation", arguments.top),
        "sequences": analysis.sequences(rows, arguments.top),
        "helper_gap": analysis.helper_gap(rows, arguments.top),
        "helper_adoption": analysis.helper_adoption(rows),
        "oversized_outputs": [
            analysis.reference(row)
            for row in sorted(
                (row for row in rows if row["oversized_output"]),
                key=lambda row: row["estimated_tokens"],
                reverse=True,
            )[: arguments.top]
        ],
    }
    if arguments.inspect:
        selected = next(
            (row for row in rows if row["start_event"] == arguments.inspect or row["end_event"] == arguments.inspect),
            None,
        )
        if selected is None:
            raise ValueError(f"No tool call references event {arguments.inspect} in {arguments.conversation}")
        result["inspection"] = selected
    return result


# ##################################################################
# bounded text
# Preserve numeric evidence and references while keeping routine reports compact.
def text_report(result):
    if "inspection" in result:
        # Drill-down prints only the selected call's evidence, never the whole sample report.
        row = result["inspection"]
        return "\n".join(
            [
                (
                    f"Inspection {row['conversation']}:{row['start_event']}->{row['end_event']} "
                    f"tool={row['tool']} operation={row['operation']} est={row['estimated_tokens']} "
                    f"input_chars={row['input_chars']} output_chars={row['output_chars']} "
                    f"oversized={row['oversized_output']} argument_fingerprint={row['argument_fingerprint']}"
                ),
                "Inspection (no raw payload): " + json.dumps(row, sort_keys=True),
                "Limit: Estimated tokens are a proxy, not billed usage; raw payloads are not printed.",
            ]
        )
    lines = [
        f"Tool hotspot audit: {result['status']} | completed conversations={result['sample_count']}",
        result["method"],
        "Coverage gaps make rankings provisional, never evidence of no useful work.",
    ]
    for row in result["conversations"]:
        lines.append(
            f"{row['id']} calls={row['calls']} est={row['estimated_tokens']} events={row['events']} "
            f"coverage={json.dumps(row['coverage'], sort_keys=True)} last_usage={json.dumps(row['last_usage'])} "
            f"recorded_turn_sum={json.dumps(row['recorded_turn_usage_sum'])} "
            f"usage_events={row['recorded_usage_events']} usage_coverage={json.dumps(row['usage_coverage'])}"
        )
    for section in ("tools", "operations", "sequences"):
        lines.append(f"Ranked {section} (observed payload estimates):")
        for index, row in enumerate(result[section], 1):
            evidence = ", ".join(
                f"{ref['conversation']}:{ref['start_event']}->{ref['end_event']}" for ref in row["evidence"]
            )
            lines.append(
                f" {index}. {row['label']} est={row['estimated_tokens']} calls={row['calls']} "
                f"convs={row['conversations']} repeated_inputs={row['identical_argument_repeats']} "
                f"large={row['oversized_outputs']} unknown={row['unknown_outputs']} evidence=[{evidence}]"
            )
            if section == "operations":
                lines.append("    Candidate: " + row["candidate"])
    lines.append("Raw shell reads with a bounded helper (proxy: output above helper budget, not billed):")
    for row in result["helper_gap"]:
        evidence = ", ".join(
            f"{ref['conversation']}:{ref['start_event']}->{ref['end_event']}" for ref in row["evidence"]
        )
        lines.append(
            f" {row['program']} -> {row['helper']} calls={row['calls']} convs={row['conversations']} "
            f"oversized={row['oversized_outputs']} avoidable_proxy={row['avoidable_tokens_proxy']} evidence=[{evidence}]"
        )
    adoption = result["helper_adoption"]
    lines.append(
        f"Helper adoption: bounded={adoption['bounded_total']} raw={adoption['raw_total']} "
        f"bounded_share={adoption['bounded_share']} bounded_calls={json.dumps(adoption['bounded_calls'], sort_keys=True)} "
        f"raw_calls={json.dumps(adoption['raw_calls'], sort_keys=True)}"
    )
    lines.append("Oversized outputs (>=16k chars): " + json.dumps(result["oversized_outputs"]))
    if "inspection" in result:
        lines.append("Inspection (no raw payload): " + json.dumps(result["inspection"], sort_keys=True))
    lines.extend("Limit: " + line for line in result["limits"])
    if result["sample_shortfall"]:
        lines.append("Sample shortfall: all available completed conversations returned, fewer than requested.")
    return "\n".join(lines)


# ##################################################################
# command arguments
# Explicit flags replace environment configuration and keep historical invocations working.
def options():
    parser = argparse.ArgumentParser(description="Rank real recent conversation tool payload hotspots safely.")
    parser.add_argument("action", nargs="?", choices=["check"], help="Run deterministic lint, format, and live tests")
    parser.add_argument("--api", default="http://127.0.0.1:8620")
    parser.add_argument("--skip", action="append", default=[], help="Exclude conversation ID; repeatable")
    parser.add_argument("--exclude-origin", action="append", default=[], help="Exclude origin_ref; repeatable")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument(
        "--started-after",
        default=None,
        help="Only completed conversations created at or after this timezone-aware ISO time, e.g. 2026-11-10T03:11:00+00:00",
    )
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--conversation",
        help="Inspect one completed conversation, including archived records",
    )
    parser.add_argument("--inspect", help="Safe drilldown: CONVERSATION_ID:START_OR_END_EVENT_SEQ")
    args = parser.parse_args()
    if not 1 <= args.count <= 50 or not 1 <= args.top <= 10:
        parser.error("--count must be 1..50 and --top must be 1..10")
    if args.started_after is not None:
        try:
            datetime.fromisoformat(args.started_after)
        except ValueError:
            parser.error("--started-after must be an ISO timestamp with a timezone offset")
        if datetime.fromisoformat(args.started_after).tzinfo is None:
            parser.error("--started-after must include a timezone offset")
    parsed = urllib.parse.urlsplit(args.api)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        parser.error("--api requires an HTTP(S) base URL without credentials")
    if args.inspect:
        try:
            conversation, event = args.inspect.rsplit(":", 1)
            args.inspect = int(event)
            if args.conversation and args.conversation != conversation:
                raise ValueError("conflicting conversation")
            args.conversation = conversation
        except ValueError:
            parser.error("--inspect requires CONVERSATION_ID:INTEGER_EVENT_SEQ matching --conversation")
    return args


# ##################################################################
# quality gate
# Resolve all commands against the script root so callers never need a particular directory.
def check():
    root = Path(__file__).resolve().parents[1]
    commands = [
        [sys.executable, "-m", "ruff", "check", "src", "run"],
        [sys.executable, "-m", "ruff", "format", "--check", "src", "run"],
        [sys.executable, "-m", "pytest", "-q"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode:
            return result.returncode
    return 0


# ##################################################################
# entry point
# API failures fail closed; missing tool results produce an explicit nonzero partial report.
def main():
    setproctitle.setproctitle("token-audit")
    arguments = options()
    if arguments.action == "check":
        return check()
    try:
        result = report(arguments)
    except (ValueError, TypeError, KeyError) as error:
        print(f"ERROR: audit incomplete: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True) if arguments.json else text_report(result))
    return 2 if result["status"] == "partial" else 0
