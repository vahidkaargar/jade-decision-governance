# Ledger readouts and label plumbing (V6-23)

This page says what the new numbers mean, what they do not mean, and how the two gated-call counters were reconciled. It is about measuring. It does not change any decision the gate makes. Everything here is record-only.

## 1. What is new

| Where | What |
|---|---|
| `jade ledger stats` | New sections `load`, `friction`, `verify_links`, `label_coverage`, `shadow_agreement`. `--json` prints the same data on one line with sorted keys. |
| `jade check --json` | The same rows as the text form, with `ok` true, false or null (null is a warning). The exit code is the same. It adds `load_last_7_days`. |
| `jade review` | Plain words instead of "silent share", "plain-high" and "95% bound". New lines for effort (questions asked, time to answer, asks that ran out). It says "Correctness is not measured" when that is true. |
| `jade ask verify` | A verify call with no `--decision` opens its own decision, so the call is linked. |
| `labels` table | New columns `label_type`, `sample_prob`, `audit_source`. Old rows keep NULL. |
| Outcomes | Each outcome carries `measurements.goal_check`. |

The code is in `jade/readouts.py`. The readouts read counts, rule ids and times. They never read command text. Rows from a `JADE_SOURCE=test` run are left out.

## 2. The two load counters, reconciled

The first measurement showed two numbers for one thing: 3,424 gated calls in 7 days from `jade check`, and 2,666 in 14 days from `jade ledger stats`. The 7-day number was larger than the 14-day number. Nobody had checked why.

Reading the code gives this answer. The two commands counted different events.

- `jade check` counted events `gated` and `safety_block`.
- `jade ledger stats` counted only `gated`.
- A call stopped by SI-07 (an earlier identical run is still held) writes `gated` first and `safety_block` after it. `jade check` counted that call twice.
- A call stopped by an invariant before the permit step (for example SI-08) writes `safety_block` only. `jade ledger stats` did not count it.

There is one definition now (`readouts.load_counts`). A gated call is every `gated` event, plus every `safety_block` event that is not the second event of an SI-07 hold. Both commands use it. The parts are printed next to the total, so a reader can see what was counted:

- `calls`
- `gated_events`
- `invariant_blocks_without_gated_event`
- `si07_blocks_counted_once_in_gated`

The `asks` section of `jade ledger stats` uses the same number. Its `gated_calls` is now `calls` (it was `gated_events` before V6-23), and `per_100_gated` is divided by it. An invariant block can never become an ask, so it makes the per-100 figures lower than before for the same answers. Old `per_100_gated` output is not comparable with new output. `asks.gated_events` is printed beside `gated_calls`, so the old denominator can be read too.

What is still not known. The code reading explains how the two counters can differ. It does not prove that this explains the whole gap in the first measurement (the 7-day number was larger than the 14-day number, which needs at least 758 safety blocks in 7 days). The owner can check this: run `jade ledger stats --days 7 --json` and read the `load` section. If `invariant_blocks_without_gated_event` plus `gated_events` is not close to 3,424, something else is going on. This is the owner step for U-10. No real ledger was read to write this page.

Two other differences remain on purpose. The `events` block of `jade ledger stats` has no time window (it counts all time). The `jade check` row "host mode bypass" counts calls with no host prompt, and now counts them the same way.

## 3. Friction numbers (effort of the human)

`friction` in `jade ledger stats`, and the lines in `jade review`, come from the `asks` table.

- **asked**: asks created in the window.
- **allowed**, **denied**: the answer. An Allow that ran out unused is still an answer.
- **expired**: never answered and over (status `EXPIRED` or `GAVE_UP`, or still waiting with its time past: the end of an ask is written when the next call looks, so JADE also counts a waiting row whose time has passed).
- **open**: still waiting.
- **approve_rate**: allowed over allowed plus denied.
- **answer_seconds**: seconds from the ask to the answer. Times have one-second resolution. A dialog is shown as soon as the ask is written. A chat question is shown after the agent calls the question tool, so a chat answer includes that wait.

These numbers say how much effort JADE asks of the human. They do not say whether the answers were right. A high approve rate can mean the questions are routine, or that answering has become a habit. The counts cannot tell which. No fake asks are used to test this. The plan keeps passive numbers only (LD-11).

## 4. Verify calls linked to decisions

`verify_links` counts the main Jev verify question (`q.verify_claim.supported_by_evidence`, not its companion) and how many carry a decision id. It also counts decisions that no Jev call was ever attached to.

Before V6-23 a call was linked only when the agent passed `--decision`. Now `jade ask verify` with no `--decision` opens a new decision of its own (objective "Verify claim: ..."), runs the call against it, and the answer moves that decision as it already did for a named one. The rules:

- The decision is always new and always in this session. A decision the caller did not name is never advanced.
- A dry run opens nothing. Only `jade ask verify` does this, not `noul`, `choice` or other questions.
- If the ledger cannot be written, the call runs unlinked as before. Linking never stops a question.
- The hook does not read ACCEPTED, so these decisions open no gate and need no permit.

A scripted run of 20 calls (`tests/test_readouts_v623.py`) links every call. The target of the plan is at least 95%.

## 5. Label kinds and the goal check

A label can say three different things. They are never merged.

| `label_type` | Question |
|---|---|
| `claim_true` | Was the claim correct? This is the only kind that fits a threshold. A row with NULL is read as `claim_true`. |
| `owner_would_allow` | Would the owner have allowed the call? |
| `harm_observed` | Was harm seen later? |

One call holds one kind. `evals.set_label` refuses to change a call to another kind (it raises `LabelTypeConflict`). Every reader that fits or reports (`evals.labeled`, `evals.choice_rows`, the canary check in `jade/improve.py`) takes only `claim_true` rows. The table has one row per call, so a second kind on the same call needs a new table. That is a later step (d06 section 8.2) and is not built here.

`sample_prob` is the stored chance that the call was drawn for an audit (more than 0 and at most 1). Nothing draws audits yet; the draw is V6-29. `audit_source` is a short code: `random_audit`, `adjudication`, `owner_mark` or `ci_file`. It is never free text.

**DI-8, the goal check.** An outcome says who saw that the goal was reached.

| `goal_check` | Who | Labels a call? |
|---|---|---|
| `exit_only` | The hook, from the exit status | No |
| `agent_reported` | An agent said so (`jade decide outcome`) | No |
| `independent` | A check the agent does not control | Yes |
| `human` | A person at a terminal | Yes |
| (no mark) | An outcome from before V6-23 | No |

The mark is written by code. It is not read from the method text, and a `goal_check` key in a caller's dict is overwritten. Text such as "ci passed" stays `agent_reported`. The state path does not read it: an exit-status close still reaches SUCCEEDED and still releases the SI-07 hold. Only the label changes. Decisions that would have been labelled but were not fit are counted in the `not_fit` part of `evals.auto_label`.

Today no code writes `independent` or `human` to an outcome. So `jade label auto` makes no new label from outcomes until a producer exists. Labels from `jade label set` (adjudicated, by a human at a terminal) still work and still fit. The real number of fit labels will stay small for a long time. That is the true state, not a defect.

The label filter is decision LD-31 of the plan, and it needs the owner's yes. This build found no recorded answer. The filter only removes labels, so the risk is low, but the owner must say yes to LD-31 before this is merged.

## 6. Shadow agreement

`shadow_agreement` compares Jev's answer with the owner's for the same call. It uses only rows that exist already: the ask (session and hold key), the `jev_asked` event of that pair just before the ask, and the proxy's Jev call. Since this fix the proxy writes a `jev_answered` event (the call id only, no text) for the same session and hold key, so the pair is exact. A ledger from before it has no such event: the call is taken only when exactly one proxy call of the session fits between the two, and no call is used for two asks. A session runs tool calls in parallel, so "the newest call of the session" can belong to another command; the join never guesses it.

- **owner_denies**: asks the owner denied that Jev was also asked about.
- **jev_caught**: of those, Jev did not allow (abstain or reject).
- **jev_false_allows**: Jev allowed what the owner denied.
- **recall**: jev_caught over owner_denies, with a 95% lower bound.
- With no owner deny, recall prints as "not measured". It is never 100%.

Limits. A call that Jev let run in the owner's place never became an ask, so it is not in this join. The audit of those calls is `jade review mark`. Asks the owner never answered are not in it. Counts are shown so a small number is seen as small. In the first measurement the owner had denied 0 of 100 answers, so recall may stay "not measured" for a long time.

## 7. Migration and rollback

The migration is additive. Three columns on `labels` (old rows stay NULL), and three indexes (`asks_created`, `decisions_created`, `calls_decision`) so the new readouts do not scan tables that grow. Old code ignores the new columns. To roll back, stop writing them. The first open of an old file runs the migration once, inside the hook budget (`tests/test_readouts_v623.py`).

## 8. Not done here

- The numbers from the real ledger (U-10, the baseline). The owner runs `jade ledger stats --json` and `jade check --json`.
- The friction and autonomy targets. The owner writes them after the baseline (LD-21). V6-29 does not start without them.
- A producer for `independent`.
- The audit draw and `sample_prob` values (V6-29).
