import collections
import hashlib
import json
import math
import re
import shlex

TERMINALS = {"tool.completed", "tool.finished", "tool.failed"}
COMMANDS = {
    "git",
    "gh",
    "curl",
    "python",
    "python3",
    "pytest",
    "ruff",
    "go",
    "rg",
    "grep",
    "find",
    "ls",
    "cat",
    "sed",
    "head",
    "tail",
    "wc",
    "jq",
    "ssh",
    "auto",
    "greenline",
    "agentd3-cli",
    "capabilities",
    "npm",
    "make",
    # bounded helpers: counting them makes adoption measurable in the same ranking
    "file-skim",
    "rg-skim",
    "shell-skim",
    "remote-probe",
    "gh-skim",
    "gh-ci-skim",
    "md-context",
    "memory-digest",
    "web-skim",
    "git-skim",
    "db-skim",
    # raw psql is the unbounded query route that db-skim replaces; label it so the gap is ranked
    "psql",
}
SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "test",
    "build",
    "check",
    "vet",
    "list",
    "version",
    "doctor",
    "memory",
    "ps",
    "find",
    "run",
    "inspect",
    "describe",
    "get",
}


# ##################################################################
# serialized size
# A character proxy is deliberately independent of provider billing or tokenizers.
def size(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(text)


# ##################################################################
# result content
# Prefer one visible content representation over duplicated structured envelopes.
IMAGE_PLACEHOLDER = "[image]"
IMAGE_PLACEHOLDER_CHARS = len(IMAGE_PLACEHOLDER)
# Matches the serialized image block prefix (escaped or plain) and its base64 payload.
IMAGE_DATA = re.compile(
    r'(\\?"type\\?"\s*:\s*\\?"image\\?".{0,200}?\\?"data\\?"\s*:\s*\\?")[A-Za-z0-9+/=]{64,}', re.DOTALL
)


def content_block_size(block):
    if isinstance(block, dict) and block.get("type") == "image":
        return IMAGE_PLACEHOLDER_CHARS
    return size(block.get("text", block))


def output_size(value):
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            # Recorded outputs can be truncated, so they are not valid JSON; still strip
            # embedded image payloads so base64 pixels are not counted as text.
            return len(IMAGE_DATA.sub(IMAGE_PLACEHOLDER, value)), "text"
    else:
        decoded = value
    if isinstance(decoded, dict) and isinstance(decoded.get("content"), list):
        # Image blocks carry base64 pixel data that is not text context; the model is
        # charged per image, not per base64 character, so count a fixed placeholder.
        return sum(content_block_size(block) for block in decoded["content"]), "content"
    return size(value), "serialized"


# ##################################################################
# operation label
# Emit only allowlisted command words, never arbitrary shell arguments or file names.
# The daemon records tool.started arguments as a JSON-encoded string; decode it first,
# otherwise every bash/read call is mislabelled shell:other / read:other.
def operation(name, arguments):
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    leaf = name.split(".")[-1]
    if leaf in {"bash", "run", "exec_command"}:
        command = arguments.get("command", arguments.get("cmd", "")) if isinstance(arguments, dict) else ""
        parts = segments(command) if isinstance(command, str) else []
        payload = [
            part for part in parts if not is_memory_write(part) and part[0].rsplit("/", 1)[-1] not in SHELL_BUILTINS
        ]
        if not payload and any(is_memory_write(part) for part in parts):
            return "shell:agentd3-cli memory write"
        words = payload_segment(payload)
        program = next(
            (word.rsplit("/", 1)[-1] for word in words if word.rsplit("/", 1)[-1] in COMMANDS),
            "other",
        )
        subcommand = next((word for word in words if word in SUBCOMMANDS), "operation")
        if program == "agentd3-cli" and subcommand == "memory":
            # `memory digest` is the bounded continuity view; keep it distinct from raw `memory show`.
            index = words.index("memory")
            if index + 1 < len(words) and words[index + 1] == "digest":
                return "shell:agentd3-cli memory digest"
        if program == "gh" and len(words) >= 3:
            # `gh run view`, `pr view`, `release list`: the CI and release polls that gh-ci-skim replaces.
            return f"shell:gh {words[1]} {words[2]}"
        if program == "git" and subcommand == "diff":
            # The bounded git diff forms carry their shape in the label, so the audit can
            # count them as adoption and never rank them as avoidable raw output.
            if "--stat" in words:
                return "shell:git diff --stat"
            if "--" in words:
                return "shell:git diff -- PATH"
        return f"shell:{program} {subcommand}"
    if leaf == "read":
        # Batch reads (paths[]) are the route the read guidance teaches; labelling them
        # separately ranks their aggregate cost instead of hiding it in read:other.
        if isinstance(arguments, dict) and "paths" in arguments:
            return "read:batch"
        path = arguments.get("path", "") if isinstance(arguments, dict) else ""
        extension = path.rsplit(".", 1)[-1] if isinstance(path, str) and "." in path else "other"
        return "read:" + (extension if extension in {"md", "py", "go", "json", "toml", "log", "txt", "sh"} else "other")
    return name


# ##################################################################
# argument comparison
# The daemon stores tool.requested arguments as JSON text but tool.started as an object;
# comparing raw forms flagged every pair. Decode text before comparing contents.
def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# Start events omit some request keys (timeouts, paging and budget options); those are a
# daemon-side view, not a change. Any key present only on start, or with a different value, is a mismatch.
def same_call(request, start):
    if request.get("name") != start.get("name"):
        return False
    expected, observed = decoded(request.get("arguments")), decoded(start.get("arguments"))
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return expected == observed
    return all(key in expected and expected[key] == value for key, value in observed.items())


# ##################################################################
# tool call pairing
# Missing, duplicate, and unfamiliar records remain visible as coverage failures.
def calls(conversation, events):
    starts, ends, issues = {}, {}, collections.Counter()
    requests, paired = {}, {}
    streams = set()
    usage = []
    for event in events:
        data = event.get("data")
        if event["type"].startswith("tool.") and not isinstance(data, dict):
            issues["malformed_tool_data"] += 1
            continue
        if event["type"] == "turn.completed":
            if isinstance(data, dict) and isinstance(data.get("usage"), dict):
                usage.append({"event": event["seq"], "usage": data["usage"]})
            else:
                issues["turn_usage_missing"] += 1
        elif event["type"] == "tool.started":
            key = data.get("tool_call_id")
            if not key or key in starts:
                issues["missing_or_duplicate_start_id"] += 1
                key = f"unpaired:{event['seq']}"
            starts[key] = event
            request = requests.pop(key, None)
            if request is not None and not same_call(request["data"], data):
                issues["request_start_mismatch"] += 1
            elif request is not None:
                # The agreed request carries the full arguments (offset, limit, max_chars).
                paired[key] = request
        elif event["type"] == "tool.requested":
            # Pig stores this transcript event before tool.started with the
            # same identity and arguments. Pair it without double-counting;
            # unmatched, duplicated or changed requests remain visible.
            key = data.get("tool_call_id")
            if not key:
                issues["missing_request_id"] += 1
            elif key in requests or key in starts:
                issues["duplicate_request"] += 1
            else:
                requests[key] = event
        elif event["type"] in TERMINALS:
            key = data.get("tool_call_id")
            if not key or key in ends:
                issues["missing_or_duplicate_terminal_id"] += 1
                key = f"unpaired:{event['seq']}"
            ends[key] = event
        elif event["type"] == "tool.output":
            streams.add(data.get("tool_call_id"))
        elif event["type"].startswith("tool."):
            issues["unknown_tool_event:" + event["type"]] += 1
    issues["orphan_stream_ids"] += len(streams - starts.keys())
    issues["orphan_requests"] += len(requests)
    result = [call(conversation, start, ends.pop(key, None), issues, paired.get(key)) for key, start in starts.items()]
    issues["orphan_terminals"] += len(ends)
    return result, {key: value for key, value in issues.items() if value}, usage


# ##################################################################
# one call
# Store references and counts only so audit reports cannot leak tool payloads.
def call(conversation, start, end, issues, request=None):
    data = start["data"]
    name = data.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[\w.:-]{1,150}", name):
        name = "unknown_tool"
        issues["unknown_tool_name"] += 1
    arguments = data.get("arguments")
    if request is not None:
        # Start events omit range options; the agreed request is the full argument set,
        # so the fingerprint and input size must come from it (same_call verified the overlap).
        requested = decoded(request["data"].get("arguments"))
        if requested is not None:
            arguments = requested
    if arguments is None:
        issues["missing_arguments"] += 1
    output, basis = None, "missing"
    if end is None:
        issues["missing_terminal"] += 1
    else:
        terminal = end["data"]
        field = next(
            (key for key in ("output", "result") if key in terminal and terminal[key] is not None),
            None,
        )
        if field:
            output, basis = output_size(terminal[field])
        else:
            issues["missing_output"] += 1
    incoming = size(arguments) if arguments is not None else 0
    return {
        "conversation": conversation,
        "start_event": start["seq"],
        "end_event": end["seq"] if end else None,
        "tool": name,
        "operation": operation(name, arguments),
        "input_chars": incoming,
        "output_chars": output,
        "estimated_tokens": math.ceil((incoming + (output or 0)) / 4),
        "output_basis": basis,
        "oversized_output": (output or 0) >= 16000,
        "argument_fingerprint": hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:12],
    }


# ##################################################################
# aggregate rankings
# Tool and operation totals rank observed payload cost without assigning model spend.
def ranking(rows, field, top):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row[field]].append(row)
    summaries = [summarize(key, group) for key, group in groups.items()]
    return sorted(summaries, key=lambda row: (-row["estimated_tokens"], row["label"]))[:top]


# ##################################################################
# group summary
# Bound evidence to three calls while preserving cross-conversation repetition counts.
def summarize(label, group):
    ordered = sorted(group, key=lambda row: row["estimated_tokens"], reverse=True)
    fingerprints = collections.Counter(row["argument_fingerprint"] for row in group)
    return {
        "label": label,
        "calls": len(group),
        "conversations": len({row["conversation"] for row in group}),
        "estimated_tokens": sum(row["estimated_tokens"] for row in group),
        "input_chars": sum(row["input_chars"] for row in group),
        "output_chars": sum(row["output_chars"] or 0 for row in group),
        "unknown_outputs": sum(row["output_chars"] is None for row in group),
        "oversized_outputs": sum(row["oversized_output"] for row in group),
        "identical_argument_repeats": sum(count - 1 for count in fingerprints.values()),
        "evidence": [reference(row) for row in ordered[:3]],
        "candidate": advice(label),
    }


# ##################################################################
# safe evidence
# Event references allow precise follow-up without including raw output or inputs.
def reference(row):
    return {
        key: row[key]
        for key in (
            "conversation",
            "start_event",
            "end_event",
            "estimated_tokens",
            "operation",
        )
    }


# ##################################################################
# candidate guidance
# Debugging and coding remain eligible for reusable inspection and verification operations.
def advice(label):
    if "read" in label or "grep" in label:
        return "Inspect referenced calls; use scoped search/read or a reusable structured inspector instead of broad dumps."
    if "shell:" in label or "bash" in label or "run" in label:
        return "Inspect repeated diagnostics/build commands; batch related checks in a general tool returning verdicts and evidence."
    return "Inspect referenced calls; consolidate repeatable discovery/verification behind a bounded higher-level tool."


# ##################################################################
# expensive sequences
# Non-overlapping three-call windows avoid counting each call repeatedly within a lane.
def sequences(rows, top):
    groups = collections.defaultdict(list)
    lanes = collections.defaultdict(list)
    for row in rows:
        lanes[row["conversation"]].append(row)
    for lane in lanes.values():
        for index in range(0, len(lane) - 2, 3):
            window = lane[index : index + 3]
            label = " > ".join(row["operation"] for row in window)
            groups[label].append(window)
    summaries = []
    for label, windows in groups.items():
        group = [row for window in windows for row in window]
        ordered = sorted(windows, key=lambda window: -sum(row["estimated_tokens"] for row in window))
        summaries.append(
            dict(
                summarize(label, group),
                windows=len(windows),
                window_evidence=[[reference(row) for row in window] for window in ordered[:2]],
            )
        )
    return sorted(summaries, key=lambda row: -row["estimated_tokens"])[:top]


# ##################################################################
# bounded helper adoption
# Raw shell reads that a bounded helper would replace; budgets are a stated proxy.
HELPERS = {
    "cat": "file-skim",
    "sed": "file-skim",
    "head": "file-skim",
    "tail": "file-skim",
    "grep": "rg-skim",
    "rg": "rg-skim",
    "ssh": "shell-skim or remote-probe",
    "curl": "web-skim",
    # git-skim lives in ~/bin next to file-skim and is named by rawGitNote only when it resolves
    # on the agent's PATH. Raw full git diff/log/show is the replacement target.
    "git": "git-skim diff|log|show (bounded; drill-down named in output)",
    # db-skim runs read-only queries with capped results; raw psql output was unbounded (282k chars in one audited conversation)
    "psql": "db-skim (bounded read-only queries; each result capped with a drill-down log)",
}
GIT_SKIM_READS = {"status", "diff", "log", "show"}


def git_bounded(words):
    # Labels from operation(): bounded diff forms only; full diff/show/log stay raw.
    return len(words) > 1 and words[1] == "diff" and ("--stat" in words or "--" in words)


HELPER_BUDGET_CHARS = 4000


# ##################################################################
# segment attribution
# A compound command (`cd X && agentd3-cli memory add ...; cat FILE`) is split at shell operators
# outside quotes (split_outside_quotes). Its payload belongs to the payload-producing segment.
SHELL_BUILTINS = {
    "cd",
    "pushd",
    "popd",
    "export",
    "unset",
    "set",
    "echo",
    "printf",
    "true",
    "false",
    ":",
    "test",
    "[",
    "wait",
}
PAYLOAD_FIRST = (
    "cat",
    "sed",
    "head",
    "tail",
    "grep",
    "rg",
    "ssh",
    "curl",
    "git",
    "file-skim",
    "rg-skim",
    "shell-skim",
    "remote-probe",
    "web-skim",
    "gh-skim",
    "git-skim",
    "md-context",
    "memory-digest",
)


def split_outside_quotes(command):
    # Split on shell separators only when they are not inside quotes, so the body of an
    # `ssh HOST '...; head -5 f'` command stays one remote segment instead of being cut
    # into fragments whose quoted program words get mislabelled as local commands.
    chunks, current, quote, index = [], [], None, 0
    while index < len(command):
        char = command[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            elif char == "\\" and quote == '"' and index + 1 < len(command):
                index += 1
                current.append(command[index])
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == "\\" and index + 1 < len(command):
            current.append(char)
            index += 1
            current.append(command[index])
        else:
            operator = next((op for op in ("&&", "||", ";", "|", "\n") if command.startswith(op, index)), None)
            if operator:
                chunks.append("".join(current))
                current = []
                index += len(operator)
                continue
            current.append(char)
        index += 1
    chunks.append("".join(current))
    return chunks


def segments(command):
    parts = []
    for chunk in split_outside_quotes(command):
        try:
            words = shlex.split(chunk)
        except ValueError:
            words = []
        if words:
            parts.append(words)
    return parts


def is_memory_write(words):
    # `memory init` and `memory add` are mandated stream writes, not raw reads of memory.
    return (
        words[0].rsplit("/", 1)[-1] == "agentd3-cli"
        and "memory" in words
        and words[words.index("memory") + 1 : words.index("memory") + 2] in (["init"], ["add"])
    )


def payload_segment(payload):
    # Prefer a payload-producing program; otherwise the first non-builtin segment labels the call.
    for words in payload:
        if words[0].rsplit("/", 1)[-1] in PAYLOAD_FIRST:
            return words
    return payload[0] if payload else []


# ##################################################################
# helper adoption
# Counts bounded-helper shell calls against raw equivalents from the same sample, so
# every report shows whether agents are actually moving to the helpers.
GH_CI_RAW = {("run", "view"), ("run", "list"), ("pr", "view"), ("release", "list")}
BOUNDED_PROGRAMS = {
    "gh-ci-skim",
    "file-skim",
    "rg-skim",
    "shell-skim",
    "remote-probe",
    "md-context",
    "memory-digest",
    "gh-skim",
    "web-skim",
    "git-skim",
    "db-skim",
}


def helper_adoption(rows):
    bounded, raw = collections.Counter(), collections.Counter()
    for row in rows:
        if row["operation"] == "read:batch":
            # One paths[] batch replaces several single-file reads; the taught route counts as bounded.
            bounded["read:batch"] += 1
            continue
        if row["tool"] != "bash" or not row["operation"].startswith("shell:"):
            continue
        words = row["operation"].removeprefix("shell:").split(" ")
        program = words[0]
        if program in BOUNDED_PROGRAMS:
            bounded[program] += 1
        elif program == "agentd3-cli" and words[1:3] == ["memory", "digest"]:
            bounded["agentd3-cli memory digest"] += 1
        elif program == "agentd3-cli" and words[1:3] == ["memory", "write"]:
            continue
        elif program == "agentd3-cli" and len(words) > 1 and words[1] == "memory":
            raw["agentd3-cli memory"] += 1
        elif program == "gh" and len(words) > 2 and (words[1], words[2]) in GH_CI_RAW:
            raw["gh " + " ".join(words[1:3])] += 1
        elif program == "git":
            if git_bounded(words):
                bounded[program] += 1
            elif len(words) > 1 and words[1] in GIT_SKIM_READS:
                raw[program] += 1
        elif program in HELPERS:
            raw[program] += 1
    total = sum(bounded.values()) + sum(raw.values())
    return {
        "bounded_calls": dict(sorted(bounded.items())),
        "raw_calls": dict(sorted(raw.items())),
        "bounded_total": sum(bounded.values()),
        "raw_total": sum(raw.values()),
        "bounded_share": round(sum(bounded.values()) / total, 3) if total else None,
    }


# ##################################################################
# helper gap
# Ranks raw shell calls whose output exceeds the default helper budget. Labels only:
# raw command text is never available here, so eligibility is by program word.
def helper_gap(rows, top):
    groups = collections.defaultdict(list)
    for row in rows:
        if row["tool"] != "bash" or not row["operation"].startswith("shell:"):
            continue
        program = row["operation"].split()[0].removeprefix("shell:")
        if program == "git":
            words = row["operation"].split()
            if git_bounded(words) or not (len(words) > 1 and words[1] in GIT_SKIM_READS):
                continue
        if program in HELPERS and row["output_chars"] is not None:
            groups[program].append(row)
    summaries = []
    for program, group in groups.items():
        over = [max(0, row["output_chars"] - HELPER_BUDGET_CHARS) for row in group]
        summaries.append(
            {
                "program": program,
                "helper": HELPERS[program],
                "calls": len(group),
                "conversations": len({row["conversation"] for row in group}),
                "output_chars": sum(row["output_chars"] for row in group),
                "oversized_outputs": sum(row["oversized_output"] for row in group),
                "avoidable_tokens_proxy": math.ceil(sum(over) / 4),
                "evidence": [reference(row) for row in sorted(group, key=lambda row: -row["output_chars"])[:3]],
            }
        )
    return sorted(summaries, key=lambda row: (-row["avoidable_tokens_proxy"], row["program"]))[:top]
