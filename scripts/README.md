# Conversation tool-payload audit

Use `./audit-conversations.sh --skip CONVERSATION_ID --count 10` from any directory.
The historical shell command delegates to `run`, which bootstraps a local venv.
No configuration environment variables are read. Dependencies are in requirements.txt.

Optional flags:

- `--json` returns bounded structured rankings.
- `--top 3` limits each ranking, default 5, maximum 10.
- `--conversation CONVERSATION_ID` selects one completed conversation, including archived records.
- `--inspect CONVERSATION_ID:EVENT_SEQ` returns safe call sizes and references for a start or terminal event.
- `--skip CONVERSATION_ID` is repeatable.
- `--exclude-origin sched:TASK_ID` is repeatable and can omit prior runs of the audit task.
- `--api http://127.0.0.1:8620` overrides the API base explicitly.

Exit 0 means tool-payload coverage is complete. Exit 2 emits a provisional ranking with tool coverage gaps.
Exit 1 means the audit failed, including transport/schema/pagination errors or no available sample.
Missing historical model usage is informational in `usage_coverage`, not a tool-payload coverage failure.

Estimates are characters divided by four, not billed tokens. They measure observed arguments and tool
output content, not repeated context, binary/image tokens, hidden output, or child-agent spend.
Tool content envelopes are counted once rather than counting both content and structuredContent.
Tool names and allowlisted command/extension labels are emitted, never raw commands, paths, or outputs.
Fingerprints identify exact repeated arguments without exposing their values.
`helper_adoption` counts bounded-helper shell calls against raw equivalents (`bounded_share`, per-program counts).
Arguments arrive as JSON text from the daemon and are decoded before labelling.
`helper_gap` ranks raw shell reads that a bounded helper (file-skim, rg-skim, shell-skim, remote-probe)
would replace; avoidable_proxy is output above the helper budget, and it is not a measured saving.
Candidates require inspection before claiming actual savings. Coding and debugging are eligible.

## Live API details

Conversation listing defaults to created_at ordering, so the audit explicitly requests
`sort=updated_at&order=desc` and includes archived conversations. Offset pages are checked for duplicate
identities and increasing timestamps. Moving pagination fails closed instead of silently dropping records.
Events use seq/next_after and an empty terminal page. Detail last_seq provides a completeness high-water mark.
Current tool terminals are `tool.completed`, with `tool.finished` and `tool.failed` also recognized.
Pairing uses tool_call_id. `tool.output` chunks are not counted again when a final terminal output exists.
Pig's `tool.requested` transcript event precedes `tool.started`; it is ignored only when the start
has the same id, name and arguments. Unmatched, duplicated, missing-id or mismatched requests remain
explicit coverage failures.
Unknown events and missing ids, arguments, outputs, or terminals remain explicit coverage gaps.
Model usage comes from the actual last_usage and available turn.completed usage records, never allocation
of model tokens to tool calls. Those records do not promise complete conversation billing.

Close urllib HTTPError responses explicitly. On Python 3.15, an unclosed HTTP error response can produce
an unraisable temporary-file ResourceWarning under pytest -W error.

## Verification

The deterministic gate works from any directory:

```sh
~/.agents/skills/token-audit/scripts/run check
```

It runs plain Ruff lint, Ruff format validation, and all six live integration tests from the script root.
`pyproject.toml` fixes line length, first-party imports, test discovery, strict warnings, and a 30-second
per-test timeout. No lint rules are disabled. The lower-level commands from this directory are:

```sh
.venv/bin/ruff check src run
.venv/bin/ruff format --check src run
.venv/bin/python -m pytest -q
./audit-conversations.sh --count 10
```

Tests use the real local daemon, its real completed conversations, pagination with small pages,
a refused loopback connection, a missing live identity, and removal of an actual recorded terminal
from fetched events to verify missing-result coverage. Tests do not invoke models or change conversations.
Testing artifacts belong in output/testing. This skill directory is not a git repository.
