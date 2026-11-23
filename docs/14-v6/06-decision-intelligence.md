# Decision intelligence design

This section answers one question: how should JADE decide better, with less human work, when the only model it has (Jev) returns numbers and no reasons? It covers evidence, repeat control, outcomes, traceability and cost. It builds on what exists and keeps the exit-code contract. It gives a small set to build first (Tier A) and a longer set to build only if measured data shows a need (Tier B).

Tags: [VERIFIED] has concrete evidence (file, symbol, probe, or evidence id A2, A6, R3, R5, A5 with the item). [INFERENCE] is my reasoning. [RECOMMENDATION] is a design proposal. [UNRESOLVED] could not be settled. Anything that changes safety behaviour is marked "NEEDS APPROVAL" and is a proposal only.

Terms, once:
- Evidence hash: a sha256 fingerprint of the evidence text. Same text, same hash.
- Anchor: the git HEAD and tree hash when evidence was seen. If they change, the code changed.
- Jaccard similarity: the share of words two texts have in common (0 to 1).
- Witness: a ledger row or hook record that proves an event happened, so the agent's word is not the only proof.

Assumptions (reversible):
- "v6" means the 0.6.x line plus main plus the unmerged 0.7.0 stack. [VERIFIED] A2 section 0, A6 section 0.
- Code claims are about `origin/main` at `e9412f3` (0.6.1 plus PR 48) unless a ref is named. I re-read `state_machine.py`, `ask.py`, `decide.py`, `ledger.py`, `hooks.py`, `evals.py`, `thresholds.py` and `schemas/outcome.schema.json` at that ref. [VERIFIED]
- The A2 probes ran on a copy at `8100ea1`. They carry over because A2 section 0 found no unmerged ref that changes `adapter.py`, `ask.py`, `decide.py`, `state_machine.py`, `route.py`, `thresholds.py`. A2 did NOT read branch changes to `ledger.py`, `hooks.py`, `proxy.py`, `questions.py`. [VERIFIED] A2 sections 0, 9. DI-1 touches `ledger.py` and the later T2 spike touches `hooks.py`, so both risk merge conflicts with the `open-items-*` and `design-clear-questions` branches (O-6).

## 0. Short answer

1. [VERIFIED] The pipeline is sound at the edges (typed errors, bands, a never-looser guard on the cached path) and weak in the middle: what counts as evidence, what counts as a repeat, how strict a check is for the risk of the work, and who may write the decision record. A2 section 13, gaps 1 to 4.
2. [RECOMMENDATION] The smallest fix is deterministic code, not a smarter model. Build first only what has a verified gap and no tuning number.
3. [RECOMMENDATION] Tier A changes only turn a "yes" into a "not yes", change a record, or reuse a stored answer for the same claim. Three exceptions, stated plainly:
   - Whitespace-only repeats (DI-3a) reuse the stored verdict, as an identical repeat does today. A different claim text never gets a stored accept.
   - Head-plus-tail truncation (section 10) changes what Jev sees and can flip a verdict either way. It is a separate change.
   - Near-duplicate matching by Jaccard (Tier B) may only count samples and keep an earlier non-yes. Reason: "all 412 tests pass" and "all 412 tests fail" share 5 of 7 words (0.71). [INFERENCE] by counting.
4. [RECOMMENDATION] No new "Jev refinement skill". Put guidance in code output and about ten lines in the existing `jade` skill (section 6).
5. [INFERENCE] Process metrics can improve now. Decision accuracy cannot be shown now: the ledger holds zero labelled rows. [VERIFIED] A6 section 5.
6. [INFERENCE] Most of this adds "not yet" answers. It does not cut owner approvals for gated commands; the memory and proxy sections do that. Where it lowers human work is in section 11, with a stop rule so added strictness is measured.

| Item | Tier | Why |
|---|---|---|
| DI-4 risk link | A | Verified gap (A2 probe_risk). No tuning number |
| DI-5 revision cap stops the call; no revision used by a cached repeat | A | Verified gap (A2 section 7, probe_loop) |
| DI-3a whitespace normalisation; repeat journal by reason | A | Verified gap (A2 probe_dedup) |
| DI-1 minimal evidence record (5 fields) | A | A stable id and hash to join on |
| DI-2 diagnosis from checks C1, C4, C5, C9, advisory | A | Today every abstain gets the same advice |
| DI-8 label filter (`goal_check` in outcome measurements) | A | Verified hole in `auto_label`; no state change |
| Outcome names as one docs table | A | No code type needed yet |
| DI-3b sample caps, Jaccard, stricter-wins | B | Numbers are guesses; needs SP-1 |
| Provenance tiers, T2 hook-seen join, C7, C8, C10, C11 | B | No measured failure; needs SP-2 |
| DI-7 witness events, DI-12 trace | B | No gate reads ACCEPTED |
| DI-9 wording | B | Needs owner word (conflicts with CLAUDE.md) |
| DI-10 safe alternative, DI-11 defer link | B | Needs blocked-rule counts |
| State rename, typed Verdict class, `labels_v2` | B | No consumer |

## 1. Jev's real contract and what follows

- [VERIFIED] The request is `{"model","state","questions"}` to `POST https://openrouter.ai/api/alpha/decisions`. The parser keeps `answers.<name>`, token usage, cost, model, id, provider. `jade/adapter.py`; A2 section 1.
- [VERIFIED] Answer shapes: `noul` (one probability), `choice` (per-option probabilities plus a confidence number), `score`; `check_answer` rejects a wrong type, an unknown option or a probability outside [0,1]. A2 section 1.
- [VERIFIED] The parser has no explanation field; the ledger stores `answers_json` only. The vendor skill says Jev returns "typed answers and probabilities rather than generating text or reasoning explanations". A2 section 1.
- [UNRESOLVED] No live body was captured, so "Jev gives no explanation" rests on docs and the parser. Spike SP-0 is one live call, raw body saved and read once.
- [INFERENCE] Jev cannot say which evidence is missing, what contradicts the claim, or what to do next. It gives a likelihood of "yes" from a model nobody has calibrated for this task. [VERIFIED] A6 section 5: no `calibration/current.json`; all bands are defaults.

[RECOMMENDATION] Any explanation JADE shows comes from code checks over recorded evidence. Four rules:
1. A finding names its check and the evidence ids it read.
2. The layer never says why Jev gave a number.
3. The layer can only lower a verdict. [VERIFIED] The companion check already works this way: `ask.py` line 240 lowers exit 0 to 3, only at risk medium or higher.
4. Text the agent wrote is marked as the agent's assertion, never trusted like text a hook or JADE observed.

No model writes free text in the diagnostic path. A typed choice question is an open alternative to measure first (section 3).

## 2. The evidence record and the checks

### 2.1 Today

- [VERIFIED] Evidence is one text string plus a free `evidence_source` (`file:<path>`, `stdin`, `inline`). It is not hashed or checked. The source string is sent to Jev too, which repo docs/03 section 5 says it is not. A2 sections 5, 11 item 5.
- [VERIFIED] The decision record keeps `evidence_refs` (cut to 512 characters), a `context_fingerprint` and `context_freshness {observed_at, ttl_s: 3600}`, written once at open and never updated. `jade/decide.py` lines 32 to 41.
- [VERIFIED] `add_evidence` needs only `revision_count < MAX_REVISIONS`; docs/02 says it needs a new `context_fingerprint`, the code does not check (A2 section 11 item 2). In the ask path, `decide.link_evaluation` (not the agent) sends `add_evidence` whenever the decision is ABSTAINED or NEEDS_EVIDENCE, without looking at the evidence (`decide.py` lines 71 to 83). That is why an identical cached re-ask used a revision in A2's probe_loop. An `IllegalTransition` there becomes only a warning (`ask.py` lines 262 to 267).

### 2.2 Minimal record (DI-1, Tier A)

[RECOMMENDATION] One additive ledger table, five fields, no tiers:

| Field | Meaning |
|---|---|
| `evidence_id` | Stable id from JADE |
| `content_sha256` | Evidence hash of the normalised, redacted text |
| `observed_at` | When JADE saw it |
| `source_ref` | Path, or `stdin` / `inline`. For a file JADE also reads the mtime |
| `anchor` | `{git_head, tree_sha}` at `observed_at` |

Normalise before hashing: LF line endings, no trailing spaces or blank lines, no ANSI codes. Keep timings and counters: a new test run is a new observation. Persian and Arabic-Indic digits map to ASCII for the C5 token check only, not for the hash.

Ledger migration rules. [VERIFIED] `jade/ledger.py` lines 56 to 79: a new table in `SCHEMA` is created by `CREATE TABLE IF NOT EXISTS`; `MIGRATIONS` only adds columns; `SCHEMA_VERSION` is a crc32 of `SCHEMA`, `INDEXES`, `MIGRATIONS`, so any change upgrades every file once. A primary-key change and a state rename (stored data) are not covered. Every hook process opens the ledger, so the upgrade runs inside a hook. Required test: an old ledger opens, the new table exists, open time stays inside the 6 s hook budget (A5 recovery table, `HookBudgetExceeded`).

### 2.3 Provenance tiers and the hook-seen join (Tier B, SP-2)

A possible design has tiers T0 to T3 and a join between evidence and hook-seen tool output (T2). Review found two faults in it, so it is Tier B.

- [VERIFIED] For shell and write tools JADE reads only failure flags from `tool_response` (`_failed`, `hooks.py` line 632) and writes "tool call returned (exit status only; the goal is not verified)". `askchat.settle` reads the human's answer from `tool_response` for AskUserQuestion (`hooks.py` lines 668 to 669). [UNRESOLVED] The Bash stdout and stderr shape.
- [INFERENCE] A hook-seen join proves only that some command wrote the bytes. The agent can run `printf 'Ran 412 tests OK' > out.txt`, and the file is then a redirect target of an observed command. So T2 raises the cost of forging and gives a producer command to inspect. It does not prove honesty.
- [RECOMMENDATION] If SP-2 is run, add C11: flag a producer command that is only `echo`, `printf`, `cat`, a heredoc or a bare redirect. T2 alone never lifts a verdict (no check can).
- [UNRESOLVED] Whether `shellnorm.skeleton()` exposes redirect targets. R3 section 6.3 spike 1 proposes that comparison as an unrun test; I found no redirect-target accessor in `origin/main` `jade/shellnorm.py`.
- [RECOMMENDATION] If built, store for hook-seen output only hash, byte count, exit status, command hash, anchor, pruned after 24 hours. Hashing cost on 10 MB is [UNRESOLVED]. Without T2 the tiers overlap, so tiers wait.

### 2.4 Checks code can do without a model

All can only lower a verdict. All are advisory now (rule in section 3).

| Check | Method | State today |
|---|---|---|
| C1 empty evidence | non-empty test | Empty: exists (`ask.check_required`, lines 370 to 374; exit 6). Under 200 characters: only PF-03 in `jade route`, advisory, English regex (A2 section 4) |
| C4 anchor drift | HEAD or tree differs from the anchor, or the evidence file is older than the last tree change | New |
| C5 claim tokens in evidence | pull numbers, paths, hex ids, backticked names from the claim; each must appear in the evidence, outside a line that only repeats the claim | New. Mostly script-neutral |
| C6 failure markers | fixed list: `Traceback`, `FAILED`, `error:`, `npm ERR!`, `panic:`, non-zero exit line | New, small list. English tool output |
| C9 truncation | the `cap_state` cut flag | New |
| C7 exit status recorded, C8 same command two results, C10 evidence newer than last write, C11 echo-only producer | need hook-seen data | Tier B |

- [VERIFIED] `ask.cap_state` (lines 62 to 75) cuts the longest string to fit 32 KiB, keeps only its head, and appends the visible marker "[truncated by jade: state cap 32 KiB]". Jev sees that a cut happened. The tail of a long log is often the summary.
- [RECOMMENDATION] C5 is the strongest cheap check: a claim naming 412 and `tests/x` needs both in the evidence, in any language. It is necessary, not sufficient. Required adversarial test: padding evidence with claim tokens must not clear C5.
- [INFERENCE, not run] Test exit status and `pytest --junitxml` output are cheaper and less fragile for C6 than a marker list. Try them in SP-3 first.
- Freshness. [VERIFIED] Today it is advisory and self-reported (PF-07 runs only if the agent passes `--observed-at` and `--ttl-s`); the permit path enforces HEAD and content sha at use (`permits.py` `_current`). A2 section 5. [RECOMMENDATION] Use anchor drift (C4) as the freshness rule. It targets the most common wrong "done": tests ran, then code was edited, then "done". No per-kind TTL numbers in any gating rule.

## 3. Missing information, contradiction, unsupported assumption

- Missing information: code does C1, C4, C5, C9. A model is needed only to judge that evidence is on topic, and Jev already gives a number for "supported by evidence". Do not build a model that lists what is missing in free text.
- Contradiction: code does C6 markers, and C8 later. Meaning conflicts in prose need a model. Do not build an NLI or LLM judge in the hot path.
- Unsupported assumption: nothing now, because the CLI cannot set `assumptions`. Do not build an extractor or ledger.
- [VERIFIED] Contradiction detection is MISSING at run time; only a post-hoc label in `evals.auto_label` exists. A2 section 5. The decision record's `assumptions` field cannot be set by `jade decide open` (`cli.py` lines 679 to 684).
- [INFERENCE] Flaky results (same command, same tree, fail then pass) are a real contradiction. C8 needs hook data, so it waits.
- [UNRESOLVED] C6 false positives ("1 xfailed", a test that names `error:` on purpose). Measure in SP-3.

Diagnosis options:

| Option | What | Verdict |
|---|---|---|
| (a) Code findings only | C1, C4, C5, C6, C9 plus a fixed catalogue of next steps | Build first |
| (b) Typed choice question | One more Jev call with options like "no test run", "stale", "wrong scope". Returns probabilities, not prose. Advisory | Measure in SP-3 against (a) first. It is one more uncalibrated number and call. Judgment classes `scope_gap`, `stale_evidence` (JG08, JG10) exist, but offline they only test delivery (A2 section 5) |
| (c) Free-text model explanation | A model says what is missing | Reject: it invents reasons that look like facts |

When may a check lower a verdict (one rule):

| Check | May lower now | Before it may lower at medium or higher |
|---|---|---|
| C1 empty | Already blocks | Nothing |
| C4, C5, C6, C9 | No (advisory at all risks) | SP-3 replay run; the owner reads every hit; no hit on an ask the owner judges fine; the owner approves each check one by one |
| C7, C8, C10, C11 | Not built | SP-2 passes, then the same rule |

[RECOMMENDATION] NEEDS APPROVAL for each promotion. The only pre-call stops are empty evidence (exists) and an exhausted revision cap (DI-5).

## 4. Bounded re-evaluation

### 4.1 The gap, as probed

[VERIFIED] A2 section 6, `probe_dedup.py`, fixture provider, temp ledger, empty HOME: seven asks gave five provider calls. Identical: cached. Claim reworded, same evidence: new call. Trailing newline or spaces: new call. Identical at `risk=high`: cached, re-judged at the new risk (`ask.py` lines 216 to 226). Evidence changed: new call.

- [VERIFIED] `probe_nocache.py`: provider answers 0.5 then 0.9 with `--no-cache`: exit 3, then exit 0. The never-looser guard (`ask.py` lines 223 to 226) sits only on the cached path.
- [VERIFIED] `probe_risk.py`: one answer P=0.75 gave exit 0 at `--risk low` and exit 3 at `--risk high`. `jade ask` does not read the decision's recorded risk.
- [VERIFIED] Only the proxy resists: 2 asks per (session, `hold_key`) per hour (`proxy.MAX_ATTEMPTS = 2`). After 3 revisions `ask.run` still calls the provider; only a warning says "escalate instead". The skill says "Change the evidence, not the wording", yet a trailing space counts as new evidence. A2 sections 6, 7, 11 item 13.
- [UNRESOLVED] How far Jev's answer moves for a trivial change. Sample shopping (asking again for a lucky yes) is a risk in theory; no real case is shown. `repeat_share` is 0.081 over 14 days (A2 section 2). SP-1.
- [INFERENCE] `evals.distinct_claims` counts a reworded claim as a new claim, so rewording can inflate the 200-label count that unlocks a threshold change. A2 section 6.

### 4.2 Tier A: no tuning number

1. DI-3a. Normalise claim and evidence text before hashing (LF, trailing spaces, ANSI) and nothing else: no case folding, no token changes. A whitespace-only repeat then takes the cached path, which is never looser and re-judged at the effective risk. NEEDS APPROVAL.
2. DI-4. Effective risk is `max(--risk, linked decision's recorded risk)`. `--decision` is optional and A6 section 5 shows 165 decisions with 0 provider calls attached, so the link alone does not bind: an agent that omits it gets `low`. Options: (a) take the highest risk among the session's open decisions (the `decisions_open` index makes it cheap [INFERENCE]; cost: an unrelated risky command makes unrelated claim checks stricter); (b) `jade ask` opens a decision when none is given; (c) accept that risk-tied strictness holds only for a declared or linked risk. [RECOMMENDATION] Ship the link, say plainly that (c) is the limit, and choose (a) only with the owner's word (O-8). NEEDS APPROVAL.
3. DI-5. At `revision_count >= max`, `ask.run` makes no provider call and returns the last verdict with "escalate or report as unverified". In `decide.link_evaluation`, when the new call is a cached repeat (`dedup_of` set, evidence hash unchanged), skip `add_evidence`: no revision is used and the stored verdict returns. The guard must be there because that is where the event is sent. NEEDS APPROVAL.
4. Repeat journal. Record each refused or cached ask with a reason: exact, whitespace, reworded, cap hit. [VERIFIED] `jade ledger stats` already reports `repeat_share`; the split is new.
5. `--no-cache` stays for tests and humans. Agent use is O-2.

### 4.3 Tier B: caps and near-duplicates, after SP-1

No cap number ships before SP-1 measures Jev's noise. Proposed shape for then:
- The repeat key is the hash of the whole normalised evidence text plus the question id. No typed/tool-seen split: typed evidence is most evidence today (A2 section 4), and a hash over tool-seen records only would be the same for every typed ask, giving honest new evidence no budget.
- A cap on provider samples per (session, question, evidence hash). A different claim over the same evidence counts as a sample.
- A near-duplicate claim (Jaccard at or above a value from SP-1) counts as a sample, and an earlier non-yes on that evidence stands for it (stricter wins). It never returns a stored accept for a different claim text. Cost: this can also hold back a true claim that negates an earlier one. That cost goes in the false-abstain metric (section 11).
- Starting caps [RECOMMENDATION, not measured]: low 3 samples and 3 revisions; medium 2 and 2; high or critical 1 and 1. After the cap: low reports unverified or escalates; medium and higher escalate (`next_state` already allows WAITING_HUMAN at medium).
- [INFERENCE] Scope. Without provenance, changed evidence text is new evidence, so an agent that appends a junk line gets a new budget. The cap stops re-asks over the same evidence, the probed gap. It does not stop a hostile agent, and a "verified" verdict is advisory because no gate reads it. [VERIFIED] A2 sections 3, 13. The journal will show how often evidence changes only a little; add a "mostly the same evidence" check only if it shows junk-appending.
- [RECOMMENDATION] Two small functions, not one shared budget. The proxy limit guards a path where Jev may allow a gated command for the owner (safety). Claim caps are advisory. A shared budget would let a claim-tuning change silently move the proxy limit. Keep separate keys, pin the proxy limit by its own test, do not touch `proxy.py` here.
- Required regression pair: a negated claim and a changed-number claim never get a cached accept.

### 4.4 Stop and escalate

- [VERIFIED] ABSTAINED may go to WAITING_HUMAN only at risk medium or higher, or when revisions are used up (`state_machine.next_state` line 75).
- [VERIFIED] `policies/escalation.yaml` keys `abstain_to`, `max_revisions`, `critical.abstain_to` and registry `fallback`, `abstention_behavior` have no reader in `jade/`, `tests/`, `policies/`. A2 section 7. [RECOMMENDATION] Delete them, or make `max_revisions` real through DI-5. Do not build `abstain_to` routing to DEEP or FAST: nothing emits those routes (A2 section 3).
- Escalation for a claim is not a new mechanism: stop asking, tell the owner "unverified: <findings>", record `jade ack --as reported`. For a risky operation the permit or chat ask already exists.

## 5. Diagnosis, in the form the agent receives

Printed on exit 3 and 4 as extra stderr lines and as an optional `diagnosis` field in `jade ask --json`. Exit codes do not change. [VERIFIED] The contract is `0,3,4,5,6` (`thresholds.EXIT`); today's output is the text from `ask.explain`. A2 section 2.

[VERIFIED] The agent already sees the number and band today: `ask.explain` (lines 297 to 311) prints `verdict.reason`, which `thresholds.py` line 98 builds as `P(yes)=0.62, accept>=0.70, reject<=0.30`. I add nothing to that.

```json
{
  "outcome": "NEED_EVIDENCE",
  "exit": 3,
  "state": "ABSTAINED",
  "findings": [
    {"code": "E03", "check": "C5", "evidence": ["ev_a1"]},
    {"code": "E02", "check": "C4", "evidence": ["ev_a1"]}
  ],
  "budget": {"revisions_left": 2},
  "next": [{"action": "observe", "catalogue": "run_tests", "text": "Run the tests again and pass the output with --state-file."}]
}
```

- [RECOMMENDATION] Show the finding class and evidence id only. Do not show which token is missing, and do not add the band or samples left. A detailed output lets the agent aim at the checks. Keep the full detail (token list, P, band, calibration status) in the owner view (`jade decide show`, ledger). [INFERENCE] The risk is limited: a check can only lower a verdict, so passing it never makes a yes. Hiding the P and band that `ask.explain` shows today is O-9; default: leave as is until SP-1 shows shopping.
- Codes: E02 stale anchor, E03 claim token missing, E04 failure marker, E05 truncated, E08 risk raised by decision, E09 new evidence has the same hash. Tier B: E01 no provenance, E06 same command two results, E07 budget used.
- `next` comes from a fixed catalogue in the repo, one entry per finding class, written by a human. No model writes it.
- E09 saves a provider call: the stored verdict returns and the agent is told.

Two kinds of abstain [INFERENCE]:
- NEED_EVIDENCE: exit 3 with at least one finding. The agent can act. One bounded retry helps.
- NO_FINDINGS_UNSURE: exit 3 with no finding. It means only that no code check fired. The checks are weak, so this is common and does not mean the evidence is complete. [RECOMMENDATION] Keep the existing advice (add raw evidence on the claim's real topic, or report unverified) but bound the retry by the revision cap (DI-5). Do not tell the agent to stop outright until SP-3 has counted how many past abstains land in each group and the owner has read some by hand. Until then it is a ledger label only.
- [VERIFIED] Today `ask.explain` gives the same advice for every abstain. A2 sections 2, 8.

## 6. The "Jev refinement skill"

- [VERIFIED] No refinement skill exists. Search: `skillfind` four times, `git grep -i refin` on `origin/main`, `git ls-tree skills/` on six refs. A2 section 8. The nearest text is a few lines in `skills/jade/SKILL.md` and `docs/claude-md-jev-section.md`; the installed and repo skills differ by 69 diff lines. A2 sections 8, 11 item 12.
- [VERIFIED] Against the seven duties: missing evidence NO; retrieve or verify NO; keep intent and risk NO; avoid rewording by instruction only; detect repeats exact only; stop NO; escalate PARTLY. A2 section 8.
- [RECOMMENDATION] Each duty belongs to code: C1, C4, C5, C9 find missing evidence; the agent runs the command in `next` (JADE runs none); DI-4 keeps risk; DI-3a (later DI-3b) and the journal handle rewording and repeats; DI-5 stops; a cap hit escalates, and NEED_EVIDENCE retries first.
- Decision: no standalone skill (it would duplicate enforced rules and drift), and no `jade next` command (`jade ask` prints the diagnosis; `jade decide show` can reprint it). Instead, add about ten lines to the existing `jade` skill: read the diagnosis; act on `next`; do not reword; stop at "budget used"; report unverified plainly; never claim done after a non-zero exit. First make the repo skill and the installed skill one file (`jade setup` installs the repo copy). A2 section 11 item 12.

## 7. Structured outcomes: a compatible extension

Goal: one table in repo docs/02 that maps the six outcome names to what exists. No second vocabulary beside `ACCEPT / ABSTAIN / REJECT`, and no code type until a consumer needs one. A2 recommendation 8.

- ALLOW: exit 0; ACCEPTED, AUTHORIZED, EXECUTING; hook paths (permit, memory, proxy accept, chat or dialog Allow). A2 section 10. Nothing new.
- DENY: two different things. (a) Jev reject: exit 4, REJECTED. (b) Policy block: SI-xx or human-only class, hook exit 2, no permit can authorize. Docs name them apart ("claim rejected", "policy blocked"). Text only.
- NEED_EVIDENCE: exit 3, ABSTAINED, at least one finding. New: findings and `next` (section 5).
- REQUIRE_APPROVAL: WAITING_HUMAN; PENDING permit; chat or dialog ask; route ESCALATE or ASK. Nothing new.
- SAFE_ALTERNATIVE: missing. DI-10, Tier B.
- DEFER_WITH_PLAN: WAITING_HUMAN plus a work item with `next_action` and `retry_or_revisit_trigger`. New: one link from work item to decision id (DI-11, Tier B).

- [VERIFIED] No new state per outcome is needed; `WAITING_DEPENDENCY` has no incoming edge (A2 section 3). [RECOMMENDATION] Remove it and the `deep_*` events, or keep them dead on purpose with a test. DEFER is advice recorded as a work item, not a state.
- [VERIFIED] The state `NEEDS_EVIDENCE` is entered only by `jev_error`, and the same word is a `jade route` value for missing evidence: two meanings. A2 sections 3, 11 item 11. [RECOMMENDATION] Defer the rename to `EVAL_ERROR`: no failure from the clash is known and it is a stored-data change (state_machine, repo docs/02, tests; migration test; NEEDS APPROVAL). For now repo docs/02 says the two differ.
- SAFE_ALTERNATIVE (DI-10). [INFERENCE] It cuts friction only if the suggestion is a different action class the gate accepts; a disguised rephrase is a bypass (gate messages say "do not rephrase", A2 section 10). [RECOMMENDATION] Static text per rule, written by a human as an optional `safer:` field in `risk.yaml` (R-22 fetch-and-run: download, read, then run; protected-branch push: push a feature branch and open a PR). No model generates alternatives. Required test: each `safer` template classifies strictly lower than the rule it replaces, or is allowed. Data first: [VERIFIED] the ledger journals `would_block` (2379 in 14 days) but not which invariant (A6 section 8, W-74). If the per-rule count cannot be had, build nothing.
- Compatibility: `diagnosis` is optional and additive; it never produces exit 0; store it as an `events` row of a new kind, not a `jev_calls` column ([VERIFIED] `events(entity_id, kind, ..., detail_json)` exists, `ledger.py` lines 35 to 38).

## 8. Confidence, calibration, correctness and execution success

### 8.1 Confidence is not probability

- [VERIFIED] The 0.70 and 0.30 defaults are generic TypeSafe numbers, not measured here (A2 section 2; repo docs/00 U3, ADR-07). No `calibration/current.json`; the ledger has zero labelled rows (A6 section 5). A band of 0.70 means "P is at least 0.70", not "70 percent right" (A6 section 7.2). I found no published Jev calibration (R5 section 3.3).
- [RECOMMENDATION] DI-9 wording (Tier B, NEEDS APPROVAL): call the number `jev_p` or "Jev estimate", never "confidence"; print a calibration status with it (`default_band_no_labels`, `calibrated_n=<count>`, `stale`); a verdict line may say "accepted" but not "verified" without an independent check.
- Conflict: this changes the owner's global rule, which tells agents to present work as verified after `jev verify` exit 0 ([VERIFIED] CLAUDE.md, Jev section). So the owner decides (O-7). Proposed phrase: "accepted by Jev (exit 0, bands not calibrated); not independently verified".
- [RECOMMENDATION] No Platt or isotonic fit now: the pipeline has only seen synthetic truth (A6 sections 4, 6). Fitting belongs to the calibration section.

### 8.2 Four facts, kept apart

[VERIFIED] `evals.auto_label` (lines 222 to 240): a verify call gets label 1 if its decision is SUCCEEDED, 0 if FAILED, only when the decision's last evaluation call was accepted (`exit_code == 0`). Labels from `user_correction` and CI have no producer. A6 sections 3.2, 9.

[VERIFIED] The hook closes its own call as "exit status only; the goal is not verified", an `observed` outcome with `attribution="indirect"` (`hooks._close_decision`, `decide.record_outcome`). That reaches SUCCEEDED (`state_machine` T: VERIFYING + `verified`). An agent can also record an `observed` outcome with free method text through `jade decide outcome`, and it reaches SUCCEEDED the same way. [INFERENCE] `auto_label` reads status, not method, so "the command exited 0" and "the claim was true" meet in one label.

| Fact | Question | Who may witness | Today |
|---|---|---|---|
| Executed | Did the call run, with what exit status? | The hook | Recorded |
| Goal reached | Did the wanted thing happen? | An independent check | MISSING (agent text) |
| Claim true | Was the claim correct? | Human adjudication or an independent check | PARTIAL (`adjudicated`, capped 10 per week) |
| Decision right | Would a careful owner say yes? Was harm seen later? | The owner, with a random audit | MISSING for proxy and gate decisions (A6 section 5; R5 section 5.2) |

DI-8 [RECOMMENDATION, NEEDS APPROVAL: it changes which rows reach calibration]:
- The state path does not change. `HOLDING = {EXECUTING, VERIFYING, OUTCOME_UNKNOWN}` (`state_machine.py` line 25) is the SI-07 hold; SUCCEEDED and FAILED release it; `decide.sweep` turns an overdue VERIFYING call into OUTCOME_UNKNOWN. If an exit-status close could not reach SUCCEEDED, every ordinary hook-run command would stay held, then become unknown, and SI-07 would block repeats of normal commands. So an exit-status close still reaches SUCCEEDED and still releases the hold. Required test.
- The fix is on labels only. Record `goal_check` as a key in the outcome's `measurements` map: `exit_only` (hook), `agent_reported` (agent text), `independent`, `human`. [VERIFIED] `measurements` accepts string values, while `schemas/outcome.schema.json` has `additionalProperties: false`, so a new top-level field would need a schema change and a measurements key needs none. `evals.auto_label` skips outcomes marked `exit_only` or `agent_reported`. Only `independent` and `human` make a `fit` label. Skipped rows stay in monitoring.
- Producer for `independent`: [VERIFIED] none exists (A6 sections 3.2, 9). Until one does, accuracy cannot be measured and DI-8 is a label filter. Options: human adjudication (exists), or later a read of a CI status file the owner configures (for example saved `gh run view` output) that JADE reads as a file and never runs. Default: adjudication now (O-10).
- [INFERENCE] Real fit labels will be very few for a long time. That is the truth, not a defect. A threshold change must wait.
- Three label kinds (claim true, owner would allow, harm observed; R5 section 5.2). [VERIFIED] `labels` has `call_id TEXT PRIMARY KEY` (`ledger.py` line 45): one label per call, so a column cannot hold three kinds. [RECOMMENDATION] Tier B: a new table `labels_v2(call_id, label_kind, label, source, note, ts, PRIMARY KEY(call_id, label_kind))`, only when a second producer exists. A key change is not covered by `MIGRATIONS`.
- [VERIFIED] Labels exist only for accepted calls, so a fit cannot see below the accept line (R5 section 4.3; A6 section 4.1: `propose_threshold` proposed 0.50 from 300 rows all at or above 0.70). Audit sampling of abstains and rejects belongs to the calibration section; DI-1 gives each row a stable id to join on. Keep stdlib `evals.wilson` (R5 section 5.7).

## 9. Traceability and the state machine the agent can walk

- [VERIFIED] An agent may send `jev_result_accept`, `fast_accept`, `deep_accept`, `route`, `add_evidence`, `authorize`, `escalate`; not human, outcome or runtime events (`state_machine.agent_may`; A2 `probe_agent_may.py`). The CLI passes only a note as `guard`, `actor="agent"`, and no `patch` (`cli.py` line 281), so the agent cannot forge `evaluation_refs`. A self-walk leaves a visible mark: actor `agent`. A2 section 3.
- [INFERENCE] A self-walked ACCEPTED decision has no call in `evaluation_refs`, so `auto_label` skips it (`gate is None`). The label harm is small. The harm is a misleading record in owner views.
- [VERIFIED] The hook gate does not read ACCEPTED (permits, SI-07 holds, memory, proxy, chat, dialog), and at risk high the CLI `authorize` fails (A2 section 3). So for safety this matters little.
- [VERIFIED] A witness precedent exists for two of four channels only. `human_accept` on a decision routed ASK needs `answer == "allow"`, a channel in (chat, dialog, memory, jev) and an `ask_id` in the patch. Then channel `memory` is checked against `human_answers` (strong allow) and channel `jev` against `jev_calls` (verdict ACCEPT, exit 0). For `chat` and `dialog` I found only the patch fields checked and no lookup of the `asks` table (`state_machine.apply` lines 119 to 131). [INFERENCE] That is an adjacent gap, reported here and not fixed here.
- [VERIFIED] The cross-session guard compares the CLI's session id with the stored one. The id comes from `JADE_SESSION_ID`, which SI-11 does not block (A5; W-81 for `jade ack`). Adding it to SI-11 belongs to the enforcement section.

DI-7 witness-bound events (Tier B; build only if owner views or a future autonomy rule start to depend on the record; NEEDS APPROVAL because it removes things an agent may do):
1. `jev_result_*` only if `ask.run` sends it in process, or the event cites a `call_id` in `jev_calls` for this session and decision with a matching exit code.
2. `fast_accept` and `fast_reject` need a matching `FAST_CHECK` row. [VERIFIED] `jade fast` writes `FAST_CHECK` and touches no decision (`cli.py` line 361).
3. Delete `deep_*` events. [VERIFIED] Nothing emits them (A2 section 3).
4. The `add_evidence` guard is in Tier A (DI-5).

DI-12 trace (Tier B): `jade decide show <id> --trace` prints the chain evidence, ask, Jev estimate, diagnosis, outcome, each marked with a witness (`hook`, `runtime`, `human_tty`, `agent`, `provider`). [VERIFIED] `events.actor` exists (`ledger.py` line 35). [UNRESOLVED] Whether every event kind sets it correctly; a test must check each producer.

## 10. Context reuse and cost

- [VERIFIED] Jev takes the whole `state` on every call, capped at 32 KiB (A2 section 1). [INFERENCE] There is no way to refer to earlier state: the request is `{model, state, questions}`.
- [UNRESOLVED] Whether the provider caches repeated state. Do not plan on it.
- [VERIFIED] The two canary calls of `jade check` cost "a few thousandths of a cent"; p50 latency is 712 ms; 369 calls in 14 days; `DAILY_BUDGET = 500` only warns (A6 section 11; A2 section 2). [UNRESOLVED] The total spend of the 369 calls: neither A2 nor A6 gives it; the owner can read the cost line of `jade ledger stats`. [INFERENCE] The cost that matters most is human time and wrong answers.
- [VERIFIED] `repeat_share` is 0.081 (A2 section 2); repo docs/04 says about 20 percent (R5 section 3.5). [UNRESOLVED] Which predicts better.

Levers, in order:
1. Repeat key by evidence (DI-3a now, DI-3b later): a cached verdict stays valid until the evidence hash or anchor changes, not for a fixed 3600 s (`dedup.ttl_s`, A2 section 6).
2. No wasted provider call at the revision cap (DI-5).
3. Head plus tail truncation. [INFERENCE] Keep the start and end of long evidence with the cut marker that already exists, and say so in the diagnosis. It fixes lost summaries but changes Jev's input and can flip a verdict either way. [RECOMMENDATION] Ship it alone with a calibration-group note, never inside a safety change. NEEDS APPROVAL.
4. Batching exists: one request carries the question and its companion ([VERIFIED] `ask.py` docstring). A verdict is about this claim, this evidence, this tree, so no semantic cache or summariser.

## 11. Evidence needed, tests and measures

Spikes first. All are read-only or one-off and change no behaviour.

| Spike | Question | How | Blocks |
|---|---|---|---|
| SP-0 | Is the Jev body only typed answers? | One live call with a fixture claim (state goes to openrouter.ai), raw body saved and read once, with the owner's word | Section 1 wording |
| SP-1 | How much does Jev move for trivial changes? | Same state, trivial variants, 20 calls each (A2 recommendation 10) | DI-3b caps and Jaccard value. Not Tier A |
| SP-2 | What does `tool_response` hold; does a redirect-target join work? | Read-only probe of the hook input (`evals/probes/hook_ask` harness), replay past sessions, R3 6.3 spike 1 | Tier B T2 |
| SP-3 | Which findings would have fired on past asks? | Replay `state_text` of the 136 stored verify calls through C1, C4, C5, C6, C9 offline ([VERIFIED] A6 section 5; `jev_calls.state_text`, `ledger.py` line 25). The agent cannot read the ledger (SI-10), so the owner or a JADE read command runs it; the owner reads every hit | Which checks may lower; whether NO_FINDINGS_UNSURE changes advice |

Tests [RECOMMENDATION]. All use the fixture provider, a temp ledger and an empty HOME, as the A2 probes do.
- DI-1: an old ledger opens with the new table and open time stays inside the hook budget; CRLF, trailing spaces and ANSI give one hash; Persian digits match ASCII for C5.
- DI-2: one fixture per finding code; a diagnosis never yields exit 0 (property test); no banned phrases such as "Jev thinks"; claim-token padding does not clear C5.
- DI-3a: a reworded claim stays a new call; a trailing newline or spaces return the stored verdict with no provider call; `--no-cache` with answers 0.5 then 0.9 stays exit 3. DI-3b (later): negated and changed-number claims never get a cached accept.
- DI-4: `probe_risk.py` as a test (linked risk medium plus `--risk low` gives the medium band).
- DI-5: after the cap, zero provider calls and the decision stays in its state; a cached re-ask uses no revision; an exit-status hook close still releases the SI-07 hold.
- DI-8: `exit_only` and `agent_reported` never label for fit; `independent` does; forged method text "ci passed" is `agent_reported`.
- DI-7 and DI-9 (later): an agent `jev_result_accept` without a matching call raises `IllegalTransition`; snapshot tests of ask wording. [VERIFIED] The forbidden-edge test file `test_phase2_enforce.py` is SKIPPED on this Mac (W-93) and CI gates it (A2 section 3).
- Corpus: new cases in `evals/adversarial` and `evals/regression`; a clean-HOME `CI=1` run and a `docker run python:3.13-slim` run before merge.

Process metrics (no labels): asks per decision; repeat share by reason; share of abstains followed by new evidence, a reword, a drop or a report; human prompts per hour; provider calls avoided; cost line.

Cost of strictness metrics (a one-sided plan cannot be shown to help):
- False-abstain rate: abstains the owner later judges fine (sampled owner marks).
- Extra escalations per 100 decisions caused by each new check or cap.
- Stop rule [RECOMMENDATION]: if a cap or check adds more than N extra escalations per 100 decisions and the replay shows no avoided wrong accept, roll it back. N is the owner's number (default 5).

Where this lowers human work [INFERENCE, to be measured]: (1) DI-5 stops a cached re-ask from using a revision, so an honest agent no longer reaches the cap early and escalates; (2) a diagnosis with a catalogue step lets the agent fix its evidence on the first retry instead of asking the owner or looping; (3) whitespace repeats stop costing a provider round trip. It does not reduce approvals of gated commands.

Accuracy needs audit labels (R5 section 5.2) and about 300 clean accepted rows to show a false-accept rate under 1 percent (R5 section 5.4, researcher's arithmetic).

## 12. What NOT to build

1. A model that explains Jev in free text. [VERIFIED] The parser has no explanation field; that Jev has none to give is docs-backed only until SP-0 [UNRESOLVED]. A typed choice question is an open option, measured first (section 3).
2. An NLI or LLM contradiction or "missing info" judge in the hot path. An assumption extractor or ledger.
3. A standalone refinement skill, or `jade next`.
4. A new state or event per outcome name; a typed `Verdict` class before a consumer exists.
5. Revival of `WAITING_DEPENDENCY`, `FAST`, `DEEP` routes or `abstain_to` routing (A2 sections 3, 7).
6. JADE running commands to fetch evidence. JADE reads files and git state only.
7. A rule engine (OPA, Cedar, Invariant) for the policy match. [VERIFIED] R3 section 6.2 advises against porting the rules to Rego or Cedar. Extending that to the decision pipeline is [INFERENCE].
8. Platt, isotonic or conformal code now. Semantic caches, embeddings, cross-session verdict reuse.
9. A test-runner parser zoo. Use exit status and `--junitxml` before a marker list.
10. Auto-loosening of any cap or band from approvals or repeat counts. [VERIFIED] The proxy gate counted 67 distinct approved calls, from 100 allow answers and 0 deny, against a threshold of 50. It counts answers of any kind, so it is a count, not a quality test (A6 sections 1, 2.1, 5).
11. Before the data exists: a provenance tier system before T2 works; a witness system for events no gate reads; numeric caps before SP-1; an automatic risk classifier for claims; printing the band or the missing token to the agent; an evidence `supersedes` chain; agent-typed provenance fields; per-kind TTL numbers in a gating rule.

## 13. Order of work, approvals, open points

Order [RECOMMENDATION]: (0) read-only spikes SP-3, SP-1, SP-0; (1) Tier A with no tuning number: DI-4, DI-5, DI-3a with the journal, DI-8; (2) DI-1, DI-2 with C1, C4, C5, C9 advisory, the skill paragraph, the outcome docs table; (3) after SP-1, DI-3b with numbers from data; (4) after SP-3 and hand review, let named checks lower a verdict at medium or higher, one at a time; (5) Tier B only if data shows need.

Changes that alter safety behaviour (stricter or record-only; NEEDS APPROVAL): DI-3a, DI-4, DI-5, DI-8, head-plus-tail truncation (not stricter-only), any check that lowers exit 0 to 3, DI-3b caps, DI-7, DI-9, the state rename, any `safer:` text.

[RECOMMENDATION, master plan check 2026-11-16] The roadmap (d12) builds DI-8 in V6-23 (inside the minimum v6) and DI-3a, DI-4, DI-5 in V6-24 (outside it). DI-1 and DI-2 are deferred (d12 section 6, d13 DF-05) because they matter only when `jev` is on. Head-plus-tail truncation is not built in v6. The order above is the design order, not the build order.

Human decisions (recommended default in brackets):
- O-1: Do a cap hit and a repeated no-findings abstain stop the agent's "done" at risk medium or higher, or only warn? [Warn at low; stop at medium and higher, once caps exist.]
- O-2: Is `--no-cache` for agents? [No: humans and tests only.]
- O-3: May an agent send `jev_result_*` itself? [No, witness-bound, when DI-7 is built.]
- O-4: Does a reworded claim over the same evidence count as a repeat? [Yes, as a sample count, never as a stored accept. After SP-1.]
- O-5: Is the installed skill the intended one? [Yes. Update the repo skill and add the ten lines.]
- O-6: Which merges first: `open-items-review-output` or this work (both touch the proxy, `ledger.py`, `hooks.py`)? [Merge or drop that branch first.]
- O-7: May "verified" be printed without an independent check? [No. Use "accepted by Jev, not independently verified"; the owner must change the CLAUDE.md text too.]
- O-8: How is claim risk known when the agent omits `--decision`? [Highest risk of the session's open decisions; otherwise say risk-tied strictness is not enforceable.]
- O-9: Should the agent keep seeing P and the band in exit-3 text? [Keep for now.]
- O-10: What produces `independent` outcomes? [Human adjudication now; a saved CI status file later.]

Unresolved:
- [UNRESOLVED] Jev's real body and noise (SP-0, SP-1); `tool_response` shape for Bash and the redirect join (SP-2, R3 6.3); whether every event kind sets `actor`; hook hashing cost on large outputs.
- [UNRESOLVED] Live level history: 48 proxy calls with 0 allowed (A2 section 2; A6 section 11). These are counts, not quality.
- [UNRESOLVED] Whether the provider caches state; the total spend of the 369 calls.
- [UNRESOLVED] Values that need data: all cap numbers, the Jaccard value, the stop-rule N.
- [UNRESOLVED] The chat and dialog `human_accept` gap (section 9): reported, not fixed here.
- [UNRESOLVED] Overlap with the proxy and memory sections: they may change `proxy.py`; this section does not.
