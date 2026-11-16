---
name: token-audit
description: >
  Rank recent AgentD3 conversations by tool-use payload hotspots and repeated
  operations, then ship one verified higher-level or general tool improvement.
  Use for "reduce token usage", "find cheaper recipes", tool-cost analysis,
  and the matching scheduled task. Reasoning-heavy conversations are eligible.
---

# Token audit

The objective is better tools, not finding whole conversations that can run on
cheaper models. There is a largest observed tool cost even when the surrounding
work requires research, design, debugging or coding.

## Reading and searching during a token audit

Do not use `sed -n`, `cat` or `grep` for source or text reads. Use
`file-skim FILE:START-END`, `file-skim --grep REGEX -C N FILE...` or `file-skim --tail N FILE...` (last N numbered lines; replaces raw `tail -n N FILE`)
for reads, and `~/bin/rg-skim PATTERN PATH...` for tree searches. When several targets are known, pass them together to one `file-skim` invocation (it accepts multiple files and apportions a shared budget via `--max-total-chars`); use `file-skim --grep REGEX -C N FILE...` to get matches with context across several files in one call instead of a search followed by reads. Do not re-read an unchanged file/range: retain its numbered excerpt and drill only into exact omitted ranges. This cuts repeated helper round trips while preserving numbered evidence and explicit truncation.

To find which files contain a pattern before reading them, the native `grep` tool with `output: "files"`
(daz-agentic PR #997, released v488) returns each matching file with its exact match
count and no line text in one call. Measured on the audited pattern (internal/api):
content mode 47,640 chars, files mode 1,870 chars. Use
For GitHub Actions and release checks use `~/bin/gh-ci-skim -R OWNER/REPO pr N|run ID|runs|releases` (one VERDICT with failed-step evidence and a saved cleaned log) instead of repeated `gh run view`, `gh run list`, `gh pr view` or `gh release list` calls. The audit counts those raw calls as `shell:gh run view` etc. against `shell:gh-ci-skim` adoption.
`~/bin/remote-probe HOST -p 'LABEL::CMD' ...` for read-only checks on a remote host,
not one ssh call per probe. Each route returns bounded output with exact drill-down.
For one remote command whose output may be large, use `~/bin/shell-skim -- ssh HOST 'CMD'`.
For a single HTTP(S) URL, use `~/bin/web-skim URL` instead of `curl URL`: it reports
HTTP status, content type and byte count, keeps the complete response body in a local
log, and returns a bounded view with exact drill-down. Use `--grep REGEX`, `--range
START-END` or `--header` to retrieve only useful evidence; non-2xx responses retain
the bounded error body and return failure. Use `~/bin/gh-skim OWNER/REPO tree|files|search`
for GitHub source, rather than fetching repository pages with curl.
Since daz-agentic PR #1330 (v807) and #1331, an oversized bash call that runs `ssh`, including
when wrapped by `timeout`, `env` or `nice`, gets an `[agentd3 read guidance]` note naming both helpers.
The audit labels a remote call by its ssh program (`shell:ssh`), not by a quoted `head` or `tail`
inside it. Before the in-place fix of 2026-11-11 (`split_outside_quotes` in audit_analysis.py), the
224 ssh calls of conv-c96ef9f012f9a03c were ranked as `shell:head`; this is an unpublished skill-directory edit, not a commit.

Since daz-agentic PR #1025 (merged 2026-11-03, generation 516), a raw `cat`, `sed`,
`head`, `tail`, `grep` or `rg` in the native bash tool whose output exceeds 4,000 characters
returns the full output followed by a `[agentd3 read guidance]` line naming `file-skim` or
`rg-skim`. Before that change the audit found 0 of 552 raw reads using a bounded helper.
The guidance line is advice only: the output is never truncated or replaced. Measured
on bash.go lines 1-400: raw `sed -n 1,400p` = 8,094 chars; `file-skim bash.go:1-400` = 4,058 chars
with an exact OMITTED drill-down. Adoption after the change is measured by the next audit's
`Helper adoption:` line, not assumed.

## Start with one bounded audit

```bash
~/.agents/skills/token-audit/scripts/audit-conversations.sh --skip "$AGENTD3_CONVERSATION_ID" --count 10
```

Use the ranked report and its conversation/event references to inspect the
largest operations. Do not paginate and print raw transcripts manually.
`~/.claude/skills/token-audit` aliases the same installed skill directory.

The report includes tool and operation rankings, expensive three-call windows,
large outputs, a `Raw shell reads with a bounded helper` section (raw `cat`/`sed`/`head`/`tail`
where `file-skim` applies, raw `grep`/`rg` where `rg-skim` applies, raw `ssh` where
`shell-skim` or `remote-probe` applies, each with calls, oversized count and an
`avoidable_proxy` = output chars above the 4,000-char helper budget divided by four; a proxy,
not billed tokens) and coverage gaps. A `Helper adoption:` line gives `bounded_total` (file-skim,
rg-skim, shell-skim, remote-probe, md-context, memory-digest, gh-skim) against `raw_total` (cat, sed,
head, tail, grep, rg, ssh, agentd3-cli memory) with per-program counts and `bounded_share`; use it
directly instead of tallying operations. Shell and read labels decode the daemon's JSON-string
arguments; before this fix every bash/read call was mislabelled `shell:other`/`read:other`, which
hid the real ranking and emptied the helper section. The helper section is the adoption measure: a rising
count there means agents still bypass bounded helpers. Windows are start-order hints, not causal
traces; nested wrapper calls can overlap. Follow the referenced calls before
choosing a replacement. Safe targeted inspection returns sizes and references,
not raw user data or command payloads:

```bash
~/.agents/skills/token-audit/scripts/audit-conversations.sh --conversation CONVERSATION_ID --top 3
~/.agents/skills/token-audit/scripts/audit-conversations.sh --inspect CONVERSATION_ID:EVENT_SEQ
```

Use `--json` for programmatic comparison, `--count 1..50` to change the sample,
`--top 1..10` to bound rankings, and `--api URL` for an explicit daemon endpoint.
Repeat `--skip ID` to exclude several conversations. Use
`--exclude-origin sched:task-a80f27b6d6500f60` to omit this audit's scheduled runs. Use `--started-after 2026-11-10T03:11:00+00:00` (timezone offset required) to restrict the sample to completed conversations created at or after a cutoff, e.g. the post-release cohort for adoption measurement; the window is fail-closed: if no completed conversation falls in it the audit exits 1 with `No completed conversations found` rather than a zero result, and a row without a timezone-aware `created_at` raises an error.
Recent selection includes archived conversations. API/schema failures exit 1;
tool-evidence gaps produce a provisional report and exit 2, not a clean audit.
Keep the evidence, resolve gaps or inspect another stable sample; never treat
either as a successful zero-opportunity result. Missing historical model usage
is a separate `usage_coverage` warning, not a tool-payload audit failure. JSON
output is for local processing, not a reason to dump more data into the
conversation.

Tool input/output size is an estimate of context cost, not exact per-tool
billing. Recorded model usage and cost belong in separate columns. Repeated
context processing, caching, reasoning, delegated agents and system prompts
prevent attributing a conversation's entire bill to a particular tool. Label
estimates; never report projected savings as measured savings.

## Image reads and JS outlines (measured 2026-11-14)

- Image reads: `read` results with `type: "image"` blocks carry base64 pixel data. The audit
  now counts each image block as the 7-char placeholder `[image]` (also inside truncated recorded
  outputs), not as text. Before this fix, conv-07bd89be8005c032:9625->9628 estimated 16,414 tokens;
  after it, 48 (chars/4 proxy, not billed). Real text reads are unchanged.
- JS/TS outlines: `file-skim --outline` lists functions, classes, interfaces, types, column-0
  `const`/`let`/`var` bindings and all-caps `// TITLE` banners. Use it before whole-file `cat` on
  CAD/config models. Shipped in daz-agentic PR #1402 (pending the Apple notary gate) and local ~/bin
  commit 194e600; the installed agent-PATH binary changes only after #1402 publishes.

## Read-family commands: 4,000-character view (daz-agentic PR #1399, pending publication)

When the last stage of a bash command is a raw read (`cat`, `sed`, `head`, `tail`, `grep`, `rg`) or
`git log`, `git diff`, `git show` or `git status`, the bash result is capped at 4,000 characters instead of 16 KB.
The head-and-tail view, the omitted-range notice and the `Full output:` path are unchanged, so the drill-down
still works. Measured (chars, real command in the agentd3 repo): `git log -p -n 12 | head -n 400` printed 20,367
raw characters and now returns a 4,958-character view. This is a chars/4 proxy, not billed usage. Before this
change, an oversized read-family result could enter context at about 16 KB. Adoption is measured by the audit's
`Helper adoption:` line and the `avoidable_proxy` values, not assumed. Until PR #1399 is installed, the old
16 KB behavior still applies; check `agentd3-cli version` before relying on this.

## Sleep arms: bounded repeat confirmation (daz-agentic PR #1099, installed v587)

The audited `sleep` operation was the largest post-v582 avoidable payload: 267 calls,
est 37,455 tokens, with ~394 static boilerplate characters repeated on every arm
(evidence conv-e5b19f9f54e306cc:4848->4849). Since PR #1099 (merged 2026-11-05,
installed generation 587) only a conversation's first arm returns the full early-wake
guidance; every repeat arm returns a 141-char `Sleep re-armed until …` confirmation.
Measured on the installed route (this audit's conversation, event stream): pre-publish
arm seq 1717 = 394 chars; post-publish repeat arm seq 1829 = 141 chars (chars/4 proxy:
~63 est tokens saved per repeat arm, not billed usage). The full guidance still reaches
the model every turn through the sleep tool description, and a count-query failure falls
back to the full wording, so adoption is automatic — no new invocation to learn. Rank
`sleep` arms against this wording split when judging the next audit's sleep totals.

## Native grep: bounded pages with an exact next offset (daz-agentic PR #1129, installed v615)

The audited native `grep` tool was the largest unbounded listing path: est 102,791
across 55 calls in 10 conversations, 7 oversized. Worst single call
conv-4ac7dd1db566630d:2287→2288 (`Command|command|state|Start`, context=5, limit=100)
returned 51,255 chars (est 12,841 tokens) in one call, and the old limit notice suggested
raising the limit (doubling the next payload). Since PR #1129 (merged 2026-11-06,
installed generation 615) one content result is capped at 12,000 chars of formatted
matches cut on whole matches, and every result that omits matches names the exact
`Re-call with offset=N for the next page` — the old "Use limit=2N for more" advice is
gone. A new `offset` argument (default 1) resumes the match list without repeating or
skipping; scan collection is capped at the display bound so a wide survey cannot
accumulate unshowable matches. `output: "files"` and no-match outputs are unchanged.
Measured on the exact audited shape (60 files x 40 long matching lines):
51,255 -> 11,236 chars with notice `offset=8`, page 2 resuming at the first omitted
match (verified live on the installed g615 route, conv-3f14295840f86166 seq 10; chars/4
proxy, not billed usage). Adoption for the next audit: count native grep calls that
pass `offset=` (paged re-calls) and results carrying the offset notice, against calls
that raise `limit=` instead; also re-rank the grep operation total.

## HTTP fetches: `web-skim` replaces raw `curl`

The audited `shell:curl` operation was the second-largest payload (about 129k estimated
tokens across 46 calls, 65k-char bodies). Use `~/bin/web-skim URL [--grep REGEX [--window N] |
--range A-B] [--max-chars N]` instead. Grep shows each match with context characters around
the match itself, so a match deep inside a minified line is never hidden. Replay on the audited
call (conv-bb255b2550853a96:19457, a `curl | grep -o | head` pipeline on a daily-digest page):
raw pipeline stdout 318,986 chars over 3 lines; `web-skim --grep` 955 chars with both matches
visible and an exact count. An earlier line-prefix grep hid the match entirely; fixed and tested. It keeps the full body in a log file and returns the
HTTP status, content type, bytes and a bounded view. Measured on a local 3,000-line body:
raw curl 180,000 chars; web-skim 6,279 chars, grep match returned exactly. Its adoption is
counted as `shell:web-skim` in the next audit.

## Git history and diffs: `git-skim` (~/bin, commits cec858f and a0ef16c35)

Raw `git show`, `git log` and full `git diff` were the largest audited helper gap (296 raw git calls,
74.7k avoidable proxy in the 40-conversation audit). Use `~/bin/git-skim` instead:
`git-skim` (verdict: status, whitespace check, stat, capped patch), `git-skim diff [--cached] [PATH]`,
`git-skim log [-n N] [PATH]` (one-line commits plus the total commit count), and
`git-skim show [REV] [PATH]` (header, stat and a capped patch). Each omitted part names its exact
drill-down, such as `git-skim show REV --max-diff-chars 0`. Measured on a real 344,820-char commit
patch: raw `git show` + `git log -20 -- PATH` = 394,170 chars over 3 calls; `git-skim show` + `log` =
8,771 chars over 2 calls (chars/4 proxy, not billed usage). Adoption is counted as `shell:git-skim` in
the next audit; the audit's `git` helper label now names git-skim.

Raw `git status` on a dirty tree was the unguided gap: the git note only covered diff, show and log, so
a 16,965-char status printed with no guidance (conv-1b2a60521b57d0d9:946). Since daz-agentic PR #1352 (merged
2026-11-11, installed v831), an oversized `git status` gets a note naming `git status --short | head -n 40` and
`git status --short | wc -l`, plus `git-skim` when it resolves. Live replay on v831: 300 untracked files gave an 18,492-char
raw status against a 2,474-char `head -n 40` window (chars/4 proxy, not billed usage). Use the window first and
the count for the total; use `git-skim` for a verdict with a capped patch.

Adoption cohort for the git status note: `--started-after 2026-11-11T21:39:00+00:00` (v831 publish time). The cohort is empty until conversations are created after that time AND complete; the audit exits 1 with `No completed conversations found` until then. An empty cohort is a time gate, not an audit defect.

## Choose and deliver the improvement

1. Compare the top three tool operations or multi-call workflows using their
   observed payload size, call counts and concrete evidence. Estimate avoidable
   context and round trips, likely reuse, correctness risk and implementation
   effort. Choose the largest actionable saving, not the easiest cosmetic edit.
   For broad Markdown reading, use `~/bin/md-context QUERY FILE...` to extract
   matching heading sections from multiple files in one call. If the right
   section is unknown, first use `~/bin/md-context --index '*' FILE...` to
   get heading titles and line numbers without bodies, then request only the
   relevant section; this avoids dumping whole documents just to discover their
   structure. It caps each
   matched section at 12,000 body characters by default and marks the exact
   omitted line range plus section start for `read` drill-down; use
   `--max-chars 0` only when the full matched section is needed. It reports
   `NO_MATCH` and exits
   nonzero rather than silently treating an unmatched document as empty.
   For reading an AgentD3 conversation's durable memory (continuation context),
   use `agentd3-cli memory digest CONV_ID` instead of `agentd3-cli memory show`, which
   returned 346 KB for a 1,244-entry conversation. On the installed v667 binary the
   digest measured 3,057 chars against 6,794 for show (31 entries) and 3,786 against
   21,958 (78 entries). It reports `OMITTED` counts, and `--seq A-B [--kind K] --max-chars 0`
   drills in. The audit counts `memory digest` as bounded; `memory show` stays raw.
   To find every match in a file without dropping later ones, use
   `file-skim --grep REGEX --hits FILE...` (or `gh-skim OWNER/REPO files --grep REGEX --hits PATH`).
   It lists every matching line number and text with no context, and any rows that
   exceed the budget are reported as an exact OMITTED hit range with a drill command.
   Measured on the audited Pi-Bolt grep (5 files, `grep -C 5`, 35,872 chars with
   `head -240` per file silently hiding matches): `--hits` listed all 98 original match
   lines in 9,582 chars, with the 218-hit file budgeted and drillable.
   For upstream or other GitHub repositories, use `~/bin/gh-skim OWNER/REPO[@REF]
   tree|files|search ...` instead of `curl raw.githubusercontent.com` loops or
   GitHub API tree pipelines. The audited curl workflow fetched four full files in one
   call (68,492 raw chars, 4 calls plus a tree pipeline); `gh-skim files` returned the
   same four files as bounded numbered ranges (16,606 chars in one call, exact
   OMITTED drill-downs), and `gh-skim tree --grep` returned 792 chars. The first use of
   a commit fetches one depth-1 snapshot; later calls for that commit are local.
   For multiple local files, prefer the installed native `read` tool's
   `paths[]` array (up to 16 entries with independent offset/limit), rather than
   issuing one broad `read` per file. Its default 6,000-char per-file excerpt
   retains both ends with explicit OMITTED counts; `max_chars:0` returns the
   full file when required and per-file errors remain visible. Since daz-agentic
   PR #1058 (merged as d1ea7f22c, installed generation 548) introduced a 60,000-char
   batch cap; PR #1092 (installed v582) tightened it to 24,000. Each file's
   `max_chars` is divided by batch size, with explicit omissions and drill-downs
   preserved. On the repeated 16-file large-read fixture, output fell from 60,862
   to 24,798 chars (about 59% fewer; one call in both cases). The earlier 10-file
   fixture (conv-4f7ee1c6ce483fa2:72) also
   showed 84,378 reconstructed chars before batch bounding and 58,221 after the
   60,000-char cap; the tighter cap now targets the residual batch cost.
   Call count is unchanged (one batch call). If a truncated file needs more
   detail, drill with `read {path, max_chars:0}` on that one path, not by re-reading the batch. For single-file reads, keep the 6,000-char default unless more detail is needed; positive budgets and the default single-path read are capped at 12,000 chars (daz-agentic PR #1108, merged e5af35c06: a default no-argument read previously returned up to the 50 KiB byte limit — the audited conv-8e03d47225d6846a:17 AGENTS.md read fell from 24,310 to 12,084 chars live, with the [OMITTED] offset/limit + file-skim drill-down retained). Use `offset`/`limit` for source ranges and `paths[]` for independent files. Prefer outline/search to discover structure; do not request full text merely for discovery. Never repeat a broad read when the exact omitted span can be targeted. Shipped in
   daz-agentic PR #976, installed v469. On the audited two-document workflow
   (conv-5f58ef4919ccdc3f:80 and :83), two reads / 88,561 chars became one
   native batch / 12,073 chars; a byte-equivalent full-text drill was verified.
   For source or text files outside that native route, prefer `file-skim FILE[:START-END] ...`
   over native broad reads (several ranges across files in one call) or
   `file-skim --grep REGEX -C N FILE...` (matches with context). Output is
   numbered, capped per file (4,000 chars by default; `--max-chars 0` for full).
   When surveying many files, add `--max-total-chars N` to apportion a shared
   content budget evenly across the inputs; errors remain visible and each
   truncation retains its exact per-file drill-down. For example,
   `file-skim --outline --max-total-chars 12000 src/a.go src/b.go` bounds aggregate
   structure output instead of multiplying the per-file budget by file count.
   The budget applies to content; report metadata and errors are additional.
   Every unshown span is an exact
   `OMITTED ... drill: file-skim FILE:A-B` line. To survey structure across many
   source files, use `file-skim --outline FILE ...` instead of reading whole
   files: one numbered line per declaration (Go func/type, Python def/class incl.
   methods, JS/TS functions/classes/interfaces, generic fallback; extensionless
   scripts fall back to their shebang), per-file budget with exact
   `[OMITTED N declaration(s) ... drill: file-skim --outline --max-chars 0 FILE]`
   omissions, and `NO_MATCH` as exit 1. Shipped 2026-11-03 after the audit ranked
   read:other as the largest payload hotspot (est 290,535 across 61 calls, 4
   conversations): native read batch calls loading whole files (50k+ chars per
   call) to survey structure. Measured on audited conv-435587e909e8689b:222
   (nine-file batch read, 51,122 recorded output chars): one `--outline` call
   returned 14,289 chars with every declaration and exact drill-down; the
   conv-bf965cbfb2fd9cad:61 set is 16,096 vs 51,430 recorded. Adoption is
   measured by the next audit's read:other ranking, not assumed. Measured on app.js: `applyRoute`
   lookup was 2 calls and 2,402 chars by grep+sed, 1 call and 903 chars with
   file-skim. On the audited call `sed -n 1,360p degraded_banner_test.go` (recorded
   41,686-char envelope, 18,160 text chars; the live file has since changed, so this
   is a recorded baseline, not a byte-identical replay), `file-skim FILE:1-360` returned
   11,638 numbered chars with no omission. Do not `cat` or `sed -n` a whole large file when a range or grep
   answers the question.
   For remote or any unbounded shell output (ssh log reads, builds, long git
   history), use `~/bin/shell-skim` (see the `shell-skim` skill): it keeps the full
   output in a log and returns the exit status, bounded head/tail and drill-down ranges.
   For several read-only checks on one remote host (the audited disk-growth diagnosis
   made 314 separate ssh calls, 110 with identical argument shapes), use
   `~/bin/remote-probe HOST -p 'LABEL::CMD' ...` (or a LABEL::CMD file): one ssh round
   trip, each probe reported with exit status, bounded head and exact drill-down via
   `--show LOG LABEL`. See the `remote` skill.
   Measured on a real spark ssh log read: 157,389 raw chars fell to a 6,684-char
   verdict, with the full log kept and drill-down ranges exact.
   Shipped git guidance (daz-agentic PR #1161, corrected by PR #1162; installed build 203b331
   verified live on an oversized `git log`): when a bash `git diff`, `git show` or `git log`
   prints more than 4,000 characters, the tool result carries a `[agentd3 read guidance]` note
   naming bounded forms that every shell has: `git log --oneline -n N` for history,
   `git diff --shortstat A B` for totals, `git diff --name-only A B | head -n 40` plus
   `| wc -l` for files, and `git diff A B -- PATH | head -c 4000` for one file's patch.
   Measured on daz-agentic (replay, not the audited call): `git diff --stat` over a 374-file
   range is 23,134 chars and `git log --stat -n 30` is 99,686, so stat forms are not bounded
   for wide ranges; `git log --oneline -n 30` is 2,383 chars; a path-scoped 30-commit patch
   for one file is 214,714 chars (`head -c 4000` bounds it). The audit labels these forms
   `shell:git diff --stat` and `shell:git diff -- PATH`, counts them as bounded adoption, and
   ranks only full `git diff`/`show`/`log` output as avoidable raw git. Audit sample
   2026-11-07: `Helper adoption: bounded=13 raw=593`, with git 13 bounded vs 70 raw.
   Bash output cap (daz-agentic PR #1163, head/tail view PR #1165): the native bash tool's
   model-facing result is now at most 16 KB. Output that fits is returned whole. Larger output
   shows its first 4 KB and last 12 KB with the exact omitted line range, and names the full-output
   file. Before #1163 the bash result shared the 50 KB read budget, so one audited result of
   47.9k-51.8k chars entered context almost whole. Measured on a 1,200-line command: 51,317 -> 16,502
   chars (tail-only view, #1163). The audited command conv-5e030219d1a428f4:115 (`cd && git log -5;
   echo; grep -rn ... | head -20`, raw output 100,207 chars, 2 minified 49 KB data lines) showed one of
   its thirteen useful match lines under the tail-only view; the head/tail view keeps all thirteen in
   1,928 chars. Do not run `read` on the full-output file to recover omitted middle lines when a
   narrower `grep` or `head -c` on that file answers the question.
   Attribution (fixed 2026-11-08): a compound bash command is split at `&&`, `||`, `;`, `|` and newlines.
   `cd` and other shell builtins never label it, `agentd3-cli memory init|add` are labelled
   `shell:agentd3-cli memory write` (mandated bookkeeping, excluded from raw adoption), and the label goes
   to the payload-producing segment (cat/sed/head/tail/grep/rg/ssh/curl/git/helpers first). Before this fix
   the top operation `shell:agentd3-cli memory` (118 calls, est 82k) was mostly `memory add` chained with
   `df`, `cat`, `grep`; only 3 of its calls were `memory show`. Start events omit some request keys
   (`timeout`, `offset`, `max_chars`, `minutes`); that is a daemon view, so pairing now accepts dropped keys
   and still flags any changed or added key as `request_start_mismatch`. Before this fix that check flagged
   119 of 119 calls on conv-f82de547aa185b6c, so `status` was `partial` and the live CLI exited 2.
   Native read view (daz-agentic PR #1205 merged e4d341a20, PR #1206 merged 2d5fceb72 for the `cd DIR &&`
   form, published v692; refined by PR #1224, published v710): a single oversized plain raw read, `cat FILE`,
   `sed -n A,Bp FILE` or `head -N FILE`, optionally after `cd DIR &&`, returns a native read view instead of
   the full bash dump. The same requested range is retained, both ends are shown, and content over 4,000 chars
   is bounded with an exact `read {...}` drill-down. No agent action is needed; the view is automatic. Pipelines,
   chains, flags, globs, out-of-range reads and native read failures keep the full bash output and raw-read note.
   Verified on installed v710: a real source-file `sed -n 1,283p raw_read_guidance.go` replay returned 11,878
   raw chars versus 4,198 view chars (chars/4 proxy: 2,970 -> 1,050); endpoints and explicit omitted-content
   drill-down were retained. This is a representative replay, not the exact historical call, and adoption change
   in audited conversations is not yet measured. Earlier v692 replays (before the tighter cap): 13,450 -> 12,248
   chars for sed, 13,810 -> 12,355 for cat, and 10,506 -> 10,350 for head. Note: helper adoption counts do not
   classify this automatic native-read route as `file-skim`.
   `file-skim` IS on the daemon bash PATH since daz-agentic PR #1276 (merged 3e6f23608; CI publish run 42747020182, release v756): the daemon's per-process alias directory exposes it, and it runs the same binary as `agentd3-cli`. Verified on the installed route: `command -v file-skim` resolves, `--outline`, `--max-total-chars`, `--max-chars 0`, `--grep -C` and `--tail` all run, and a missing file exits 2 with an ERROR line. Bounded-helper adoption in the 40-conversation completed sample (2026-11-10, before v756 conversations completed): `bounded=65 raw=3011` (share 0.021); `file-skim` 16 calls against raw `head` 895, `tail` 624, `sed` 297, `cat` 274. Post-release adoption is not yet measured because no completed conversation has run since v756 at audit time.
   For repeated pre-commit review, use `~/bin/git-skim [PATH...]` instead of
   `git status --short --branch; git diff --check; git diff --stat; git diff`.
   It keeps status, whitespace diagnostics and stats, but caps the patch at 4,000
   characters with an exact `git-skim diff --max-diff-chars 0` drill. Use
   `git-skim --cached` for staged changes. Daemon bash agents DO get this helper:
   it lives in `~/bin`, which is on the daemon bash PATH, and since daz-agentic PR #1267
   (merged 2026-11-10, running build b1b1350/generation 791) the oversized `git diff`/`git log`/
   `git show` note names the git-skim forms whenever git-skim resolves on that PATH — verified
   live on conv-239aaeaefe6a4cc3:67 (post-merge, 2026-11-10T16:18+11:00): the note carried the full
   git-skim sentence. In the measured sample that was the only oversized raw git call after the
   merge; the other 16 predate PR #1267 and could not have named it. Adoption is therefore not yet
   judgeable: the next audit should count oversized raw git calls on the post-791 route and whether
   a bounded git form or git-skim follows within three calls (measured on the mixed sample:
   1 of 17 followed, but 16 saw no git-skim sentence). In the 2026-11-07 sample, raw
   status/diff appeared 123 times (88 status, 35 diff), about 96k estimated
   tokens; two full-diff calls
   around 13k estimated tokens apiece. A representative 10.8k-character diff
   produced a 4.4k-character verdict (59% fewer) preserving diagnostics and
   drill-down; estimates are payload proxies, not billed tokens.
2. Consider a domain tool that performs the whole workflow and a general tool
   that processes data near its source. Examples include batched inspection,
   structured search/query results, bounded summaries with explicit drill-down,
   and a reusable programmatic or persistent Python workspace where appropriate.
   For broad source-tree text searches that risk dumping many long lines, use
   `~/bin/rg-skim PATTERN PATH...` for total match counts, bounded per-file
   counts/previews, and explicit truncation; narrow with paths/patterns or raise
   `--max-matches`/`--max-files` to drill down. Previews stop at a total budget
   (`--max-chars`, default 4000); when omitted, the notice prints the exact
   `--offset N` for the next page (commit e321940; counts always cover every match,
   paging the whole result set returns each match once). For several related searches over
   the same tree, batch them in one invocation with repeated `--also REGEX`, e.g.
   `rg-skim 'needleA' src --also 'needleB' --also 'needleC'`. Each query keeps
   separate counts/previews and visible errors; this reduces agent round trips
   without conflating search results. It reports `NO_MATCH` explicitly. Do not reduce every idea to
   suppressing output from one existing command.
3. Inspect existing capabilities first. An existing helper is a starting point:
   improve its interface, batch work, fix adoption or address the remaining cost.
   If already solved, prove that and continue to the next ranked target.
   Batch native reads (the `paths[]` array the read guidance teaches) are labelled
   `read:batch`, not `read:other` (fixed 2026-11-11 after the 10-conversation audit
   ranked read:other est 61,998 across 27 calls with 7 oversized while hiding the
   cause). On that sample, 14 batch calls carried est 51,194 with all 7 oversized
   read outputs (17-26k chars each, conv-178ce29ab3ef579f:67/70/73/149/257 among
   them): a wide survey of long documents at `max_chars` 10-12k legitimately fills
   the batch cap; judge those calls against the section relevance of what was
   surveyed, not as raw dumps, and reach for `md-context QUERY` or `--outline`
   when only part of each file is needed. `read:batch` also counts as bounded
   adoption in `Helper adoption:` (that sample: 14 batch reads, bounded_share
   0.032 -> 0.047). Measure the fix's adoption by the next audit's `read:other`
   ranking: true read:other is now only single-file reads of non-listed extensions.
4. Implement ONE durable improvement, test against a representative real
   operation, and connect it to the skills, tool descriptions or consuming
   prompts that make future agents use it. Follow the affected project's normal
   build, review, publication and installation requirements.
5. Compare original versus replacement call counts and returned payload/token
   estimates. Verify equivalent correctness, visible failures and retained
   evidence. Report measured results separately from expected future savings.

Exploratory, debugging, research, coding, UI and design work is not exempt. The
reasoning may remain agentic while its surrounding retrieval, transformation,
comparison and verification become cheaper. "No mechanical recipe", "already
scripted", "not repeated enough" and "no cheaper model" are not completion
criteria. Never downgrade coding work or `agentic-auto` to `agentic-low` as a
substitute for improving tools.

Every successful scheduled run must ship one useful, verified improvement and
its adoption change. Do not manufacture work or hide risk to satisfy this rule.
A genuine blocker must name the evidence and next concrete action and be reported
as incomplete, not as success or "nothing to do". If there is no useful tool
activity in the sample, widen it and exclude this audit's own runs. If evidence
is missing, fix the audit rather than conclude there is no opportunity.

Retain the chosen hotspot, rejected alternatives, invocation and before/after
results in conversation memory. Check the previous run's result to avoid
repeating the same change. Keep the final report short: winner and evidence,
what shipped, invocation, measured before/after, adoption change and limitations.

## Verify changes to this helper

```bash
~/.agents/skills/token-audit/scripts/run check
```

This runs the pinned local Ruff lint/format checks and six read-only live API
integration tests from the correct project directory. No model calls are made.

## Ranking models by time-token efficiency

For a specific model-efficiency question, this existing separate command avoids
manual API pagination:

```bash
~/.agents/skills/token-audit/scripts/rank-models.sh [--days N] [--min-convs N] [--json]
```

It groups completed conversations by model and prints counts, recorded tokens
and cost, median tokens per conversation, median wall hours and tokens per hour.
Prefer median cost/tokens per conversation to wall time, which includes parked
or sleeping time. `--min-convs` defaults to 2; `--json` emits machine rows.

Recorded usage coverage is limited to what the daemon stores. In particular,
`last_usage` is not proof of whole-conversation or delegated-agent cost. Never
mistake low top-level usage for a cheap delegated workflow.

## Native read views and repeat stubs (agent-side read tools, daz-agentic)

- Oversized plain-file raw reads are answered by a native read view of the same lines (4,000 chars, exact drill-down). Covered shapes: solo `cat F`, `sed -n A,Bp F`, `head [-n] N F`, optionally after `cd DIR &&`, and since PR #1257 (v744, installed 2026-11-09) `cat F | head [-n] N`. Multi-stage pipelines and byte counts keep full output.
- Measured on a 400-line fixture: `cat data.txt | head -n 300` 21,300 chars -> 4,606 chars (chars/4 proxy, not billed usage). Measured on the solo sed form: 8,591 -> 4,602 chars.
- A same-turn identical `read` (same path, offset, limit, max_chars, and byte-identical content) returns a stub starting `Unchanged: this exact read` (PR #1255, v742). Measured saving on the 10-conversation sample: 0 exact same-turn repeats. The stub is unmeasured in practice, not proven useful.

## Audit fingerprints and repeat counts (corrected)

`argument_fingerprint` hashes the full arguments. Start events carry only some keys (for example `path`); `offset`, `limit` and `max_chars` are on the paired `tool.requested` event. Earlier audits grouped reads by path only, so different ranges of one file were counted as repeats. The corrected audit uses the paired request's arguments when `same_call` confirms they agree. Before the fix: `read` identical-argument repeats 35; after: 1 (same 10 conversations). Earlier published figures of 19 same-turn repeats, 25.8k proxy tokens and 32k tokens are withdrawn.

## Adoption measurement

Count bounded helper use (file-skim, rg-skim, shell-skim, remote-probe, md-context, memory-digest, gh-skim, web-skim) against raw `cat`, `sed`, `head`, `tail`, `grep`, `rg` and `ssh`, grouped by conversations started after the change. Post-v728 sample (7 conversations, measured 2026-11-09): bounded 14 (file-skim 9, rg-skim 5) against about 1,323 raw stages. Adoption is still negligible, so a larger change to agent-facing guidance is still the open problem.

## Single-file read batching nudge (daz-agentic PR #1364, merged eda7bd5, release v842)

The agent-side `read` tool appends one `[agentd3 read guidance]` note on the third
successful single-path read in a turn, naming `read {paths:[...]}`. It is once per turn and
batched calls never count. The note recommends batches of up to four files (PR #1367, release v844): a larger batch shares one 24000-char result, so each file's excerpt shrinks below the single-read size. Each file in a batch shows up to 6000 chars, against 12000 for a single read (measured on a real turn: four-file batches cut 7 of 21 files; 6 calls instead of 21). Live check on v846 (7b9cf3af) showed the "up to 4 files" and 6000-char text on the third single read. Verified live on v842: the note appeared on read 3, not on read 4,
and not on a batched read. The audit measured 164 single-file reads over 21 multi-read turns
(132 batchable) before the change. Measure adoption by comparing single-path `read` calls per
turn in conversations started after 2026-11-12T14:13Z (v842) with the earlier cohort. Pre-change baseline
(119 completed conversations, 2026-11-09T01:04Z to 2026-11-12T14:13Z): 1,553 single-path reads, 306 batch reads;
123 turns had three or more single-path reads, and 53 of those turns (43%) also made a batch read. No conversation
had started after 2026-11-12T14:13Z when this baseline was taken, so the post-change figure is still unmeasured. This is a
measured mechanism, not a measured token saving, until the next audit shows the drop.

## Database reads: `db-skim` replaces raw `psql -c` (~/bin, tests in ~/bin/tests/test_db_skim.py)

Raw `psql` output was unbounded and the audit classified it `shell:other`; the audited conversation conv-e93d357a8bccd38f ran 115 psql-like calls returning 282k chars. Use `~/bin/db-skim 'SQL' ...` (read-only, per-result cap, exact drill logs, errors kept, exit 1 on any ERROR). `psql` is now labelled `shell:psql` (raw) and `db-skim` counts as bounded adoption.
