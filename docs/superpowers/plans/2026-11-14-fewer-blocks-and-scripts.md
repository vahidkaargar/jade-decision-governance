# Fewer blocks, joined commands and disciplined scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fewer human questions for repeated and planned work, by reading every script a call runs with the gate's rules, approving a script once bound to its content, one question per bundle of calls, and a script library with audit and propose-first cleanup.

**Architecture:** Phase 1 finds scripts in more places and feeds their bytes into the call's `extra` text (as `blind.written_scripts` already does), so every existing rule runs on them; a `script_sha` fingerprint, carried on the `Assessment`, binds content, argv, env and project. Phase 1 ships in shadow mode. Phase 2 runs a checked, content-addressed copy and reuses the 0.7.0 hint and confirm flow. Phase 3 adds sealed bundle grants. Phases 4 and 5 add `jade scripts` and `jade tidy`, report-only unless a human terminal applies.

**Tech Stack:** Python 3.13/3.14, SQLite ledger (`jade/ledger.py` SCHEMA + MIGRATIONS), argparse CLI (`jade/cli.py`), pytest acceptance tests, ruff.

**Spec:** `docs/superpowers/specs/2026-11-14-fewer-blocks-and-scripts-design.md` (v2)

## Global Constraints

- Start only after the 0.7.0 branch `design-clear-questions` is merged into `origin/main`. Branch from merged `origin/main`.
- One phase per PR. Merge, tag and release are the owner's.
- Tests first. Gate per task: `uv run ruff check`, `uv run pytest`; before each PR also `CI=1 HOME=$(mktemp -d) uv run pytest`.
- Question text only from the gate. No secret in any text, index, report or log.
- Human-only classes, live JADE wiring, R-21, R-22 and R-31b are never remembered and never covered by a script approval. None of these, nor pushes and merges, go in a bundle.
- Nothing hard-deletes except the existing `jade ledger purge`. Files go to the system Trash.
- Script reads are never cached across calls (no `lru_cache`, not in `policy._hidden`).
- The script sha comes from the bytes the assessment read. Never read the file a second time for the fingerprint.
- New ids: rule `R-31b` (high, exact only, marker `blind:script-unread`); fingerprint kind `script_sha`; gate setting `scripts` (`shadow|enforce`, default `shadow`).
- Limits: script read max 1 MiB (`_SCRIPT_READ_MAX`), nesting depth 2, bundle max 10 calls, bundle grant TTL 1800 s, suggest threshold 3 same shapes in 7 days in one project, stale or unused after 30 days, shadow window at least 7 days.
- Plain-text lines for the owner: simple English, short sentences.

## Review Focus

1. A script edited after the check but before it runs: the run uses the checked copy (phase 2), or the permit does not match (phase 1).
2. The same approved script with other arguments, other env prefix, or from another project: asks again.
3. A script found only through PATH, `scripts/x.sh` without `./`, `jx`, `xargs bash` or `find -exec bash {}`: read or R-31b, never silent.
4. A script that runs `source "$X"`, `eval`, or a `$cmd` command word: R-31b, never standing.
5. `jade tidy --apply` from an agent (no tty), or aimed at a git-tracked file or JADE state: refused.

---

## Phase 0. Measure

### Task 1: Baseline counts script

**Files:** Create `evals/blocks_baseline.py`; Test `tests/acceptance/test_blocks_baseline.py`

**Interfaces:** `baseline(db_path: str, days: int = 14) -> dict` with keys `gated_per_100`, `asks_per_100`, `terminal_per_100`, `would_gate_per_100`, `top_shapes` (max 10 of `{hold_key_prefix, rule_id, count}`), `script_calls`. Counts script calls from gate events, not from `blind_spot` events (journaled once per session and label). CLI prints JSON counts only.

- [ ] Test: fixture ledger, 200 events (40 gated, 10 script calls, one hold_key 7 times, 5 `would_gate`): `gated_per_100 == 20.0`, `script_calls == 10`, top count 7, `would_gate_per_100 == 2.5`. Output contains no `subject` text.
- [ ] Run, fail. Check gate events carry `hold_key`; if not, add it in this task. Implement. Run, pass. Commit.

### Task 1b: Permit TTL counts from approval

**Files:** Modify `jade/permits.py` (`_create` 280-304, `approve` 520-584, `sweep` 803-823); Modify `jade/hooks.py` (block message for a subagent call); Test `tests/acceptance/test_permit_ttl_from_approval.py`

**Interfaces:** On `approve`, `expires_at = approved_at + ttl_s`. A PENDING permit expires at `issued_at + 86400`. New constant `permits.PENDING_MAX_S = 86400`.

- [ ] Tests: create PENDING, advance clock 40 min, approve, consume at +45 min: consumed; approve then advance 31 min: not consumed; PENDING never approved: expired after 24 h by `sweep`; a blocked call with an agent id gets one plain line saying the main session can resend it and ask in the chat.
- [ ] Run, fail. Implement. Run, pass. Full gate. Commit (ships in PR 1).

## Phase 1. Every script read like an inline command, shadow first (PR 1)

### Task 2: Find every script a call runs

**Files:** Modify `jade/blind.py` (`script_file` 237-249, `script_label` 205-232); Test `tests/acceptance/test_script_find.py`

**Interfaces:** `blind.scripts_run(words: list[str], cwds: list[str], path_env: str) -> list[ScriptRef]`, where `ScriptRef` has `path: str | None`, `candidates: list[str]`, `argv: list[str]`, `env: dict[str, str]`, `unread_reason: str | None`. `blind.SYSTEM_DIRS` holds `/usr`, `/bin`, `/sbin`, `/System`, `/opt/homebrew` and the uv tool folder.

- [ ] Tests: `scripts/x.sh a` resolved against cwd with argv `["a"]`; bare `deploy` found in a `tmp_path` folder on PATH is a ref; bare `git` (system) is not; `jx release check` resolves to `scripts/release/check.sh`; `FOO=1 ./x.sh` records env `{"FOO": "1"}`; `printf x | xargs bash`, `find . -exec bash {} \;`, `bash "$f"`, `bash *.sh` give `unread_reason == "no literal file"`.
- [ ] Run, fail. Implement. Run, pass. Commit.

### Task 3: Read the script with every rule

**Files:** Modify `jade/policy.py` (`_assess_shell` 1244-1336, `extra` feed 1244-1245, raw invariants 1309-1316, `_script_ho` 932-948, `_script_names_state` 918); Modify `jade/interp.py` (call site only); Test `tests/acceptance/test_script_content_gate.py`

**Interfaces:** `Assessment` gains `scripts: list[ScriptRead]` with `path`, `sha` (sha256 of the bytes read), `argv`, `env`, `project`, `gated_lines: list[str]` (max 5), `unread_reason`. Script bytes join `extra`; raw invariants read `text_in` plus `extra`.

- [ ] Tests (scratch scripts in `tmp_path`):
  - `git reset --hard HEAD~1` in `x.sh`, call `bash x.sh`: high, R-11, `gated_lines` has that line.
  - read-only script (`git status`, `ls`): not gated, no blind label.
  - a script writing `~/.claude/settings.json` disabling hooks: live wiring (today missed).
  - a script naming `jade permit approve prm_x`: SI-09 block; a script only naming `jade check`: not blocked.
  - Python script calling `subprocess.run(["gh","pr","merge","5"])`: gated R-10 through `interp.py`.
  - `./a.sh` calls `./b.sh` calls `gh pr merge 5`: gated. Depth 3: R-31b. Nested path with two candidate folders, one unreadable: R-31b.
  - `rm -rf ~/` inside: `ho_ids` set (regression).
  - two calls in a row after editing the file: the second assessment sees the new bytes (no cache).
- [ ] Run, fail. Implement. Run, pass. Full gate. Commit.

### Task 4: R-31b and the `.git/hooks` rule

**Files:** Modify `policies/risk.yaml` (R-31b after R-31; `.git/hooks/*` write rule high); Modify `jade/policy.py` (emit marker `blind:script-unread`); Modify `jade/memory.py` (`EXACT_ONLY_RULES`, `NEVER_RULES`); Test `tests/acceptance/test_script_content_gate.py`

- [ ] Tests: each `unread_reason` (unreadable, over 1 MiB, depth 3, unknown language, interp incomplete, `source "$X"`, `eval "$(cat f)"`, `$cmd` word, `while read c; do $c; done`) gives R-31b high; `memory.eligibility` is `never` for R-31b; a Write to `.git/hooks/pre-commit` is high.
- [ ] Run, fail. Implement. Run, pass. Commit.

### Task 5: `script_sha` fingerprint and enforcement on permits

**Files:** Modify `jade/hooks.py` (`fingerprints` 518-545, `_gate`); Modify `jade/permits.py` (`try_consume` ~672-703, `consumed_by_call` ~641-663, `_preconditions` 274); Modify `jade/cli.py` (`permit show|approve` display, ~184 and 211-228); Test `tests/acceptance/test_script_sha.py`

**Interfaces:** `fingerprints` takes `script_sha` from `Assessment.scripts` (no file read): sorted list of `sha256:<hex>|argv|env|project` digests.

- [ ] Tests: permit for `bash x.sh`, edit one byte, retry: not consumed, asks again; unchanged retry: consumed; other argv: not consumed; permit created without `script_sha` refused when the call has one (both `try_consume` and `consumed_by_call`); `jade permit show` prints path, `sha256 <12 hex>` and gated lines; memory `exact_key` differs after the edit.
- [ ] Run, fail. Implement. Run, pass. Commit.

### Task 6: Shadow mode for the new script levels

**Files:** Modify `jade/hooks.py`; Modify `jade/ops.py` (`gate` subcommand, `check` row); Modify `jade/cli.py`; Test `tests/acceptance/test_script_shadow.py`

**Interfaces:** `jade gate scripts shadow|enforce` (human tty; add to SI-09 patterns in `policies/safety.yaml`). In shadow, a call gated only because of script content journals `would_gate` with rule and level and runs as before. Human-only and SI findings from scripts enforce in both modes (today's behaviour or stricter).

- [ ] Tests: shadow: risky script runs, `would_gate` event written; enforce: asks; HO in a script asks in both modes; an agent call `jade gate scripts enforce` is blocked by SI-09; `jade check` shows `scripts: shadow`.
- [ ] Run, fail. Implement. Run, pass. Full gate. Commit. PR 1 after the owner says yes.

## Phase 2. Approve a script once (PR 2)

### Task 7: Run the checked copy

**Files:** Create `jade/scriptcopy.py`; Modify `jade/hooks.py` (stdout `updatedInput`); Test `tests/acceptance/test_script_copy.py`

**Interfaces:** `scriptcopy.store(bytes_, sha) -> str` writes `<cache>/scripts/<sha>.<ext>`, mode 0444, created only if absent. `scriptcopy.rewrite(command, refs) -> str | None` swaps each script path for its copy; returns `None` when any ref cannot be swapped (runner with no literal file, nested call by relative path).

- [ ] Tests: approved run executes the copy (edit the original after the check: the old content runs); `rewrite` returns `None` for a nested relative call; when `None`, no standing choice is offered; the copy dir is outside JADE state paths.
- [ ] Run, fail. Implement. Run, pass. Commit.

### Task 8: Script choice in the question

**Files:** Modify `jade/askchat.py` (`question_for`, `SUGGEST_LABELS` ~54); Modify `jade/scopes.py` (`offered`, `_is_plain_shell_call` ~108); Test `tests/acceptance/test_script_approval.py`

**Interfaces:** `askchat.SUGGEST_SCRIPT = "Allow, suggest this exact script for later"`. Scope kind `script_exact`, key = sha256 of the `script_sha` list. Offered only with a checked copy, no `ho_ids`, no live wiring, no R-21/R-22/R-31b.

- [ ] Tests: question has `Script: <path>, <size> bytes, sha256 <12 hex>` and up to 5 gated lines; click writes a `hint` with `scope_kind == "script_exact"` and no standing power; after `jade review confirm` the same script, argv, env and project runs silent at level low; other argv, edited script, or another project asks; HO content: no script choice.
- [ ] Run, fail. Implement. Run, pass. Commit.

### Task 9: Confirm and list show script scopes

**Files:** Modify `jade/ops.py` (`review_confirm`, `knows_list`, `_SCOPE_COVERS`); Test `tests/acceptance/test_script_approval.py`

- [ ] Tests: confirm prints `Would become standing for: this exact script <path> (sha256 <12 hex>), with these arguments, in <project>` and the 0.7.0 lifetime line; `jade knows list` shows `scope  script <path>`.
- [ ] Run, fail. Implement. Run, pass. Full gate. Commit. PR 2 after the owner says yes.

## Phase 3. One question for a list (PR 3)

### Task 10: Bundle propose, assessed by the hook

**Files:** Modify `jade/ledger.py` (SCHEMA table `bundles(bundle_id, session_id, created_ts, expires_ts, state, items_json, seal)`); Create `jade/bundles.py`; Modify `jade/hooks.py` (recognise `jade bundle propose FILE`); Modify `jade/cli.py`; Test `tests/acceptance/test_bundles.py`

**Interfaces:** `bundles.assess(items: list[dict], session_id, cwd) -> Bundle`, called by the hook on the `jade bundle propose` call. Per item: command items bind `plan_hash` and fingerprints; Edit items bind path and `content_sha(tool_input)`; Write items bind path and content digest. Items `alone`: `ho_ids`, live wiring, blocked, R-31b, push, merge. Over 10 items: refused with one plain line. Rows sealed with the permit HMAC key.

- [ ] Tests: 3 items (two Edits on one file, one `rm -rf build`): two bundled, one `alone`; 11 items refused; editing the file after propose changes nothing stored; a row with a broken seal is ignored.
- [ ] Run, fail. Implement. Run, pass. Commit.

### Task 11: Bundle question and grants

**Files:** Modify `jade/askchat.py`; Modify `jade/hooks.py` (`_gate` grant lookup); Modify `jade/bundles.py`; Test `tests/acceptance/test_bundles.py`

**Interfaces:** `bundles.grant_for(db, session_id, call) -> Grant | None` consumes one item once. Options `Allow all listed`, `Deny all`.

- [ ] Tests: one Allow, then 4 sequential Edits to one file all run with no new question (the 0.7.0 retry case); a second run of the same call asks; a call not in the bundle asks and names the closest item; grants expire after 1800 s; no `human_answers` row is written; `alone` items keep their own question.
- [ ] Run, fail. Implement. Run, pass. Full gate. Commit. PR 3 after the owner says yes.

## Phase 4. Script library and audit (PR 4)

### Task 12: `jx` launcher and header

**Files:** Create `scripts/jx` (POSIX sh); Create `docs/13-scripts.md`; Test `tests/acceptance/test_jx.py`

**Interfaces:** `jx <area> <verb> [args]`: `<repo>/scripts/<area>/<verb>.sh`, else `~/scripts/<area>/<verb>.sh`, else exit 2 with one plain line. Header lines `# purpose:`, `# owner:`, `# capability:` (comma list of `read|write|network|delete`), `# callers:`. Same lookup as `blind.scripts_run` (Task 2), one shared test table.

- [ ] Tests: project wins over shared; missing: exit 2; area or verb with `/`, `..` or a leading `-`: refused.
- [ ] Run, fail. Implement. Run, pass. Commit.

### Task 13: `jade scripts index|audit|suggest`

**Files:** Create `jade/scriptlib.py`; Modify `jade/cli.py`; Test `tests/acceptance/test_scriptlib.py`

**Interfaces:** `index(root) -> list[Entry]` writes `scripts/INDEX.toml`. `audit(root, db) -> list[Finding]`, kinds `no_header`, `capability_under_declared`, `one_task`, `duplicate`, `unreferenced`, `stale_index`, `human_only_inside`, `unread_pattern_inside`. `suggest(db, project, days=7, min_count=3) -> list[Suggestion]`.

- [ ] Tests: one fixture per finding kind (`capability_under_declared` uses the Task 3 assessment); duplicates after whitespace normalisation; suggestions use one project's shapes only; `audit` exits non-zero on any finding.
- [ ] Run, fail. Implement. Run, pass. Full gate. Commit. PR 4 after the owner says yes.

## Phase 5. Cleanup that proposes first (PR 5)

### Task 14: `jade tidy`

**Files:** Create `jade/tidy.py`; Modify `jade/ledger.py` (MIGRATIONS if `retired` needs a column); Modify `jade/cli.py`; Modify `jade/ops.py` (`check` row, `maintain` report); Test `tests/acceptance/test_tidy.py`

**Interfaces:** `tidy.plan(db, roots) -> list[Item]`, kinds `permit_expired`, `bundle_expired`, `hint_old`, `memory_revoked`, `scope_orphan`, `script_flagged`, `copy_orphan`, `temp_file`. `tidy.apply(items)` needs `is_human_tty()`. `tidy.TEMP_ROOTS` and `tidy.trash_dir()` (`~/.Trash` on darwin, `$XDG_DATA_HOME/Trash/files` else) each in one place.

- [ ] Tests: `plan` lists each kind from fixtures; `apply` without a tty is refused; git-tracked files, JADE state and config, and paths outside `TEMP_ROOTS` are never listed; moved files are in `trash_dir()` (test sets `XDG_DATA_HOME` to `tmp_path`); ledger rows are retired, not deleted; `jade check` shows `N items to tidy`; `jade maintain` changes nothing.
- [ ] Run, fail. Implement. Run, pass. Commit.

### Task 15: Agent texts, docs, threat rows, CHANGELOG

**Files:** Modify `skills/jade/SKILL.md`, `docs/claude-md-jev-section.md`, `docs/05-*` (threat rows: script as bypass, check-to-run race, bundle hiding, make/npm residual), `CHANGELOG.md`; Test extend `tests/acceptance/test_one_risky_action_text.py`

- [ ] Test both agent texts contain: join read-only commands in one call; each risky action in its own call or a bundle; run `gh pr merge` alone; repeated work goes into a `jx` script. Run, fail. Write. Run, pass. Full gate. Commit. PR 5 after the owner says yes.

## Owner steps

1. Approve the spec and this plan.
2. After PR 1: wait at least 7 days in shadow, read `uv run python evals/blocks_baseline.py`, then decide on `jade gate scripts enforce`.
3. After PR 2: `jade review confirm` for script suggestions.
4. After each PR: review and merge. Tags and releases when you want them.
5. After PR 5: `jade tidy`, read the list, then `jade tidy --apply` in a terminal.
