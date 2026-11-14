import json

import audit_analysis as analysis
import audit_api as api

BASE = "http://127.0.0.1:8620"


def test_psql_is_labelled_raw_and_db_skim_counts_as_bounded_adoption():
    # Raw psql was classified shell:other (hidden from the helper gap); it must rank as the raw query route,
    # and db-skim must count as bounded adoption.
    assert analysis.operation("bash", json.dumps({"command": "psql -X -d praxis -c 'SELECT 1'"})).startswith(
        "shell:psql"
    )
    rows = [
        {"tool": "bash", "operation": "shell:db-skim operation"},
        {"tool": "bash", "operation": "shell:psql operation"},
    ]
    adoption = analysis.helper_adoption(rows)
    assert adoption["bounded_calls"] == {"db-skim": 1}
    assert adoption["raw_calls"] == {"psql": 1}


# ##################################################################
# range options live only on the request
# Different offsets of one file are different calls; identical requests are still repeats.
def test_request_arguments_keep_range_options_in_fingerprint():
    def requested(seq, call_id, args):
        return {
            "seq": seq,
            "type": "tool.requested",
            "data": {"tool_call_id": call_id, "name": "read", "arguments": json.dumps(args)},
        }

    def started(seq, call_id):
        return {
            "seq": seq,
            "type": "tool.started",
            "data": {"tool_call_id": call_id, "name": "read", "arguments": {"path": "f"}},
        }

    def completed(seq, call_id):
        return {"seq": seq, "type": "tool.completed", "data": {"tool_call_id": call_id, "output": "x"}}

    events = [
        requested(1, "a", {"path": "f", "offset": 1, "limit": 5}),
        started(2, "a"),
        completed(3, "a"),
        requested(4, "b", {"path": "f", "offset": 6, "limit": 5}),
        started(5, "b"),
        completed(6, "b"),
        requested(7, "c", {"path": "f", "offset": 1, "limit": 5}),
        started(8, "c"),
        completed(9, "c"),
    ]
    rows, issues, _ = analysis.calls("c1", events)
    assert not issues and len(rows) == 3
    by_start = {row["start_event"]: row for row in rows}
    assert by_start[2]["argument_fingerprint"] != by_start[5]["argument_fingerprint"]
    assert by_start[2]["argument_fingerprint"] == by_start[8]["argument_fingerprint"]


# ##################################################################
# requested/start duplicate handling
# Pig's transcript event is redundant only when the actual start agrees.
def test_requested_duplicate_is_not_double_counted_and_mismatch_is_visible():
    start = {
        "seq": 2,
        "type": "tool.started",
        "data": {"tool_call_id": "one", "name": "read", "arguments": {"path": "a"}},
    }
    request = {"seq": 1, "type": "tool.requested", "data": start["data"]}
    end = {"seq": 3, "type": "tool.completed", "data": {"tool_call_id": "one", "output": "contents"}}
    rows, issues, _ = analysis.calls("c1", [request, start, end])
    assert len(rows) == 1 and not issues
    changed = {
        "seq": 1,
        "type": "tool.requested",
        "data": {"tool_call_id": "one", "name": "read", "arguments": {"path": "b"}},
    }
    _, issues, _ = analysis.calls("c1", [changed, start, end])
    assert issues["request_start_mismatch"] == 1
    _, issues, _ = analysis.calls("c1", [request, end])
    assert issues["orphan_requests"] == 1


# ##################################################################
# string-encoded arguments
# Real daemon events carry tool arguments as JSON text; labels must still be derived from them.
def test_json_string_arguments_are_labelled_by_command_and_path():
    assert analysis.operation("bash", json.dumps({"command": "git status --short"})) == "shell:git status"
    assert analysis.operation("bash", json.dumps({"command": "git-skim --cached"})) == "shell:git-skim operation"
    assert analysis.operation("bash", json.dumps({"command": "grep -rn x src"})) == "shell:grep operation"
    assert analysis.operation("read", json.dumps({"path": "docs/a.md"})) == "read:md"
    assert analysis.operation("read", {"path": "docs/a.md"}) == "read:md"
    assert analysis.operation("bash", "{not json") == "shell:other operation"


def test_batch_reads_are_labelled_read_batch_not_read_other():
    # The taught read route is one paths[] batch per survey; its aggregate cost must be
    # ranked as read:batch, not mislabelled read:other and hidden from drill-down.
    assert (
        analysis.operation("read", json.dumps({"paths": [{"path": "a"}, {"path": "b"}], "max_chars": 12000}))
        == "read:batch"
    )
    assert analysis.operation("read", {"paths": [{"path": "a"}]}) == "read:batch"
    assert analysis.operation("read", json.dumps({"path": "a.md"})) == "read:md"
    assert analysis.operation("read", json.dumps({"path": "a.unknown"})) == "read:other"


# ##################################################################
# helper adoption
# Bounded helper calls and their raw equivalents are counted separately, without payload.
def test_gh_ci_polls_are_labelled_and_counted_against_gh_ci_skim():
    rows = [
        {"tool": "bash", "operation": "shell:gh run view"},
        {"tool": "bash", "operation": "shell:gh pr view"},
        {"tool": "bash", "operation": "shell:gh release list"},
        {"tool": "bash", "operation": "shell:gh-ci-skim operation"},
        {"tool": "bash", "operation": "shell:gh api"},
    ]
    adoption = analysis.helper_adoption(rows)
    assert adoption["bounded_calls"] == {"gh-ci-skim": 1}
    assert adoption["raw_calls"] == {"gh run view": 1, "gh pr view": 1, "gh release list": 1}


def test_helper_adoption_counts_bounded_and_raw_shell_calls():
    rows = [
        {"tool": "bash", "operation": "shell:file-skim operation"},
        {"tool": "bash", "operation": "shell:rg-skim operation"},
        {"tool": "bash", "operation": "shell:cat operation"},
        {"tool": "bash", "operation": "shell:agentd3-cli memory"},
        {"tool": "bash", "operation": "shell:ssh operation"},
        {"tool": "bash", "operation": "shell:other operation"},
        {"tool": "grep", "operation": "grep"},
    ]
    adoption = analysis.helper_adoption(rows)
    assert adoption["bounded_calls"] == {"file-skim": 1, "rg-skim": 1}
    assert adoption["raw_calls"] == {"agentd3-cli memory": 1, "cat": 1, "ssh": 1}
    assert adoption["bounded_total"] == 2 and adoption["raw_total"] == 3
    assert adoption["bounded_share"] == 0.4
    assert analysis.helper_adoption([])["bounded_share"] is None


def test_compound_command_is_attributed_to_its_payload_segment_not_memory_write():
    def label(command):
        return analysis.operation("bash", json.dumps({"command": command}))

    # The memory write is mandated bookkeeping; the cat segment produces the payload.
    assert label("cd /w\nagentd3-cli memory add $ID action 'x'; cat notes.md") == "shell:cat operation"
    assert label("agentd3-cli memory add $ID action 'x' && df -h") == "shell:other operation"
    assert label("agentd3-cli memory add $ID action 'x'") == "shell:agentd3-cli memory write"
    assert (
        label("agentd3-cli memory init $ID && agentd3-cli memory add $ID state 'y'") == "shell:agentd3-cli memory write"
    )
    assert label("cd /w && git status") == "shell:git status"
    assert label("cd /w") == "shell:other operation"
    assert label("agentd3-cli memory show $ID") == "shell:agentd3-cli memory"


def test_memory_writes_are_not_counted_as_raw_memory_reads():
    rows = [
        {"tool": "bash", "operation": "shell:agentd3-cli memory write"},
        {"tool": "bash", "operation": "shell:agentd3-cli memory"},
    ]
    adoption = analysis.helper_adoption(rows)
    assert adoption["raw_calls"] == {"agentd3-cli memory": 1}
    assert adoption["bounded_total"] == 0


def test_memory_digest_is_bounded_and_memory_show_stays_raw():
    digest = json.dumps({"command": "agentd3-cli memory digest conv-abc --seq 1-4"})
    show = json.dumps({"command": "agentd3-cli memory show conv-abc"})
    assert analysis.operation("bash", digest) == "shell:agentd3-cli memory digest"
    assert analysis.operation("bash", show) == "shell:agentd3-cli memory"
    rows = [
        {"tool": "bash", "operation": "shell:agentd3-cli memory digest"},
        {"tool": "bash", "operation": "shell:agentd3-cli memory"},
    ]
    adoption = analysis.helper_adoption(rows)
    assert adoption["bounded_calls"] == {"agentd3-cli memory digest": 1}
    assert adoption["raw_calls"] == {"agentd3-cli memory": 1}


def test_git_skim_is_counted_against_read_only_git_calls():
    rows = [
        {
            "tool": "bash",
            "operation": operation,
            "output_chars": 100,
            "conversation": "test",
            "oversized_output": False,
            "start_event": 1,
            "end_event": 2,
            "estimated_tokens": 25,
        }
        for operation in (
            "shell:git status",
            "shell:git diff",
            "shell:git commit",
            "shell:git-skim status",
            "shell:git-skim diff",
        )
    ]
    adoption = analysis.helper_adoption(rows)
    assert adoption["bounded_calls"] == {"git-skim": 2}
    assert adoption["raw_calls"] == {"git": 2}
    assert adoption["bounded_share"] == 0.5
    gaps = analysis.helper_gap(rows, 10)
    assert [(item["program"], item["calls"]) for item in gaps] == [("git", 2)]


# ##################################################################
# live analysis
# Cross-check real result envelopes and every ranked total against the fetched events.
def test_live_analysis_and_missing_result_coverage():
    # Select a real conversation containing an MCP content envelope, rather
    # than assuming whichever conversation finished most recently has one.
    # Content envelopes are older than the newest runs, so search a wide recent window.
    events, rows, records = [], [], []
    for candidate in api.conversations(BASE, 100, ""):
        detail = api.detail(BASE, candidate["id"])
        events = api.events(BASE, candidate["id"], detail["last_seq"])
        rows, issues, records = analysis.calls(candidate["id"], events)
        if any(row["output_basis"] == "content" for row in rows):
            break
    assert rows and records
    tool_issues = {key: value for key, value in issues.items() if key != "turn_usage_missing"}
    # Real streams can hold calls that never got a terminal event (interrupted turns). The
    # audit must report exactly those, not hide them and not invent others.
    terminal_ids = {event["data"].get("tool_call_id") for event in events if event["type"] in analysis.TERMINALS}
    unpaired = sum(
        1
        for event in events
        if event["type"] == "tool.started" and event["data"].get("tool_call_id") not in terminal_ids
    )
    assert set(tool_issues) <= {"missing_terminal"}, tool_issues
    assert tool_issues.get("missing_terminal", 0) == unpaired, (tool_issues, unpaired)
    assert len(rows) == sum(event["type"] == "tool.started" for event in events)
    groups = analysis.ranking(rows, "tool", 100)
    assert sum(group["estimated_tokens"] for group in groups) == sum(row["estimated_tokens"] for row in rows)
    assert groups == sorted(groups, key=lambda row: (-row["estimated_tokens"], row["label"]))
    row = next(row for row in rows if row["output_basis"] == "content")
    end = next(event for event in events if event["seq"] == row["end_event"])
    decoded = json.loads(end["data"]["output"])
    assert row["output_chars"] == sum(analysis.size(block.get("text", block)) for block in decoded["content"])
    retained = [event for event in events if event["seq"] != row["end_event"]]
    incomplete, coverage, _ = analysis.calls(row["conversation"], retained)
    # The removed terminal adds one gap on top of the real unpaired calls already in the stream.
    assert coverage["missing_terminal"] == unpaired + 1
    assert next(item for item in incomplete if item["start_event"] == row["start_event"])["output_chars"] is None
    for group in analysis.sequences(rows, 5):
        assert group["calls"] == group["windows"] * 3
        assert len(group["evidence"]) <= 3


# ##################################################################
# live input privacy
# Whitelisted operation labels must never contain serialized arguments or transcript text.
def test_live_operation_privacy():
    conversation = api.conversations(BASE, 1, "")[0]["id"]
    detail = api.detail(BASE, conversation)
    events = api.events(BASE, conversation, detail["last_seq"])
    rows, _, _ = analysis.calls(conversation, events)
    for row in rows:
        assert len(row["argument_fingerprint"]) == 12
        assert "input" not in analysis.reference(row)
        assert "output" not in analysis.reference(row)
        assert row["operation"] == row["tool"] or row["operation"].startswith(("read:", "shell:"))


# ##################################################################
# batch read adoption
# paths[] batch reads are the taught route; the audit must be able to prove agents use it.
def test_batch_reads_are_counted_as_bounded_adoption():
    rows = [
        {"tool": "bash", "operation": "shell:file-skim operation"},
        {"tool": "read", "operation": "read:batch"},
        {"tool": "read", "operation": "read:md"},
    ]
    adoption = analysis.helper_adoption(rows)
    assert adoption["bounded_calls"] == {"file-skim": 1, "read:batch": 1}
    assert adoption["bounded_total"] == 2
    assert adoption["raw_calls"] == {}  # read:md is neither a helper call nor a raw shell read


# ##################################################################
# helper gap
# Only raw shell reads with a bounded helper count, and the proxy excludes the helper budget.
def test_helper_gap_counts_only_helper_eligible_raw_reads():
    def row(operation, output_chars, tool="bash", conversation="c1", start=1):
        return {
            "conversation": conversation,
            "start_event": start,
            "end_event": start + 1,
            "tool": tool,
            "operation": operation,
            "output_chars": output_chars,
            "oversized_output": (output_chars or 0) >= 16000,
            "estimated_tokens": (output_chars or 0) // 4,
        }

    rows = [
        row("shell:cat operation", 12000, start=1),
        row("shell:cat operation", 3000, start=3),
        row("shell:ssh operation", 65000, conversation="c2", start=5),
        row("shell:python operation", 65000, start=7),
        row("read:py", 9000, tool="read", start=9),
        row("shell:cat operation", None, start=11),
    ]
    gap = {item["program"]: item for item in analysis.helper_gap(rows, 10)}
    assert set(gap) == {"cat", "ssh"}
    assert gap["cat"]["calls"] == 2
    assert gap["cat"]["avoidable_tokens_proxy"] == (12000 - 4000) // 4
    assert gap["ssh"]["helper"] == "shell-skim or remote-probe"
    assert gap["ssh"]["conversations"] == 1 and gap["ssh"]["oversized_outputs"] == 1
    assert [item["program"] for item in analysis.helper_gap(rows, 10)] == ["ssh", "cat"]


def test_raw_curl_is_counted_against_web_skim():
    rows = [
        {
            "conversation": "c1",
            "start_event": 1,
            "end_event": 2,
            "tool": "bash",
            "operation": "shell:curl operation",
            "output_chars": 65000,
            "oversized_output": True,
            "estimated_tokens": 16250,
        },
    ]
    gap = {item["program"]: item for item in analysis.helper_gap(rows, 10)}
    assert gap["curl"]["helper"] == "web-skim"
    assert gap["curl"]["calls"] == 1
    assert gap["curl"]["avoidable_tokens_proxy"] == (65000 - 4000) // 4


# ##################################################################
# bounded git forms
# git diff --stat and path-scoped git diff are the shapes the git guidance teaches; they must
# count as bounded adoption, while a full diff/show/log stays a raw, rankable read.
def test_bounded_git_diff_forms_are_labelled_and_counted_as_adoption():
    assert analysis.operation("bash", json.dumps({"command": "git diff --stat HEAD~3 HEAD"})) == "shell:git diff --stat"
    assert analysis.operation("bash", json.dumps({"command": "git diff -- src/a.go"})) == "shell:git diff -- PATH"
    assert analysis.operation("bash", json.dumps({"command": "git diff HEAD~3"})) == "shell:git diff"
    rows = [
        {"tool": "bash", "operation": "shell:git diff --stat"},
        {"tool": "bash", "operation": "shell:git diff -- PATH"},
        {"tool": "bash", "operation": "shell:git diff"},
        {"tool": "bash", "operation": "shell:git show"},
    ]
    adoption = analysis.helper_adoption(rows)
    assert adoption["bounded_calls"] == {"git": 2}
    assert adoption["raw_calls"] == {"git": 2}
    assert adoption["bounded_share"] == 0.5


def test_bounded_git_diff_forms_are_not_ranked_as_avoidable_raw_output():
    def row(operation, start):
        return {
            "tool": "bash",
            "operation": operation,
            "output_chars": 9000,
            "conversation": "t",
            "oversized_output": True,
            "start_event": start,
            "end_event": start + 1,
            "estimated_tokens": 2250,
        }

    bounded_only = analysis.helper_gap([row("shell:git diff --stat", 1), row("shell:git diff -- PATH", 3)], 10)
    assert bounded_only == []
    mixed = analysis.helper_gap([row("shell:git diff --stat", 1), row("shell:git diff", 3)], 10)
    assert [gap["program"] for gap in mixed] == ["git"]
    assert mixed[0]["calls"] == 1
    assert mixed[0]["helper"] == "git-skim diff|log|show (bounded; drill-down named in output)"
    assert "git-skim" in mixed[0]["helper"]


# ##################################################################
# request and start pairing
# Start events are a reduced daemon view: dropped keys are normalization, changed values are mismatches.
def test_same_call_accepts_text_requests_and_reduced_start_arguments():
    request = {"name": "read", "arguments": json.dumps({"path": "a.md", "max_chars": 0, "timeout": 5})}
    assert analysis.same_call(request, {"name": "read", "arguments": {"path": "a.md"}})
    assert not analysis.same_call(request, {"name": "read", "arguments": {"path": "b.md"}})
    assert not analysis.same_call(request, {"name": "grep", "arguments": {"path": "a.md"}})
    assert not analysis.same_call(request, {"name": "read", "arguments": {"path": "a.md", "extra": 1}})


# ##################################################################
# quoted remote commands
# A separator inside quotes belongs to the remote program. The audited ssh calls were
# split at their own `;` and `|` and labelled `shell:head`, hiding 208 remote calls.
def test_ssh_with_quoted_separators_is_one_remote_segment():
    command = "ssh darren@10.0.0.254 'free -h; cat /proc/meminfo | head -5'"
    assert analysis.operation("bash", json.dumps({"command": command})) == "shell:ssh operation"
    assert analysis.segments(command) == [["ssh", "darren@10.0.0.254", "free -h; cat /proc/meminfo | head -5"]]


def test_wrapped_ssh_and_helper_forms_keep_their_own_label():
    assert analysis.operation("bash", json.dumps({"command": "timeout 60 ssh spark 'df -h'"})) == "shell:ssh operation"
    assert (
        analysis.operation("bash", json.dumps({"command": "shell-skim -- ssh spark 'tail -5 x.log'"}))
        == "shell:shell-skim operation"
    )
    assert (
        analysis.operation("bash", json.dumps({"command": "remote-probe spark -p 'DF::df -h'"}))
        == "shell:remote-probe operation"
    )


def test_separators_outside_quotes_still_split_local_pipelines():
    assert analysis.operation("bash", json.dumps({"command": "git log -5 | head -3"})) == "shell:git log"
    assert (
        analysis.operation("bash", json.dumps({"command": "printf 'a;b' && grep -n x f.txt"})) == "shell:grep operation"
    )
    assert analysis.segments('echo "x;y" ; tail -n 2 a.log') == [["echo", "x;y"], ["tail", "-n", "2", "a.log"]]


def test_image_content_blocks_count_as_placeholder_not_base64_text():
    import audit_analysis as analysis

    image = "A" * 60000
    payload = json.dumps(
        {
            "content": [
                {"type": "text", "text": "Read image file [image/png]"},
                {"type": "image", "data": image, "mimeType": "image/png"},
            ]
        }
    )
    chars, basis = analysis.output_size(payload)
    assert basis == "content"
    assert chars == len("Read image file [image/png]") + analysis.IMAGE_PLACEHOLDER_CHARS
    assert chars < 100


def test_truncated_recorded_image_output_strips_base64_payload():
    import audit_analysis as analysis

    raw = (
        '{"content":[{"type":"text","text":"Read image file [image/png]"},'
        '{"type":"image","data":"' + "QUJD" * 5000 + '","mimeType":"image/png"}]'
        " ... [tool output truncated: 99999 bytes]"
    )
    chars, basis = analysis.output_size(raw)
    assert basis == "text"
    assert chars < 200, chars
