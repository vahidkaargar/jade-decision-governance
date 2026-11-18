# Decision hygiene (V6-24)

This page says what V6-24 changes in `jade ask` and in the decision state machine, why, and how to undo it. It also lists the three spikes. Every change here can only turn a "yes" into "not yes", record more, or give back an answer that was already stored for the same claim. None of them changes the gate: the PreToolUse hook never read `ACCEPTED`.

## Why

Before this change, the "verified" answer of `jade ask` could be bought or faked in four ways:

- A trailing space or a Windows line ending made a new text, so Jev was asked again and a lucky answer could win.
- `--no-cache` asked again with no limit.
- `jade ask --risk` defaulted to `low` and ignored the risk of the decision it was linked to.
- An agent could send `jev_result_accept` itself and walk a decision to ACCEPTED with no Jev call at all.

The harm was a false "verified" and dirty labels, not a wrong gate decision.

## What changed

| Id | Change | Effect |
|---|---|---|
| DI-3a | The repeat key ignores ANSI colour codes, CRLF (read as LF), trailing space or tab on a line and trailing blank lines. Nothing else: no case folding, no digit mapping. | A whitespace-only repeat returns the stored verdict with no provider call. A reworded claim is still a new call. The text that is sent and stored is the original. A text with none of these hashes as before, so stored repeats still match. |
| DI-4 | `jade ask --decision D` judges at the stricter of `--risk` and the risk recorded for D. A warning says when D raised it. | A claim linked to a high decision is judged at the high band even with `--risk low`. Limit: an agent that does not give `--decision` still gets `low`. (V6-23 opens a decision for `jade ask verify` with none given, but that one is rated low.) |
| DI-5 | A decision that waits for evidence (ABSTAINED or NEEDS_EVIDENCE) and has used all 3 revisions gets no provider call. `jade ask` returns exit 3 and says: escalate, or report the claim as unverified. A cached re-ask of an answer the decision already holds uses no revision. | An honest agent no longer reaches the cap by asking the same thing again. After the cap, more asks buy no new sample. |
| `--no-cache` (H16) | Only a human terminal or a `JADE_SOURCE=test` run may use it. An agent gets the stored answer and a warning. It is capped at 3 provider answers per claim and evidence in a session (the first included). A fresh answer is never looser than the first one. | No unlimited resampling. The human proxy path (`jade/proxy.py`) is unchanged: it still asks fresh each time. |
| H17 | `jev_result_accept`, `jev_result_abstain` and `jev_result_reject` are not agent events any more: `jade decide event` refuses them. `jade ask` writes them and names the call. In process, an event with `actor="agent"`, or with a `call_id`, needs a finished call of that decision whose exit code can stand behind it. A result may be stricter than its call (a companion check lowers an accept to an abstain), never looser. | An agent cannot send a Jev result without a call. It can still send `fast_accept` or `deep_accept` (see "Not fixed" below). |

Every ask that was a repeat, or was stopped, writes one `ask_repeat` event with a reason: `exact`, `whitespace`, `no_cache` or `cap_hit`. `jade ledger stats` shows the counts as `repeat_reasons`.

Not fixed in this card (found): an agent can still send `fast_accept`, `fast_reject` and `deep_accept` through `jade decide event`, so it can still walk a decision to ACCEPTED with no Jev call. This is the rest of HR-01. The gate does not read ACCEPTED, so the harm is the same as before: a false "verified" and dirty labels. It needs its own owner decision.

The limit of "human and test only" for `--no-cache`: the test path is `JADE_SOURCE=test` in the environment, and an agent can put that variable in front of a `jade` call (the gate rates `JADE_SOURCE=test jade ask ...` as low risk). So the cap (3 provider answers per claim and evidence) and the never-looser rule are the real protection, not the human check. A resample from the test path is journalled like any other.

Not in this card: head-plus-tail truncation (it can flip a verdict either way), near-duplicate claims and sample caps (DI-3b, they need the SP-1 numbers), DI-1 and DI-2 (deferred, they matter only when `jev` is on).

## Risks

- More exit 3. A human or an agent that relied on a lucky resample now gets an abstain. The stop rule from the plan: more than 5 extra escalations per 100 decisions, with no avoided wrong accept, means roll back. The owner sets that number.
- `jade decide event ... jev_result_*` stops working for scripts. Use `jade ask --decision`.
- The cap number 3 is a guess until SP-1 is run.
- The cap and the risk link bind only when `--decision` is given. An agent that leaves it out is still judged at `--risk`.

## Rollback

Revert the commit. There is no migration: no table and no column changed. Old rows and new rows read the same. The `ask_repeat` events stay in the ledger and are ignored by old code.

## The spikes (read only, `evals/spikes.py`)

Run these yourself. An agent must not.

| Spike | Command | What it tells you | Needs |
|---|---|---|---|
| SP-0 | `uv run python evals/spikes.py sp0 --out raw.json --owner-ok` | One live call with a fixed, harmless fixture. The raw body goes to `raw.json` (mode 600). The output lists every field and flags long strings. It answers: is the Jev body only typed answers? | Your word: it sends text to openrouter.ai and uses your key. |
| SP-1 | `uv run python evals/spikes.py sp1 --out noise.json --owner-ok` | 6 trivial variants of one claim, 20 calls each (120 calls). Min, max, mean, spread and the share at or above 0.70 for each. It sets the numbers for DI-3b. | Your word: same, and it costs money. |
| SP-3 | `cp <ledger> /tmp/copy.sqlite` then `uv run python evals/spikes.py sp3 --ledger /tmp/copy.sqlite` | Which cheap checks (C1 short evidence, C5 claim token missing, C6 failure marker, C9 truncation) would have fired on each stored verify call. Counts and call ids only, never the stored text. | A copy of the ledger. It opens the copy read only and refuses the live file. |

No spike was run in this card: SP-0 and SP-1 need your word, and SP-3 needs a copy of your real ledger. The checks in SP-3 are advisory. No check may lower a verdict until you have read every hit by hand and approved that check.
