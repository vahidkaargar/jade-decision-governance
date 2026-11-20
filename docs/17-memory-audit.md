# Memory hardening and the audit of silent allows (V6-29)

This page says what changed in how JADE remembers your answers, what the audit does, and what it does not do yet. Everything here makes JADE ask more, never less. It is stage 1 only: the audit looks at every silent run, up to a cap. Sampling (stage 2) and the choice of the first scope (V6-30) are not built here.

## 1. Why

A call that runs from a standing answer is never checked by anyone. Nobody can say how often such a call was wrong. A repeat is not proof: a call run 100 times unseen is still unseen. Before this change a silent run also renewed the life of the answer, so a standing answer never aged out early. And `jade review confirm --all` took every group after one "yes".

## 2. What changed

| Part | What it does now |
|---|---|
| Audit draw | Every call that runs from a standing answer is drawn for your check. The draw is made inside the hook, in the same ledger transaction as the run. The chance of the draw (`sample_prob`) is stored with it. In stage 1 it is 1.0. |
| Audit queue | Table `audit_queue`: one row per drawn call, with the scope, the stage, the chance, the call that ran (its text, its directory and its session), the status (`pending`, `right`, `wrong`) and a seal. |
| Scopes | Table `audit_scopes`: for each scope (the scope key, else the kind key, else the exact call) the stage, the count of silent runs, and whether it is paused. |
| Your check | `jade review mark` with no arguments, in your terminal, shows the waiting calls one by one, each with the call that ran, the directory it ran in and its session (stored with the run, not taken from the text of the standing answer, which can cover other directories): right, wrong or skip. `jade review mark DECISION_ID right\|wrong` still works for one call. |
| A wrong check | Retires the answer and every answer of the same call or kind (as before), and pauses the scope. A paused scope asks about every call. Jev does not decide such a call either: `memory.step` tells `proxy.step` that the audit is why the call is asked about. |
| End of a pause | Only `jade review confirm` in your terminal, for an answer of that scope. The count then starts again at 0. A hint (a click in the chat) never ends a pause. |
| Aging by evidence | A standing answer that ran silently 60 times with no mark stops deciding. So does one that ran silently and has had no evidence for 90 days. Evidence is your confirm or a `right` mark. |
| `--all` limit | `jade review confirm --all` takes only groups of a rule below high risk. A high group is still asked one by one. One sitting confirms at most 10 groups; the rest wait. |
| `jade review` | New lines: how many calls were drawn, checked, right, wrong and waiting; how many scopes are paused; the false-allow rate with its upper bound and the session count. Under 10 sessions it shows counts and no rate. |
| `jade knows list` | Shows how many silent runs a row has had since its last mark, and when it stops. |

## 3. How the limits work in stage 1

A call that runs from a standing answer needs room in all of these, or it is asked about as before:

- the scope is not paused;
- the scope has had fewer than 59 silent runs (the cap of stage 1);
- fewer than 10 calls of the scope were drawn in the last 7 days (the weekly count);
- fewer than 10 calls in all scopes were drawn in the last 7 days (the weekly budget);
- the draw key can be read or made.

So the audit never costs you more than 10 checks a week, and it stops being a source of silent runs when you cannot keep up. Each time a limit stops a call, one event `audit_refused` is written for that scope and reason each day.

## 4. The draw cannot be read or set by the agent

- The key is derived from the permit seal key (`permit-seal.key` in the JADE home) with a fixed label. That file is already guarded by SI-10: the Bash text that names it, and the Read, Grep and Glob tools, are blocked. No new file name and no new policy text.
- No environment variable, config key or argument sets the chance, the key or the result of a draw. The module reads none. A planted `audit` config value is ignored unless it only lowers a limit (section 6).
- The key is never stored in the ledger. Each drawn row is sealed with an HMAC over the draw fields, the rule, the session, the text and directory of the call, the status and the time of the check. A mark writes the new status and seals the row again. A row edited with SQL in any of these fields fails its seal, is not counted as a check, and is not offered to you. A row that is deleted is not detected: the seal is not a chain, and a missing row is just missing.
- The commands that write verdicts (`jade review mark`, `jade review confirm`) are blocked for an agent by SI-09 and need a human terminal.
- The limit, as for the permit seal (docs/05 T19): the key is a file of the same OS user. This raises the bar. It does not remove it. A script that the agent writes and runs is outside the text match.

## 5. The numbers

The labels of a check are written with the V6-23 columns: `label_type` = `owner_would_allow` (1 for right, 0 for wrong), `sample_prob` copied from the stored draw row, `audit_source` = `random_audit`. The call id of such a label is the decision id. These labels never fit a threshold (`FIT_TYPE_SQL`). They count against the weekly adjudication cap, because the owner's time is one budget.

- Each checked call is weighted by 1 over its chance of being drawn. In stage 1 every weight is 1.
- The rate is the weighted share of wrong calls. The upper bound is the exact one-sided 95% limit (Clopper-Pearson). With no wrong call it is 1 minus 0.05 to the power 1/n: 59 clean checks give 4.95%, 149 give 1.99%, 299 give 0.99%. The old text of `jade review` uses 3/n (5.08% at 59). That figure is a little wide, and is not the number of the plan.
- With different chances the effective sample size is smaller (Kish), so the bound is wider, never narrower.
- A rate is shown only when the checks come from at least 10 sessions. Rows in one session look alike, so a rate from few sessions would look more sure than it is.
- A wrong check stays wrong. A later `right` mark on the same call does not clear it.

The claim that is allowed after stage 1 is the one in V6-30: "scope S false-allow rate under 5%, n and sessions stated". This page does not make that claim.

## 6. Limits you set, and what is the owner's decision

Defaults are in `jade/audit.py` (`DEFAULTS`). You may lower any of them in the config file under `"audit"`. A value that is not a whole number, is zero or negative, or is above the default is ignored. Raising a number is a change of code, which needs your decision.

| Key | Default | What it limits |
|---|---|---|
| `weekly_count` | 10 | drawn calls per scope per week |
| `weekly_budget` | 10 | drawn calls in all scopes per week |
| `stage1_cap` | 59 | silent runs of a scope in stage 1 |
| `age_runs` | 60 | silent runs with no mark before a row needs evidence |
| `age_days` | 90 | days after the last evidence before a row that has run silently needs evidence |

Owner decisions (D-B9 and LD-24), not made here:

1. The weekly count and the weekly budget. 10 and 10 are the existing cap on labels per week, not a measured capacity.
2. `age_runs` and `age_days`. 60 and 90 are starting values. The risk: at a low sampling chance (stage 2), a busy row may reach 60 runs before any run of it is checked, and then it asks again. That is the cost of "a repeat is not evidence". Tune it with stage 2.
3. The cap of 10 groups per sitting of `jade review confirm`.
4. Whether `--all` should take any high-risk group. The plan says no.

## 7. Migration and rollback

Additive. Two tables (`audit_queue`, `audit_scopes`), two columns on `human_answers` (`silent_runs`, `evidence_ts`), two indexes. An old ledger gets them on first open. An old row has NULL, which counts from now. Old code ignores all of it.

To roll back: stop the draw by removing the call to `audit.admit` in `memory.step`. The standing answers then decide as before, and the aging stays (it is in `memory.standing`). There is no switch in the config, on purpose: a switch an agent could flip would be a way to loosen.

## 8. Not built here

- Stage 2 (sampling with a chance below 1, a fixed weekly count). The function `audit.stage2_prob` and the `stage` column exist. Nothing sets the stage. Lifting the cap is V6-30, after 59 clean checks from 10 sessions and your written yes.
- (Built in W-207: the audit of silent allows by Jev, `jev_allowed`. A Jev yes goes through `audit.admit` and `audit.record` like a standing answer: when the scope is paused, a limit is reached or there is no key, the owner is asked; when it is admitted, the call is drawn with its sealed text and folder, and `jade review mark` checks it. A call that the audit holds before Jev is asked is still not given to Jev. They share the weekly budget with the standing answers.)
- The pause at an approve rate above 95% for the new asks. The plan marks a pause as a loosening. The approve rate of asks is shown by `jade review` (V6-23).
- Honeypots (fake asks). Not built (LD-11).
- The expiry of an unconfirmed hint older than 30 days.
- The gate on the friction and autonomy targets (D-B5, LD-21): the owner writes them from the V6-23 baseline. This build could not check that they exist.
