# Clear questions and bigger scopes: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the chat question easy to read, let the owner propose a bigger scope from it (confirmed later in a terminal), allow a narrow `gh pr merge` scope, and give `jade knows edit` a better template.

**Architecture:** One new module for plain texts (`jade/askplain.py`) feeds both the chat question and the macOS dialog. One new module for scopes (`jade/scopes.py`) decides what a call may be generalised to; `jade/memory.py` matches scope keys; the chat option only records a `hint` with a scope, and `jade review confirm` makes it standing. The safety rules stay in code and are tested.

**Tech Stack:** Python 3.13 and 3.14, sqlite (`jade/ledger.py`), pytest, ruff. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-14-clear-questions-and-scopes-design.md` (approved 2026-11-14).

## Global Constraints

- Every text the owner reads is simple English: short sentences, one idea each, at most 20 words per sentence, no jargon (spec section 1 and 6).
- The question text comes only from the gate, never from the agent (spec anti-goals).
- A click in the chat creates a `hint` only. Only `jade review confirm` in a human terminal makes an answer `strong` (owner decision 7).
- A scope never covers delete, force, tag or release. The merge scope covers only `gh pr merge N` in one named project and only with the allowed flags in Task 3 (spec section 7).
- Lifetime of a standing answer stays 30 days after last use and 180 days after confirm (`memory.USE_DAYS`, `memory.CAP_DAYS`).
- Tests first. Gate before every commit: `uv run ruff check`, `uv run pytest -n auto --dist loadgroup -m "not timing"`, `uv run pytest -m timing`. Stage explicit paths. Edit tracked files with python, not `sed -i`.
- Never run JADE code that touches state from outside `tests/`.
- Release 0.7.0 (version bump, tag) is a separate step after this plan. The tag is the owner's.

## Review Focus

Failure modes the spec implies that most likely bite a person. Each has a test in the task named in brackets.

1. `gh pr merge 940 -R other/repo` merges in a different repo than the folder. It must get no merge scope. [Task 3]
2. `cd /other/project && gh pr merge 940` where the `cd` target is not the named project, or the `cd` uses a variable. No project means no project scope. [Task 3]
3. A forged AskUserQuestion with an extra option or a reworded label. The existing token rewrite must still refuse it, and `settle` must ignore an unknown label. [Task 5]
4. A call that joins two risky commands. No scope is offered; the question says to send them one by one. [Task 1 and Task 5]
5. A scope row that outlives a changed project path, or a standing scope that matches a call with a new flag. Matching is by key computed from the current call, so a new flag changes the key. [Task 4]

---

### Task 1: Plain question text (chat and dialog)

**Files:**
- Create: `jade/askplain.py`
- Modify: `jade/askchat.py:49-62` (`question_for`), `jade/askdialog.py:87-98` (`text_for`)
- Test: `tests/acceptance/test_ask_plain_text.py`

**Interfaces:**
- Produces:
  - `RISK_TEXT: dict[str, str]`: rule id to one plain sentence. Must hold every rule id in `policies/risk.yaml`.
  - `risk_line(rule_id: str) -> str`: the sentence, or a generic one for an unknown id.
  - `what_line(a, tool: str) -> str`: one plain sentence for what will happen, built from `a.seg_keys` and `a.seg_rules` (shell) or the tool name (MCP, write). Example: `gh pr merge N` becomes "Merge pull request 940." The number comes from `a.subject`.
  - `other_count(a) -> int`: how many command segments of the call are not gated (the safe ones), from the policy's own command splitter, never below 0.
  - `header(a, tool: str) -> list[str]`: the lines in this order: what line, risk line, also line ("Also in this call: N other commands that only read."), and, when two or more gated segments exist, "This call joins N risky commands. Ask the agent to send them one by one."

- [ ] **Step 1: Write the failing tests** in `test_ask_plain_text.py`: (a) every rule id in `policies/risk.yaml` has an entry in `RISK_TEXT` and every sentence has at most 20 words; (b) for the owner's real example (`cd /Users/x/iro && gh pr merge 940 --squash --delete-branch 2>&1 | tail -1; gh pr view 940 --json state -q .state; cat a.txt; pgrep -fc "cargo test"`) `what_line` starts with "Merge pull request 940" and `header` has an also line with "3 other commands"; (c) `question_for` output starts with the token line, then the what line, then the risk line, and the full command comes after them; (d) the macOS `text_for` has the same first three lines; (e) a call with two gated segments gets the "joins 2 risky commands" line.
- [ ] **Step 2: Run to see failure.** `uv run pytest tests/acceptance/test_ask_plain_text.py -q`. Expected: FAIL, module not found.
- [ ] **Step 3: Implement** `jade/askplain.py` as above. Write `RISK_TEXT` by hand from each rule's rationale in `policies/risk.yaml`; keep one line per rule. Register segment renderers as a list of `(re.Pattern, str)`; the fallback is "Run: " plus the first 60 characters of the segment key.
- [ ] **Step 4: Change `question_for` and `text_for`** to put `header(a, tool)` right after the token line (chat) or first (dialog). Keep the full command last and the clip rules (`HEAD_CHARS`, `TAIL_CHARS`). Keep the existing fixed option labels for now.
- [ ] **Step 5: Run the file and the existing ask tests.** `uv run pytest tests/acceptance -k "ask or dialog" -q`. Expected: PASS (update old expectations that name the old first line).
- [ ] **Step 6: Commit.** `git add jade/askplain.py jade/askchat.py jade/askdialog.py tests/acceptance/test_ask_plain_text.py` then `git commit -m "Plain first lines in the chat question and the dialog"`.

---

### Task 2: One risky action per call (agent text)

**Files:**
- Modify: `skills/jade/SKILL.md`, `docs/claude-md-jev-section.md`, `jade/hooks.py` (the gate message for a joined call, only if the text lives there)
- Test: `tests/acceptance/test_one_risky_action_text.py`

**Interfaces:**
- Consumes: `askplain.header` from Task 1.

- [ ] **Step 1: Write the failing test:** both files contain the sentence "Send one risky action per call" (exact text below), and `jade claude-md show` output contains it.
- [ ] **Step 2: Run to see failure.** `uv run pytest tests/acceptance/test_one_risky_action_text.py -q`. Expected: FAIL.
- [ ] **Step 3: Add the sentence** to both files: "Send one risky action per call. Do not join a merge, a delete or a push with other commands using `;` or `&&`; the human then sees one clear question." Keep the managed-section markers of the claude-md section intact.
- [ ] **Step 4: Run the test and `uv run pytest tests -k "claude_md or skill" -q`.** Expected: PASS.
- [ ] **Step 5: Commit** the three paths.

---

### Task 3: Scopes module (what a call may be generalised to)

**Files:**
- Create: `jade/scopes.py`
- Test: `tests/acceptance/test_scopes.py`

**Interfaces:**
- Consumes: `memory.eligibility(a, tool, tool_input, fp) -> str`, `memory.class_key(a) -> str`, `memory.keys(...)`, `a.seg_keys`, `a.subject`, `a.plan_cwd`.
- Produces:
  - `@dataclass(frozen=True) class Scope: kind: str; key: str; label: str; covers: str; short: str`. `kind` is `"kind_project"`, `"kind_any"` or `"merge_project"`. `label` is one line, at most 120 characters, secrets removed (for example `gh pr merge * in /Users/mobogift/Documents/iro`).
  - `project_of(subject: str, cwd: str) -> str | None`: the repository root the call acts in. Reads only a leading `cd PATH &&`; else uses `cwd`. Returns `None` for a variable, a subshell, `pushd`, a missing folder or no `.git` entry found walking up. Uses `os.path.realpath`; no subprocess.
  - `MERGE_FLAGS_ALLOWED: frozenset[str]` = `{"--squash", "-s", "--merge", "-m", "--rebase", "-r", "--subject", "-t", "--body", "-b", "--body-file", "-F", "--match-head-commit"}`. A merge with any other flag, including `--delete-branch`, `-d`, `--admin`, `--auto`, `-R`, `--repo`, gets no merge scope.
  - `offered(a, tool: str, tool_input: dict | None, fp: dict | None, cwd: str) -> list[Scope]`: at most two scopes. `kind_project` and `kind_any` only when `memory.eligibility` is `"class"` (and a project is known for the first). `merge_project` only for exactly one gated segment `gh pr merge <digits> [allowed flags]`, a known project, no human-only class, level not critical. Empty for `never`, blind calls, inline programs and joined risky commands.
  - `keys_for(a, tool, tool_input, fp, cwd) -> list[str]`: the keys of the scopes `offered` would return, used by lookup. Same rules, so a call with a new flag has a different or no key.

- [ ] **Step 1: Write the failing tests** (names and assertions):
  - `test_merge_scope_for_a_plain_squash_merge`: `gh pr merge 940 --squash` in a temp repo gives one scope of kind `merge_project` whose label contains `gh pr merge *` and the repo path.
  - `test_merge_scope_refused_for_each_excluded_flag` (parametrized: `--delete-branch`, `-d`, `--admin`, `--auto`, `-R other/repo`, `--repo other/repo`, `--force`): `offered(...) == []` for kind `merge_project`.
  - `test_merge_scope_unknown_project`: `cd $REPO && gh pr merge 940`, `(cd x && gh pr merge 940)`, `pushd x && ...`, a cwd with no `.git` all give no scope.
  - `test_cd_to_another_project_changes_the_key`: `cd A && gh pr merge 1` and `cd B && gh pr merge 1` give different keys; PR number 1 and 2 in A give the same key.
  - `test_no_scope_for_human_only_blind_inline_and_joined`: each gives `[]`.
  - `test_kind_scopes_for_a_class_call`: a call whose `memory.eligibility` is `class` (use `gh issue comment 5 --body hi`; check it really lands in `class`, else pick the nearest real example from `tests/acceptance/test_memory_w101.py`) offers `kind_project` and `kind_any`.
  - `test_label_has_no_secret_and_is_at_most_120_chars`.
- [ ] **Step 2: Run to see failure.** `uv run pytest tests/acceptance/test_scopes.py -q`. Expected: FAIL, module not found.
- [ ] **Step 3: Implement** `jade/scopes.py`. Scope key: `sha256(canonical({"kind": kind, "family": <normalised segment keys>, "project": <realpath or None>}))` using `util.canonical` and `util.sha256`. For `kind_any` the key is `memory.class_key(a)`.
- [ ] **Step 4: Run** `uv run pytest tests/acceptance/test_scopes.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `jade/scopes.py tests/acceptance/test_scopes.py`.

---

### Task 4: Store scopes and match them

**Files:**
- Modify: `jade/ledger.py` (schema near line 48-52, the `_migrate` column list near line 76, `record_human_answer` near line 312, `human_answers` near line 348), `jade/memory.py` (`lookup`, `step`)
- Test: `tests/acceptance/test_scope_memory.py`

**Interfaces:**
- Consumes: `scopes.Scope`, `scopes.keys_for`.
- Produces:
  - New columns: `asks.scopes_json TEXT`; `human_answers.scope_kind TEXT`, `scope_key TEXT`, `scope_label TEXT`. Added through the existing migration list, so an old ledger gains them.
  - `Ledger.record_human_answer(..., scope: dict | None = None)`: when given, stores `scope_kind`, `scope_key`, `scope_label` (label redacted for secrets, at most 120 characters).
  - `memory.lookup(db, a, tool, tool_input, fp, cwd: str = "")`: the SQL gains `OR scope_key IN (...)` with `scopes.keys_for(...)`, only when eligibility is not `never`.
  - `memory.step` passes `cwd` through.

- [ ] **Step 1: Write the failing tests:**
  - `test_old_ledger_gains_the_scope_columns`.
  - `test_confirmed_merge_scope_covers_another_pr_number_in_the_same_project`: record a `strong` allow with a `merge_project` scope for `gh pr merge 940 --squash` in project A; `lookup` for `gh pr merge 941 --squash` in A returns it.
  - `test_merge_scope_does_not_cover_another_project`, `..._a_delete_branch_merge`, `..._an_admin_merge`.
  - `test_a_hint_with_a_scope_decides_nothing` (strength `hint` is never matched).
  - `test_scope_row_expires_like_any_answer` (30 days after last use).
- [ ] **Step 2: Run to see failure.** `uv run pytest tests/acceptance/test_scope_memory.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the columns, `record_human_answer(scope=...)`, and the extended `lookup`. Keep `eligibility` unchanged: a merge stays `exact` there; the scope match is a separate OR in `lookup`.
- [ ] **Step 4: Run** the new file plus `uv run pytest tests/acceptance/test_memory_w101.py tests/acceptance/test_ledger_scale_w58.py -q`. Expected: PASS (the scale test must still see no table scan).
- [ ] **Step 5: Commit** the changed paths and the test.

---

### Task 5: Scope choices in the chat question, recorded as a proposal

**Files:**
- Modify: `jade/askchat.py` (`question_for`, `step` insert, `settle`)
- Test: `tests/acceptance/test_ask_scope_choices.py`

**Interfaces:**
- Consumes: `scopes.offered`, `Ledger.record_human_answer(scope=...)`.
- Produces:
  - Option labels as module constants: `ALLOW = "Allow"`, `DENY = "Deny"`, `SUGGEST_PROJECT = "Allow, suggest for this project"`, `SUGGEST_ANY = "Allow, suggest for any project"`. At most four options in total. The description of a suggest option is the scope's `covers` plus "You confirm it later in a terminal. Undo with `jade knows rm`."
  - `question_for(ask_id, a, tool, ctx, scopes: list | None = None)`: adds the suggest options when scopes are given.
  - The `asks` insert stores `scopes_json`.
  - `settle`: accepts the four labels. For a suggest label it records the allow answer as a `hint` with the matching scope from `asks.scopes_json`; an unknown label is ignored as today.

- [ ] **Step 1: Write the failing tests:**
  - `test_question_lists_suggest_options_for_a_merge`: options are `Allow`, `Deny`, `Allow, suggest for this project`; the description names `gh pr merge *` and the project.
  - `test_no_suggest_option_for_a_never_call_or_a_joined_call`.
  - `test_answer_suggest_records_a_hint_with_the_scope` and `..._decides_nothing_yet` (a second identical call still asks).
  - `test_plain_allow_records_no_scope`.
  - `test_forged_question_with_an_extra_option_is_refused` and `test_settle_ignores_an_unknown_label` (Review Focus 3).
- [ ] **Step 2: Run to see failure.** `uv run pytest tests/acceptance/test_ask_scope_choices.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** The hook's rewrite of the AskUserQuestion input already writes the stored question; make sure the stored `question_json` carries the new options so the rewrite stays exact.
- [ ] **Step 4: Run** the new file and `uv run pytest tests/acceptance -k "ask or chat" -q`. Expected: PASS.
- [ ] **Step 5: Commit.**

---

### Task 6: Show the scope in `jade review confirm` and `jade knows list`

**Files:**
- Modify: `jade/ops.py` (`review_confirm` near line 800, `knows_list` near line 839, `_pending_hints`)
- Test: `tests/acceptance/test_review_scope.py`

**Interfaces:**
- Consumes: `human_answers.scope_label`, `scope_kind`.

- [ ] **Step 1: Write the failing tests** with a fake `ask` and `say`: (a) a hint with a scope prints "Would become standing for: gh pr merge * in /path (30 days after last use)" and its group key includes the scope, so two scopes do not merge into one group; (b) answering `n` leaves the row a `hint`; (c) `knows_list` prints the scope label for a scoped row; (d) a hint without a scope prints as today (existing tests unchanged).
- [ ] **Step 2: Run to see failure.** `uv run pytest tests/acceptance/test_review_scope.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the changes. Keep `Type yes to confirm:` as is.
- [ ] **Step 4: Run** the new file and `uv run pytest tests/acceptance -k "review or knows" -q`. Expected: PASS.
- [ ] **Step 5: Commit.**

---

### Task 7: A better `jade knows edit` template

**Files:**
- Modify: `jade/ops.py` (`knows_edit` near line 880)
- Test: `tests/acceptance/test_knows_template.py`

**Interfaces:**
- Produces: the template builder `knows_template(rows) -> str` (new, pure, in `jade/ops.py`).

- [ ] **Step 1: Write the failing tests:** (a) rows with the same kind and project become one commented line `# I am fine with: <scope label>  [rule]  (risk: <risk_line>)`; (b) rows without a scope use a short label of the exact call, still grouped by rule and first word; (c) the file starts with the two header lines and then two commented model sentences (an ssh host; a GitHub or GitLab project) with placeholders; (d) every line starts with `#`; (e) at most 40 lines.
- [ ] **Step 2: Run to see failure.** `uv run pytest tests/acceptance/test_knows_template.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `knows_template` and use it in `knows_edit` only when the file does not exist yet (an existing file is never rewritten).
- [ ] **Step 4: Run** the new file and `uv run pytest tests/acceptance -k "knows" -q`. Expected: PASS.
- [ ] **Step 5: Commit.**

---

### Task 8: Docs, threat rows, changelog

**Files:**
- Modify: `docs/12-jev-decides-phase1.md` (new short section: clear questions and scopes), `docs/05-threat-model.md` (T30 and its evidence row), `CHANGELOG.md` (`[Unreleased]`)
- Test: the existing docs truth tests (`test_every_threat_row_has_evidence_that_exists`, documented commands exist)

- [ ] **Step 1: Write T30** in `docs/05-threat-model.md`: "A scope that covers more than the owner meant". Mitigation: the click records a hint only; confirm in a terminal; allow-list of merge flags; one named project; lifetime 30 and 180 days. Evidence: the tests of Tasks 3, 4 and 5 by file and name.
- [ ] **Step 2: Write the docs section and the CHANGELOG entries** in simple English.
- [ ] **Step 3: Run the whole gate:** `uv run ruff check`, `uv run pytest -n auto --dist loadgroup -m "not timing" -q`, `uv run pytest -m timing -q`. Expected: all pass; the docs tests pass.
- [ ] **Step 4: Commit** the three docs paths. Then open the PR (the owner decides when to merge).

---

## Self-review (done)

- **Spec coverage:** section 5 step 1 is Task 1 and 2; step 2 is Tasks 3 to 6; step 3 is Task 7; decisions 7.1 and 7.2 are Tasks 3 and 5; the threat row is Task 8.
- **Type consistency:** `Scope` fields, `scopes.offered` and `keys_for`, `record_human_answer(scope=)` and `memory.lookup(..., cwd)` are named the same in every task that uses them.
- **Proportion:** the plan names files, signatures, values and test names. Bodies are left to the builder.
- **Open for the builder to settle (marked as builder calls):** the exact `RISK_TEXT` wording; whether `gh issue comment` is `class` (Task 3 test checks); how `other_count` splits segments; glab merge flags are not in this plan (follow-up).
