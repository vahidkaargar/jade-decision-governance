# Repository and branch inventory, and capability matrix

This section answers two questions. Part B: what exists in the JADE repository now (branches, worktrees, tags, PRs, work that lives only on disk, id collisions, deletion hazards). Part C: what JADE can really do today, with a code status, a live status for this Mac, and a need status. Part D lists what not to build. Part E lists the owner's decisions. It merges audits A1 to A8 and research R1 to R6, and re-checks git.

Tags: [VERIFIED] has concrete evidence (file, symbol, commit, command, or evidence id such as A3). [INFERENCE] is my reasoning. [RECOMMENDATION] is advice. [UNRESOLVED] could not be settled. Anything that changes safety behaviour is a proposal and needs the owner's approval.

## 0. Reading rules

**Settled assumption (reversible).** [INFERENCE] "v6" is not a label in the repo (A1). It means: the 0.6.x line (tags v0.6.0, v0.6.1; installed 0.6.1), plus main after 0.6.1, plus the unmerged stack planned as 0.7.0 (PR 42 to 47). Other readings: [UNRESOLVED, A1].

**Snapshot.** [VERIFIED] I re-checked git on 2026-11-16 with read-only commands. Where A1 and my check differ, mine wins. Numbers about dirty files are "at the time of check" and will move.

Two trees matter. Installed: 8100ea1 = tag v0.6.1, what runs on this Mac [VERIFIED, `jade version`, A2, A3]. origin/main: e9412f3 = 0.6.1 plus PR 48 (SI-13, repo `docs/13`) [VERIFIED, `git rev-parse`].

**Code status.** It says what code and tests show. It does not say the code protects this Mac.
- IMPLEMENTED: code exists and a pass was seen (a test or probe run by an auditor, or CI read for that ref).
- CODE-ONLY: code exists, no pass was seen (tests skipped locally, CI not read).
- PARTIAL: works for some cases; the gap is named.
- UNVERIFIED: a claim or code exists; nobody saw it work.
- ABSENT: not in code on any ref I checked. It does NOT mean "to build". Read Need.
- REDUNDANT: exists, nothing reads it.

**Live status (this Mac).** ACTIVE: runs now and affects calls. IDLE: installed, but level `off` or not triggered. NOT HERE: only on main after 0.6.1 or on a branch. UNKNOWN. n/a.

**Need.** `proven`: an incident, probe or measured number shows the problem (cited). `unproven`: plausible, no evidence. `rejected`: do not build now (Part D). `keep`: exists and is useful.

[VERIFIED, A3 and A4] The installed JADE is 0.6.1 at level `off`, gate mode `enforce`. At level `off` only SI-09 to SI-12 block. SI-08 (push to a default branch) and all risk rules are advisory.

---

# Part B. Repository and branch inventory

## B1. What changed since A1 (read this first)

| Item | Now [VERIFIED by my re-check] |
|---|---|
| origin/main | e9412f3, 3 commits after v0.6.1. PR 48 merged 2026-11-16T12:27Z. `pyproject.toml` still says 0.6.1. |
| PR 48 | MERGED: SI-13, repo `docs/13`, `jade/harness_audit.py`, threat row T30 "test harness runs payloads in a real shell", W-115 row. |
| Open PRs | 6 (42 to 47). A1 said 7. |
| PR 42, 44, 46 | Now contain main. PR 42 `design-clear-questions` (dcq) 8866872, +18. PR 44 `open-items-scopes` ed0d26d, +25. PR 46 `open-items-shell-startup` a18bf71, +23; its threat row is now T32. |
| PR 43, 45 | Stale. PR 43 `open-items-plain-text` 3b5a456: 3 behind main, 4 behind dcq. PR 45 `open-items-review-output` 8c8163e sits on the OLD scopes tip 9956e4f (12 ahead, 5 behind the new scopes). |
| PR 47 round 4 | SUPERSEDED, see the correction note after this table. At the time of this draft: committed locally as 967b166, NOT pushed, origin and the PR head c6ab27a. At the time of check the worktree also had four modified tracked files: `CHANGELOG.md`, the escaped-operators spec, `jade/shellnorm.py` (about +38 -17), `tests/test_redirect_round4.py` (about +72 -1). Work in progress. |
| Checks | PR 42 to 47: both checks `SUCCESS`, MERGEABLE CLEAN on GitHub, for pushed heads. PR 47's are for c6ab27a, not 967b166. I ran no tests. |

**Correction (master plan check, 2026-11-16) [VERIFIED `git rev-parse origin/claude/heuristic-hamilton-0eae1d`, `git worktree list`, `gh pr list`].** PR 47 head on origin is now `c676801`, and the PR 47 worktree is at `c676801`. So the claims in this draft that the round-4 fix is "local only", "not pushed" or lives only at `967b166` are stale, and so is "PR 47 as pushed (c6ab27a)" in Part B6 and Part E. The weakness measured in A1 section 7.2 was at `c6ab27a`; d12 section 0 probed `c676801` and the four A1 commands block there. [UNRESOLVED] Whether `c676801` is weaker than main on other shapes, and whether `967b166` holds anything `c676801` lacks (d13 U-19). Part E item 1 stands in a changed form: do not merge PR 47 before V6-04 verifies it. Also: Part E item 7 (add nothing for the `jev` key) is overruled by d12 section 2 row 5 (build it in V6-28); Part E item 4 merge order is settled by d13 X-16 (W-97 order).

## B2. Tags and releases

[VERIFIED, A1 and `git tag`] Eight tags: v0.3.0 to v0.3.3, v0.4.0, v0.5.0, v0.6.0, v0.6.1, all ancestors of origin/main. v0.6.1 is GitHub "Latest". No 0.7.0 tag; no branch bumps the version. The installed tool lacks SI-13.

## B3. Worktrees

| Path (under the main checkout unless absolute) | HEAD | Branch | State now |
|---|---|---|---|
| main checkout | e2efe02 | `harness-tools-w97` (local) | 9 behind origin/main. DIRTY: `BACKLOG.md`, `work-items.jsonl` (116 rows against 95 in HEAD; 21 new rows W-96 to W-116). Untracked `sh.sh`, `.DS_Store`. File-mode noise (A1). |
| `.claude/worktrees/harness-tools-w97` | 8866872 | `design-clear-questions` | NAME MISMATCH. Untracked: 4 plan and spec files ("spec B"), 3 probe runtime files. |
| `.claude/worktrees/heuristic-hamilton-0eae1d` | 967b166 | `claude/heuristic-hamilton-0eae1d` (PR 47) | 967b166 local only; 4 modified tracked files (B1). |
| `.claude/worktrees/docs-approvals-wording` | ff84b03 | same name | clean. Local branch only. |
| `pr44-scopes`, `pr46-shell-startup`, `sandbox-w115` (merged, redundant), `plan-v6` (this phase) | ed0d26d, a18bf71, 8f796e5, e9412f3 | scopes, shell-startup, sandbox, `plan-jade-v6-master` | clean. |
| 5 detached scratch worktrees in the scratchpad | 3c7d2e9, c6ab27a, d10abf9, e1a76a3, 8100ea1 | detached | [VERIFIED, A1] each commit is on PR 47 or main. Review copies. |

[VERIFIED] `git stash list` is empty. [RECOMMENDATION] Stage explicit paths, never `git add -A`, in the main checkout.

## B4. Unmerged work

Counts are `git rev-list --count` against origin/main.

| Ref (PR, base) | Tip | Content | Status; where the work lives |
|---|---|---|---|
| `design-clear-questions` (PR 42, main) | 8866872 | `jade/askplain.py` (plain first lines in the chat question), `jade/scopes.py`, `jade knows edit`, threat row T31. | PARTIAL. A1: 207 tests passed on an older tip; CI SUCCESS on current tip. Origin and PR. |
| `open-items-plain-text` (PR 43, dcq) | 3b5a456 | Push and merge line hardening (flag and refspec allow-list). | PARTIAL, STALE. |
| `open-items-scopes` (PR 44, dcq) | ed0d26d | Merge scope needs `--repo`; a newer deny beats an older allow. | PARTIAL. |
| `open-items-review-output` (PR 45, scopes) | 8c8163e | One-line `jade review` and `jade knows`; Jev sees denies; question v2 file. | PARTIAL. v2 calibration UNVERIFIED (W-102 dirty). On old scopes tip. |
| `open-items-shell-startup` (PR 46, dcq) | a18bf71 | Human-only class `HO-shell-startup` (T32). Known gaps. | PARTIAL. |
| `open-items-shell-startup-waves4-6` (no PR) | 7507c10 | Waves 4 to 6 of that guard, parked (W-98). | On origin and, at the 2026-11-16 re-check, also as a local branch at `7507c10`. Never delete. (Earlier text said remote only.) |
| `claude/heuristic-hamilton-0eae1d` (PR 47, main) | c6ab27a pushed at the time of this draft [SUPERSEDED, origin is now c676801, see the correction note under Part A]; 967b166 local | SI-08 fix for a redirect that hid the rest of a push. `jade/shellnorm.py`, about 4,400 test cases. | A1 probe 7.2: c6ab27a blocks LESS than main on one command. 967b166 plus the diff: UNVERIFIED. Local worktree only. |
| `docs-approvals-wording` (no PR) | ff84b03 | README and repo `docs/08`: chat approval 10 min, signed permit 30 min. | Docs only. LOCAL ONLY. |


## B5. Stack shape and conflicts

[VERIFIED] by `git merge-base --is-ancestor` and `rev-list --count`:

```
origin/main e9412f3
 +-- design-clear-questions 8866872   PR 42
 |    +-- open-items-scopes ed0d26d          PR 44
 |    +-- open-items-shell-startup a18bf71   PR 46
 |    +-- open-items-plain-text 3b5a456      PR 43   STALE
 |    +-- open-items-shell-startup-waves4-6  no PR   STALE
 +-- open-items-review-output 8c8163e       PR 45   on OLD scopes tip
 +-- claude/heuristic-hamilton-0eae1d       PR 47   independent
 +-- docs-approvals-wording                         independent, local only
```

[INFERENCE] The dirty W-97 merge order (42, then 43, 44, 46, then 45) now needs a rebase of PR 43 and PR 45 first. Predicted conflicts, re-run by me with old-style `git merge-tree` (read only) [VERIFIED]: PR 42 x PR 47: `CHANGELOG.md` only. PR 46 x PR 47: `CHANGELOG.md` and `jade/shellnorm.py`, the only real code conflict. PR 45 x PR 42, x main, x PR 46: repo `docs/05` (T30 row; PR 46 adds `CHANGELOG.md`). PR 43 x PR 42: none.

## B6. Work that exists only outside git history (loss risk)

| # | What | Where | Risk |
|---|---|---|---|
| 1 | PR 47 round-4 fix 967b166 and four modified tracked files | `heuristic-hamilton-0eae1d` worktree | HIGH. [VERIFIED] origin c6ab27a (at the time of this draft; origin is now c676801) lets `cat<<<'git push origin main'\|sh` pass; main blocks it (A1). [UNRESOLVED] Whether 967b166 plus the diff covers every R4 item. |
| 2 | "Spec B": 3 plans, 1 design (approved 2026-11-14; build after 0.7.0 merges), 3 probe files | `harness-tools-w97` worktree, UNTRACKED | HIGH. Removing the worktree by name deletes them. |
| 3 | Backlog: 21 rows W-96 to W-116, `BACKLOG.md` edits | Dirty main checkout | MEDIUM to HIGH. Only copy of the PR 47 review rounds. |
| 4 | `docs-approvals-wording` ff84b03; waves 4 to 6 | Local branch; branch on origin and local | MEDIUM each. |

## B7. Collisions

**Work-item ids.** [VERIFIED] `origin/main:work-items.jsonl` has 96 rows: W-1 to W-95 plus W-115 (the PR 48 sandbox row). On main, W-96 to W-105 are FEATURE ids (CHANGELOG, docs, commits). The dirty checkout reuses them for different open items, so W-97, W-99, W-101 to W-105 each have two meanings (A1). Example: W-102 is "Jev decides in your place" in CHANGELOG and "Jev question v2 is not calibrated" in the dirty backlog. W-115 collides on main itself (sandbox row, human, P1; PR 47 meaning, agent, P0). PR 47 commits and tests cite the dirty numbers (Part E, 2).

**Threat rows.** [VERIFIED] Same id, different meaning, by ref:

| Ref | T30 | T31 | T32 |
|---|---|---|---|
| origin/main | test harness in a real shell | none | none |
| dcq, scopes | test harness | scope covers more than meant | none |
| shell-startup (PR 46) | test harness | scope | shell start-up edit |
| PR 43, PR 45 | scope | none | none |
| waves4-6 | scope | shell start-up edit | none |

[INFERENCE] A1, A3, A5 say "T31 = shell start-up, T30 = scope": true for old tips only. PR 43 and PR 45 still carry old T30 and will conflict in repo `docs/05`.

**Other ids.** [VERIFIED, my grep] SI-01, SI-02 and SI-05 to SI-13 are present in `policies/safety.yaml` on origin/main; SI-03 and SI-04 were never assigned and are reserved (file header; corrected by the master plan check, which re-read the file). SI-13 is on main, dcq and shell-startup, not on PR 45 and PR 47. `HO-shell-startup` exists only on PR 46 and waves4-6. repo `docs/12` s12 is on dcq, scopes, review-output, shell-startup, not main. A1 Q3 (which of PR 42 or 48 keeps T30) is moot: PR 48 merged.

## B8. Stale refs and deletion hazards

[VERIFIED, A1 and my count] About 40 local and 41 remote-tracking branches are merged into main. They are history. The traps:
- `.claude/worktrees/harness-tools-w97` holds `design-clear-questions`. The branch `harness-tools-w97` is the main checkout.
- Local `harness-tools-w97` is e2efe02; `origin/harness-tools-w97` is 342bdc2. `worktree-harness-tools-w97` is local 342bdc2; its origin twin is 068e92f (patch-equivalent per A1, not re-run).
- `git fetch --prune` writes refs: the owner's call.

[RECOMMENDATION] Delete nothing in this phase. Before any cleanup check B6, and also: branch `open-items-scopes` while PR 45 targets it (deleting a base branch can close PR 45, A1). The 5 scratch worktrees are safe to prune, by path, never by the shared name.

## B9. What the inventory says

- [INFERENCE] The release line is healthy (A8). The risk is the pile on top: six open PRs, local-only branches, the backlog in a dirty tree. One wrong cleanup can lose it.
- [INFERENCE] Two things block any v6 design that touches safety: PR 47 is not safe to merge as pushed, and the id collisions make references ambiguous.
- [RECOMMENDATION] The owner decides Part E before v6 design work.

---

# Part C. Capability matrix

Columns: Capability | Code | Live | Need | Evidence and note. Code statuses used (six): IMPLEMENTED, CODE-ONLY, PARTIAL, UNVERIFIED, ABSENT, REDUNDANT. Need values used (four): keep, proven, unproven, rejected. [UNRESOLVED] The owner's spec text is not in the evidence folder (d00 A.1), so I could not check these six names against the owner's six. If they differ, map them in one line here before the matrix is used. Some Code cells carry a short qualifier after the status word, for example `PARTIAL (defect)`; the first word is the status. Ref is origin/main e9412f3 unless a row names another. [INFERENCE] Audits A2 to A8 probed 8100ea1; rows carry to e9412f3 because PR 48 adds only SI-13, `jade/harness_audit.py`, repo `docs/13`. Lifecycle and crash tests are SKIPPED on this Mac (W-93; A2, A5), so a local green is weaker than CI.

**What this means.** [VERIFIED, my count of the first word of each cell, C1 to C9, 79 rows] Code: IMPLEMENTED 17, CODE-ONLY 2, PARTIAL 30, UNVERIFIED 4, REDUNDANT 1, ABSENT 25. Of the 17 IMPLEMENTED rows, 8 are ACTIVE, 7 are IDLE (level `off`), 1 is NOT HERE, 1 is CI only. Need: 26 `proven`, 19 `unproven`, 3 `rejected`, 31 `keep` (the Dead pieces row had the non-standard Need word "keep or delete: owner", already counted with `keep`; it now says `keep`, so the totals do not change). [INFERENCE] Live protection on this Mac is lower than the code count suggests: at level `off` the gate is mostly advisory (C2). A large IMPLEMENTED count is not protection, and ABSENT is not a to-do list.

## C1. Decision pipeline and Jev

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| Jev transport, typed errors, retries, 20 s deadline; answer check; exit codes 0/3/4/5/6; threshold bands per risk | IMPLEMENTED | ACTIVE | keep | [VERIFIED] `adapter.py`, `thresholds.py`; A2: 123 and 153 tests passed; probe 0.70 at low, 0.85 at high. Jev returns probabilities only, no explanation field (A2). Provider URL is a constant (A8). |
| Bands calibrated on real calls | ABSENT | n/a | proven | [VERIFIED] A6: `jade eval run` shows no labeled rows; repo `docs/12` s10 "judged against fixtures only". |
| Decision state machine as a record | CODE-ONLY | ACTIVE | keep | [VERIFIED] `state_machine.py`, one SQLite transaction per step (A2, A5). Lifecycle tests skipped locally; CI not read. |
| State machine as authority | PARTIAL | IDLE | unproven | [VERIFIED] A2 probe `agent_may`: the agent can send `jev_result_accept`; `hooks._gate` never reads ACCEPTED. |
| Dead pieces: states `WAITING_DEPENDENCY`, `RETRY_SCHEDULED`; FAST and DEEP routes; policy keys `abstain_to`, `max_revisions`, `each_question_includes`, `deadline_default_h`, `fallback`; the `score` primitive; `q.risk.*`, `q.routing.*` and two more question families | REDUNDANT | n/a | keep | Owner decides: keep or delete (the Need word is `keep` until then). [VERIFIED] A2 s3, s7, s10; A5 s3, s5; `git grep` finds no reader. "Abstain goes to DEEP or human" is a promise, not behaviour. W-83 says delete; A7 says use the keys as the human-question field list. |
| Evidence collected by JADE for `jade ask`; evidence record with provenance and freshness | PARTIAL | n/a | unproven | [VERIFIED] The agent passes the evidence file (`cli._read_state`). Gate evidence IS collected (`policy.Assessment`). Only an `evidence_source` string exists; freshness check PF-07 runs only in `jade route` (A2 s5). `evidence_source` is sent to Jev; repo `docs/03` says not. |
| Contradiction detection | ABSENT | n/a | unproven | [VERIFIED] Only the post-hoc `evals.auto_label` heuristic (A2). No incident cites it. |
| Repeat detection | PARTIAL | n/a | proven | [VERIFIED] Exact repeats are caught (`ledger._anchor`). Rewording and `--no-cache` are not: 7 asks made 5 provider calls; `--no-cache` flipped exit 3 to 0 (A2 probe). [INFERENCE] Sample shopping is possible. |
| Check strictness tied to operation risk | ABSENT | n/a | proven | [VERIFIED] A2 probe: same answer gives exit 0 at `--risk low` and exit 3 at `--risk high`. The agent picks `--risk` (default `low`). |
| Preflight PF-01 to PF-08; revision limit; verdict names | PARTIAL | IDLE | unproven | [VERIFIED] Tested, but only `jade route` calls preflight. The ask still runs after 3 revisions (probe `loop`). Daily budget 500 only warns. SAFE_ALTERNATIVE is ABSENT; `NEEDS_EVIDENCE` means two things (A2 s10). [RECOMMENDATION] One verdict table in code. |

## C2. Enforcement and bypass

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| PreToolUse hook gate for shell, write, MCP (`onFailure: block`) | IMPLEMENTED | ACTIVE | keep | [VERIFIED] A3 live wiring on host 2.1.314; `jade check`. Native primitive (R1 3.1, [INFERENCE] from summary). |
| What the gate does at level `off` | PARTIAL | ACTIVE | proven | [VERIFIED] A3: only SI-09 to SI-12 block; 3416 of 3424 gated calls had no host prompt in 7 days. JADE is the only gate and mostly advisory. `jade check` prints "ok level: off". |
| Permits (plan hash, session, agent, key seal, expiry, single use, HEAD); signed approver; SI-07 hold | IMPLEMENTED | ACTIVE for signed approver; IDLE otherwise | keep | [VERIFIED] A3 s4, P9: forge refused on signed, accepted on the weaker terminal backend. SI-07 gap: an agent can set `JADE_SESSION_ID` (A5, [INFERENCE]); treat as a speed bump. |
| Chat grant scope | PARTIAL | IDLE | proven | [VERIFIED] A3 probe C4: Allow for a staging `kubectl apply` was spent on a prod text in another cwd. |
| SI-08 push to default branch (text scan) | PARTIAL | IDLE | proven | [VERIFIED] A3: `git -C 'a\ b' push`, `git send-pack`, `g=git; $g push`, `gh api .../merge`, scripts and wrappers pass (W-99, W-107, W-110, W-112, W-114). |
| Pre-push guard; remote branch protection | UNVERIFIED | UNKNOWN | proven | [VERIFIED] `githook.py` exists; JADE does not install it (A3). [UNRESOLVED] Install state here; remote protection (A8). This is the real SI-08 boundary: cheap and deterministic. |
| SI-08 redirect fix (PR 47) | UNVERIFIED | NOT HERE | proven | [VERIFIED] A1: c6ab27a (the old head; origin is now c676801, not re-probed here) blocks less than main on a glued here-string. [UNRESOLVED] 967b166 plus the diff not probed. Do not merge c6ab27a as pushed. |
| Memory silences a whole KIND of critical call (R-01 firewall, R-03 deploy, R-04 key removal) | PARTIAL (defect, W-116) | IDLE | proven | [VERIFIED] `memory.eligibility` never reads level; probes (A3 s6, A4 s9.1, A6 s3.3). The Jev proxy refuses critical; memory does not. Docs say critical is asked (Part E, 6). |
| Human-only classes (delete, money, release, wiring, mod, terminal) | IMPLEMENTED | IDLE | keep | [VERIFIED] A3, A4 probes: never remembered, never sent to Jev. `HO-shell-startup` PARTIAL on PR 46. |
| Fail closed on crash, budget, ledger error | PARTIAL | ACTIVE | keep | [VERIFIED] A3 s8g: closed for gated or write-like calls; open for malformed events and unknown MCP tools. Host below 2.1.295 fails open (A8). |
| MCP coverage | PARTIAL | ACTIVE | proven | [VERIFIED] A3: MCP file readers can read the key that signs permits (W-73); GUI tools medium M-99, not gated. |
| Subagent coverage | PARTIAL | IDLE | keep | [VERIFIED] Dialog in a subagent verified on 2.1.314, cells 8c, 8d (`evals/probes/hook_ask/RESULTS.md`; A3). `bypassPermissions` NOT run for cells 8, 8b, 8c (RESULTS.md "Not run"). |
| Three bypass gaps: writes to agent config (`.mcp.json`, `.vscode/settings.json`, `.codex/config.toml`, project `CLAUDE.md`); fetch-and-run hidden by a trailing comment (`\| sh # x`); child agent launch (`claude -p --dangerously-skip-permissions`, `codex exec`) | ABSENT | NOT HERE | proven | [VERIFIED] R6 probes 3 and others, A3 8e: all rated low R-99 on main and scopes. Only `.claude/settings.json` is high. Each needs a test and a fix [RECOMMENDATION]. [UNRESOLVED] Whether `--bare` skips managed hooks (R1 3.4). |
| Other harnesses (Codex, Cursor, Gemini, Aider, Cline) | ABSENT | n/a | unproven | [VERIFIED] no adapter on any ref (A3, A8). [INFERENCE] R1 5: Codex and Cursor have exit-2 pre-tool hooks (summaries). |
| OS containment of the agent | ABSENT | n/a | unproven | [VERIFIED] SI-13 covers test harnesses only (A3). Option: native sandbox (C11). |
| SI-13 guard for test harnesses | IMPLEMENTED | NOT HERE | keep | [VERIFIED] Present on e9412f3 (file check: `policies/safety.yaml`, repo `docs/13`, `jade/harness_audit.py`). Tests run by A1 on PR head 8f796e5: 100 passed, 1 failed (archive copy without `.git`, not a product failure); CI green on the PR. macOS only; Linux backend ABSENT (W-115). |
| Hook wiring tamper resistance | PARTIAL | ACTIVE | keep | [VERIFIED] detect and block at the gate only (A3, R1). Option: managed settings (C11). |
| Shell classifier (`shellnorm`, `interp`, `samecmd`, `inert`, `blind`, rules) | IMPLEMENTED | ACTIVE | keep | [VERIFIED] R3: about 4,100 lines. [INFERENCE] R3: no engine parses shell. |
| Pre-trust repo config (hooks in `.claude/settings.json`, MCP auto-start); MCP tool description changes | ABSENT | n/a | unproven | [VERIFIED] R6: nothing in `jade/` names them. A hook cannot stop them (they run before hooks). Detect only. |

## C3. Modes, levels, autonomy

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| Gate mode `enforce` / `advisory` / `off`; level `unset` / `off` / `low` / `medium` / `high` | IMPLEMENTED | ACTIVE | keep | [VERIFIED] A4 probe matrix (120 rows); tests `test_level_si12_w99`, `test_memory_w101`, `test_jev_proxy_w102`. `off` is not "only watch": three hooks still run. `unset` = 0.4.0 behaviour. |
| Level provenance check (config must equal last ledger event) | PARTIAL | ACTIVE | keep | [VERIFIED] `ops.py:615-647` (A4): exists for `level`; ABSENT for `gate_mode`. |
| Safety and autonomy as separate dimensions | PARTIAL | ACTIVE | proven | [VERIFIED] A4 s6: setting any level also turns on SI-12 and firm SI-09 to SI-12, human-only raising, chat, memory, Jev. `low` and `medium` differ only by Jev bar and egress. |
| Autonomy names MANUAL, ASSISTED, BALANCED, HIGH, MAXIMUM; MEDIUM-HIGH, HARD; per-project level | ABSENT | n/a | unproven | [VERIFIED] none of these words in `jade/`, `docs/`, `policies/` (A4). `level high` today means LEAST autonomy. A4 s8: BALANCED and HIGH equal `level medium` and `level low`, so the names are aliases. repo `docs/12` s8 lists per-project level as future. |
| "Jev off" switch (needed for MANUAL and ASSISTED) | ABSENT | n/a | unproven | [VERIFIED] Jev is asked whenever preferences or past answers exist (A4 s3); at `high` the text still goes to the provider as shadow. [RECOMMENDATION, safety change, needs approval] One key `jev`, with the same provenance check as `level`. |
| Host permission mode as a hidden knob (auto and bypass get the chat ask; others get a terminal permit) | PARTIAL | IDLE | unproven | [VERIFIED] A4 s4 probe `hostmodes`. Overlaps native auto mode (C11). |

**Challenge to the owner's spec.** [RECOMMENDATION] The six names are not a capability to match. They are aliases of `level` and a not-yet-existing `jev` key. First question: what does the owner do differently at MEDIUM-HIGH than at HIGH? No answer means do not build the name (Part D). A4 s8: MAXIMUM is a safety decision for the owner; it must not be reached by loosening never-lists.

**Two lists, not one.** [VERIFIED, A4 s6] Safety floors are constants and do not vary with level: SI ids, human-only classes, never-remember lists. Autonomy knobs: `level` (exists), memory scope (exists on PR 42 to 45), `jev` (ABSENT). The defect is coupling: the `level` knob turns on safety rules as a side effect. [RECOMMENDATION, needs approval] Keep the lists apart in repo `docs/12`; an autonomy knob never changes a floor.

## C4. Memory and the Jev proxy

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| Memory of confirmed answers (hint, then strong row; 30-day use; 180-day cap) | IMPLEMENTED | IDLE | keep | [VERIFIED] `memory.py`; `test_memory_w101.py` (A4). `--all` confirms every group after one yes (A6). Defects: critical class (C2); use renews life; `right` mark ignored; 82 hints waiting (A6). |
| Deny beats an older allow | ABSENT on main; PARTIAL on branch | NOT HERE | proven | [VERIFIED] A6: main reads no deny. PR 44 and PR 45 add `memory.honoured`. |
| Scopes `kind_project`, `kind_any`, `merge_project` | PARTIAL | NOT HERE | keep | [VERIFIED] A1, A7: 207 tests pass in a copy; CI green on the PR. Ref PR 42 (+ 44, 45). Each scope is a new rule for "same call" and needs adversarial tests (A6). |
| Jev decides in the owner's place (low, medium; shadow at high) | IMPLEMENTED against fixtures; accuracy UNVERIFIED | IDLE | keep | [VERIFIED] `test_jev_proxy_w102.py` passed (A2); repo `docs/12` s10 "fixtures only". Live: 48 calls, 0 allowed (A2). Readiness gate counts 50 DISTINCT exact keys incl. hints; live 67 of 50, 100 answers, all allow, 0 deny (A6). Cannot show Jev says no when it should. |
| Proxy question v2 (Jev sees denies) | PARTIAL | NOT HERE | proven | [VERIFIED] A2 s9, A6; W-102. Ref PR 45. Default band, uncalibrated. [RECOMMENDATION] Start in shadow (Jev answers, is not used). |
| Shadow agreement metric; proxy decisions and owner marks feeding calibration | ABSENT | n/a | proven | [VERIFIED] A6 s5: no code compares Jev P with the owner's answer; the shadow stage ends by count. `auto_label` filters proxy decisions out; `review mark` writes events, not labels. |
| Memory against native "don't ask again" rules | PARTIAL | IDLE | keep | [INFERENCE] R1: native rules are per repo and command; JADE memory adds scope, lifetime, risk. |

## C5. Continuous execution and recovery

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| Crash-safe ledger (WAL, one transaction per step) | CODE-ONLY | ACTIVE | keep | [VERIFIED] Crash tests exist (`test_permits_lifecycle.py:520-545`, `test_phase2_enforce.py:74`); skipped locally (A5). [UNRESOLVED] A5 did not read CI. Must not become a workflow engine (R2). |
| SessionStart briefing; JADE Stop reminders | PARTIAL | ACTIVE | unproven | [VERIFIED] Briefing: 8-line cap, ignores `source`, not project-scoped. Stop hook: two cases, skipped when `stop_hook_active` (A5 s2). |
| Stop hook "keep going" (FLOW) | PARTIAL | ACTIVE | keep | [VERIFIED] Outside the repo (`~/.agents/continuous-flow/hooks/flow_stop.py`): regex heuristic, fails open (A5). |
| Work-item persistence (`work-items.jsonl`, ledger mirror) | PARTIAL | ACTIVE | unproven | [VERIFIED] `work.save` not atomic, no lock, cwd-relative; sync manual (A5 s4). |
| Auto work item for a blocker; blocker taxonomy; retry guard | ABSENT | n/a | unproven | [VERIFIED] Only MODEL_DRIFT and QUESTION_DRIFT auto-create; no retry counter (A5, A6). No incident cited. |
| Hooks SubagentStop, PreCompact, SessionEnd, StopFailure, PermissionRequest, PermissionDenied, Elicitation | ABSENT | n/a | unproven | [VERIFIED] JADE registers five events (`ops.py:38-39`; A5, R1, R6). R1: not all needed. |
| Headless approval through native `defer` | UNVERIFIED | n/a | unproven | [INFERENCE] Docs: `defer` works only in `claude -p`, one tool call per turn (R1 3.1, A5; docs read, not run). [VERIFIED] no JADE probe (R2). |
| Batch asks with the chat rewrite | PARTIAL (defect) | IDLE | unproven | [INFERENCE] A5 2b: `askchat.rewrite` replaces EVERY question when one carries a token; no test has a batch. [UNRESOLVED] How the host shows it. |

Own runtime or scheduler: ABSENT, `rejected` (Part D). Scheduled continuation: C11.

## C6. Learning and calibration

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| Offline evaluation kit (labels with sources, splits, interval and calibration measures, bootstrap) | IMPLEMENTED | IDLE | keep | [VERIFIED] A6: 108 tests passed on synthetic truth; R5 s2. Proves the code, not Jev. |
| Labels from real calls | ABSENT | n/a | proven | [VERIFIED] A6: zero labeled rows; 136 verify calls, none tied to a decision. No threshold ever calibrated, no template promoted. |
| Biased labels (only accepted calls get labels) | PARTIAL (defect) | n/a | proven | [VERIFIED] A6 run: 300 rows at p >= 0.70 made `propose_threshold` propose 0.50. R5 s0: the selective-labels problem. |
| Replay pass rule | PARTIAL (defect) | n/a | proven | [VERIFIED] A6 run: compares an upper bound with a point estimate; near-unreachable, fail-safe. R5: canary rollback has no minimum n. |
| Platt, isotonic, conformal, inter-annotator agreement; labels from user correction, CI, "regretted"; public benchmark adapters | ABSENT | n/a | rejected | [VERIFIED] grep (A6, R5); no producer for those labels. If ever needed: small local code, not MAPIE or netcal (R5 5.7, [INFERENCE]). Nothing to fit until real labels exist. |
| Random audit of silent allows | ABSENT | n/a | proven | [VERIFIED] R5: `review mark` is owner-chosen, so biased. Needed to measure decision accuracy (C10). |
| Shadow, canary, promote, rollback pipeline; miner | IMPLEMENTED (question templates only) | IDLE | keep | [VERIFIED] `improve.py`, `improve.mine`; tests passed (A6). Never run on real data. Other targets ABSENT by design (W-44). |
| Drift checks | PARTIAL (defect) | NOT RUNNING | proven | [VERIFIED] `check_drift` compares model strings; a fixture run wrote a MODEL_DRIFT item into the live ledger (A6, [INFERENCE]). The maintain job is not loaded (C8). |
| Measure of decision accuracy | ABSENT | n/a | proven | [VERIFIED] A6 3.2: success of the action is not decision quality; nothing links them. The owner's third goal has no instrument yet. See C10. |

## C7. Human experience

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| Chat question through the `AskUserQuestion` rewrite; macOS dialog for subagents | IMPLEMENTED | IDLE | keep | [VERIFIED] A7: 59 tests passed on main; RESULTS.md cells 8c, 8d (untested mode: C2). |
| Plain first lines; scope choices; one-line `jade review` and `jade knows` | PARTIAL | NOT HERE | keep | [VERIFIED] A7: 233 tests passed on PR 43. Gap: merge line drops `--delete-branch`, `--admin`, `--auto`. Refs PR 42 to 45. |
| Recommendation, consequence of Deny or no answer, working folder, who asks | ABSENT | n/a | proven | [VERIFIED] A7 s2. The ask never says that expired asks switch JADE to a terminal permit. |
| Safe display of commands (bidi, zero-width, control characters) | ABSENT | n/a | proven | [VERIFIED] A7 probe, R6 probe 2: U+202E survives in chat and dialog; the terminal permit escapes it and shows the Persian half-space (ZWNJ) as text. |
| Language setting, Persian, RTL, plain-language mode; simple-English rule carried by JADE | ABSENT | n/a | unproven | [VERIFIED] grep (A7). Only the owner's CLAUDE.md has the rule. PARTIAL on PR 42 (risk lines tested at 20 words or fewer). The owner picks the language first. |
| Permit screen: whole command and code | IMPLEMENTED | IDLE | keep | [VERIFIED] `permits.py` (A7). Defects: signed helper shows another code than `confirm_code`; refusal not journaled. |
| Batched asks | ABSENT | n/a | rejected | [VERIFIED] repo `docs/12` s8, T14; A7 rec 11. |

## C8. Install, release, public readiness

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| Install from tag (`uv tool install git+...`); 7-step `jade setup`; upgrade and rollback by tag | IMPLEMENTED | ACTIVE | keep | [VERIFIED] A8; `tests/test_setup.py`; `verify_install.sh`; `upgrade.py` (network path UNVERIFIED). Full state needs 12 to 14 human actions over a week. Tags unsigned. |
| Zero-config onboarding | ABSENT | n/a | unproven | [VERIFIED] A8. [RECOMMENDATION] `jade quickstart`, advisory only, no sudo, if public use is a goal. |
| Linux and macOS support | PARTIAL | ACTIVE | keep | [VERIFIED] CI runs Linux only; Keychain, dialog, launchd, installer are macOS (A8). Windows ABSENT. |
| CI and release workflow | IMPLEMENTED | n/a | keep | [VERIFIED] `ci.yml`, `release.yml` (A8). No macOS job, no secret scan (`.gitleaks.toml` unused), no `timeout-minutes`. The rule "release only from merged main with CI green" lives in docs and habit, not in the workflow. Signing ABSENT. |
| Maintain job (`com.jade.maintain`) | UNVERIFIED | FAIL | proven | [VERIFIED] A3, A8: `jade check` FAIL, plist exists, launchd not loaded; README says loaded. Calibration upkeep, sweeps, drift checks are not running. |
| Retention purge | PARTIAL | ACTIVE | proven | [VERIFIED] A8: `ops.purge` skips `asks` and `human_answers.label`; the 90-day promise is not fully kept. |
| Provider choice; privacy page; PyPI package; 0.7.0 bump | ABSENT | n/a | unproven | [VERIFIED] `adapter.URL` constant, no page (source and commands go to openrouter.ai); no publish step; top-level package `policies` (W-28); no 0.7.0 tag (B2). |
| Docs truth test; unique work-item id register | PARTIAL | n/a | keep | [VERIFIED] `tests/test_docs_truth.py` missed README "four hooks" and "10 entries" (A8). Ids collide (B7). |

## C9. Observability

| Capability | Code | Live | Need | Evidence and note |
|---|---|---|---|---|
| Ledger as audit trail; `jade ledger stats`, `review`, `decide`, `permit list` | IMPLEMENTED | ACTIVE | keep | [VERIFIED] A8, A2 live counts (369 calls in 14 days). |
| `jade check` health gate | IMPLEMENTED | ACTIVE | keep | [VERIFIED] A8 ran it offline: 22 rows, 21 ok, 1 FAIL; 2 more rows are live-only and were not run (24 in all). No `--json`. Nothing alerts the human between checks. |
| Hook log or heartbeat | ABSENT | n/a | proven | [VERIFIED] A5, A8: no logging in the runtime; FLOW has a heartbeat, JADE does not. A dead hook is silent; the dead maintain job (C8) went unseen. |
| Human-effort measure | PARTIAL | ACTIVE | keep | [VERIFIED] A8: 100 asks and 20 terminal approvals from 2666 gated calls in 14 days (about 4.5%). One data point, from the maintainer developing JADE (A8). NOT evidence of success: see C10. |
| `tool_use_id` on the decision row; OpenTelemetry export; MCP annotation audit | ABSENT | n/a | rejected | [VERIFIED] `tool_use_id` only in `permits.py`; no OTel hit on any ref (R4). [INFERENCE] Claude Code already exports `tool_decision` events; MCP annotations are untrusted (R4, summary). |

## C10. Friction and accuracy: measure them as pairs

[RECOMMENDATION] A friction number alone invites "fewer asks is better". Pair every friction metric with a correctness metric. If the pair cannot be measured, do not use the friction number as a goal.

| Friction metric | Paired correctness metric | Status and test |
|---|---|---|
| Asks per 100 gated calls (about 4.5 now; C9) | Wrong-allow rate on a RANDOM sample of silent allows | Pair ABSENT (C6). Test: owner reviews a random sample each week. Bar [UNRESOLVED]: the owner sets the acceptable wrong-allow rate. |
| Share of asks answered by memory or Jev | Share the owner later marks wrong | Marks are owner-chosen, so biased. Needs the random audit. |
| Calls the Jev proxy allows | Agreement with the owner in shadow, including denies | ABSENT. 0 denies exist today (C4), so false-allow rate cannot be measured. |

**Tests for the recommendations.**
- Question v2 in shadow: pass rule [UNRESOLVED]. Proposal: no live use until the agreement metric exists and the owner has labeled some denies.
- Replay 50 plain-high calls through the proxy and native auto mode. Pass rule [RECOMMENDATION, needs approval]: auto mode makes no more wrong allows than the proxy. 50 calls is a smoke test, not proof.
- Native deny rules as a floor: each rule blocks a probe command in a live host cell.
- Fix the R6 gaps (C2): a case in `evals/adversarial/cases.jsonl` that fails on main and passes after the fix.

## C11. Options from outside tools (not decisions)

[INFERENCE] throughout. R1 to R4 are mostly WebFetch summaries. Each row is an option with a condition. No safety control is removed until a replay shows an equal or better wrong-allow rate.

| Option | Could reduce | Condition |
|---|---|---|
| Claude Code auto mode. R1 3.2, [INFERENCE: vendor figure via summary, R1 3.2], not checked by me: Anthropic reports 0.4% false positive on 10,000 real calls and 17% false negative on 52 real overeager actions; "not a security boundary"; data goes to the vendor | Part of the Jev proxy | Replay test (C10). No comparison on JADE's data exists. |
| Native deny rules; managed settings (R1 3.3, 3.4) | A second "never run" list; part of hook wiring protection | Cheap floor, not a boundary alone. Managed settings need sudo; probe `--bare` first. |
| Native sandbox. R1 3.5, [INFERENCE, web page via summariser], not checked by me: macOS Seatbelt, off by default | OS containment (JADE has none) | Owner turns it on and counts prompts before and after. Whether on here: UNVERIFIED. |
| `/goal`, `/loop`, cron, Monitor (A5 s6, docs read not run; R1 3.10, 3.11) | Scheduled continuation | JADE stays the record layer under them. |

[INFERENCE, R3 6.2, R2 s5] Native `ask` rules are UNVERIFIED (R1 3.13): the one tested did not reach the owner in auto or bypass mode on 2.1.314. Not replaceable by an outside tool: `shellnorm`, rules, permits, signed approver, SI invariants, the ledger, calibrated verdicts with abstention.

## C12. Documentation against code (key rows)

Full list: A2 s11, A7, A8. [VERIFIED] Doc text is not runtime behaviour. Other known drift: repo `docs/03` s5 (provenance not sent; `evidence_source` is sent), repo `docs/04` (Platt, CI labels: no code), repo `docs/10` (approver not installed: it is).

| Doc claim | Code or live fact |
|---|---|
| README: "the four hooks"; the maintenance job is loaded; latency about 500 ms | Five events (`ops.py:38-39`); `jade check` FAIL on the job; live p50 712 ms (A5, A8). |
| repo `docs/12` s10: critical calls and merges are asked too | Memory silences them at low and medium (A3, A4). |
| repo `docs/08` s2.1: advisory mode never blocks; repo `docs/12` s2: level is an "autonomy level" | SI-09 to SI-12 block once a level is set; `high` is LEAST autonomy (A4). |
| repo `docs/05` T18: subagent coverage unverified | Verified on 2.1.314 for the dialog; `bypassPermissions` not run (C2). |
| A1, A3 to A5: T31 = shell start-up, T30 = scope; PR 48 unmerged | Now T30 harness, T31 scope, T32 shell start-up; PR 48 merged (B1, B7). |

---

# Part D. What not to build

[RECOMMENDATION] These items are ABSENT in the matrix. None has a demonstrated need, or another tool does it. Add one only after its trigger.

| Do not build | Why | Trigger to reconsider |
|---|---|---|
| Own durable runtime or scheduler | Temporal, LangGraph, DBOS and others need the agent loop inside their code; JADE governs agents it does not write (R2). | Not for JADE's role. |
| Second allow-list engine for "never run" lines | Native deny rules sit under any hook `allow` (R1 3.3, [INFERENCE]). | Native rules fail the probe test (C10). |
| Batched asks | repo `docs/12` s8, T14; A7 rec 11. | After "one risky action per call" ships and is measured. |
| New autonomy names; MAXIMUM by loosening never-lists | Aliases (A4 s8); changes safety floors. | The owner states what differs between two adjacent names (C3). |
| Platt, isotonic, conformal, MAPIE, netcal; benchmark adapters (`inspect_ai`) | No real labels to fit or test (C6). | A real labeled set and a measured gap. |
| Contradiction detection, blocker taxonomy, retry guard, `Elicitation`, `SubagentStop`, `PreCompact`, `SessionEnd` hooks | No incident cited. | A recorded incident. |
| OTLP export, `tool_use_id` on the decision row, MCP annotation audit | No join need; annotations are untrusted (R4). | The owner wants to join with Claude Code telemetry. |
| Per-project level; Linux and Windows backends; PyPI package | No stated need. | A real project or a public release goal. |
| `tree-sitter-bash` comparison for the shell classifier | Adds a dev dependency. | An existing probe finds a bypass the text scan cannot close. |

---

# Part E. Owner decisions

These are the owner's calls. I changed nothing. Cheapest option first.

**Hard warning (the only one).** Do not merge PR 47 as pushed (c6ab27a at the time of this draft; the head is now c676801, still unchecked against main here, see W-115). It blocks less than main.

**First cheap checks (deterministic, no new code).** [RECOMMENDATION] Before any new design: (1) is the pre-push guard installed here? (2) is the default branch protected on the remote? They are cheaper and stronger than the SI-08 text scan (C2).

| # | Decision | Options (cheapest first) | Recommended default |
|---|---|---|---|
| 1 | PR 47 (A1 Q4) | [SUPERSEDED by d13 Q-02 and X-19: the head is `c676801`; merge PR 47 first after one clean review round, ship in 0.7.0, no 0.6.2. Older text follows.] (a) Push 967b166 and the diff, probe, fold into 0.7.0. (b) Ship a 0.6.2 hotfix. | (a). Main already blocks the command [INFERENCE]. |
| 2 | Id collisions (B7) | (a) Note in repo `docs/07`: W-96 to W-105 on main are "feature ids", dirty rows are "open-item ids". (b) Renumber dirty rows from W-117 (touches PR 47 citations). | (a) now, (b) after 0.7.0 merges. |
| 3 | Spec B and the dirty backlog (B6) | (a) Commit each on its own branch, explicit paths. (b) Leave as is. | (a). They are the only copies. |
| 4 | 0.7.0 merge train | [SUPERSEDED by d13 Q-09: the order is 42, 43, 44, 46, 45, with PR 47 first. Older text follows.] PR 42 first; rebase PR 43 and 45; then PR 44 and 46. | As written. |
| 5 | Waves 4 to 6 | (a) Keep the remote branch. (b) Redesign (W-98). | (a). Do not delete. |
| 6 | Memory and critical calls (W-116) | (a) Docs say what code does. (b) Code asks for critical. | (b). Safety change; needs approval. |
| 7 | Autonomy names; `jev` switch | (a) Add nothing until "what differs" is answered. (b) Add the `jev` key only. | (a). |

---

## Unresolved

1. [UNRESOLVED] Meaning of "v6" (A1).
2. [UNRESOLVED] Whether 967b166 plus the dirty diff covers every R4 item of PR 47.
3. [UNRESOLVED] Local tests on the re-merged tips and on 967b166. GitHub shows `SUCCESS` for pushed heads only. I ran no tests.
4. [UNRESOLVED] Remote branch protection and pre-push guard install state (A8). Repo visibility is settled: PUBLIC under MIT [VERIFIED `gh repo view`, master plan check 2026-11-16; d11 A-3].
5. [UNRESOLVED] Live Jev answer spread and accuracy; no live call was allowed.
6. [UNRESOLVED] Whether native sandbox, auto mode and the mods guard are on for this Mac (R1); how the host renders Persian or RTL text.
7. [UNRESOLVED] CI output for the 0.6.1 tag. If read, the CODE-ONLY rows can move to IMPLEMENTED.
8. This draft was not Jev-verified. `jev verify` and `jade ask` write the live ledger and send text out, so I did not run them.
