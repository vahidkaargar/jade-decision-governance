# 12 — The human answers in the chat (release 0.5.0); memory and Jev decide (release 0.6.0)

Status: **0.5.0 and 0.6.0 (memory of confirmed answers, Jev deciding in your place, `jade review`; W-98b to W-104) are released.** The installed JADE decides what you have: until `jade upgrade`, none of the 0.6.0 behaviour exists on your Mac. This page says what the code does today; section 10 is the owner page for 0.6.0.

## 1. Why

Before 0.5.0 every high call under `enforce` with the signed backend needed a typed code, the sudo password and a helper code in a terminal, then a "retry" message to the agent. On 2026-11-13 the owner gave 18 such approvals in one afternoon and then stopped; 18 more calls expired. The four pains: the same permission asked again, the same obvious question asked again, every answer typed in a terminal, and a chat that stops until a new prompt.

0.5.0 removes the last two. It does not reduce how often the human is asked: it turns each terminal approval into one click, in the chat or in a dialog, and the agent carries on in the same turn. Fewer questions are 0.6.0.

## 2. The level

One value for the whole Mac, set only by a human in a terminal: `jade level high` turns the new behaviour on, `jade level off` makes JADE only watch, `jade level unset` returns to 0.4.0, and `jade level low` or `jade level medium` (0.6.0) add memory of what you confirmed (section 9). The value lives in the JADE config file; a bad value counts as unset. `jade check` shows it.

| Level | What the gate does |
|---|---|
| unset | As 0.4.0: a high call waits for the signed terminal permit. |
| off | JADE only watches. High calls and ordinary safety invariants are advisory. The four governance invariants (SI-09 to SI-12: the permit, the permit key, the state and the config directory) still block. |
| high | A high call is asked, in the chat or a dialog (section 3). The signed permit stays for live JADE wiring and for what no channel reaches. |
| low | As high, except that a call you confirmed earlier runs with no question, and Jev may decide a plain high call in your place at 0.70 (egress at 0.85), once you have answered 50 plain-high calls (section 9). |
| medium | As low, with Jev's bar at 0.85 and egress always asked. |

A human-only class (section 4) is high work once a level is set, whatever its rule says. Safety invariants are never asked: nobody can approve them.

## 3. How a call is asked

The first channel that fits the call is used.

1. **Chat question.** Main session, host mode `auto` or `bypassPermissions`, gate mode `enforce`.
   - The gated call exits with a fixed text: call AskUserQuestion with a question that starts with the token `[jade:ask_<id>]`, then retry the identical call.
   - The PreToolUse hook on that question **replaces its whole text** with the hook's own: level, rule, rationale, the command (with "N characters not shown" when it is cut), and the size and head of any content the call writes. The agent's wording never reaches you.
   - The PostToolUse hook reads your answer from the host's `tool_response`. **Allow** makes a grant: single use, 10 minutes, this session, this exact call (the same hold key and fingerprints, so quoting and an env prefix do not matter). **Deny** refuses the retry, and the same call is not asked again for 10 minutes. The 3rd Deny of the same call is a plain refusal for good.
   - The retry that uses the grant opens a decision (route ASK, with an `ask` block that records the answer), so SI-07 still holds an unknown outcome of an irreversible command.
2. **macOS dialog.** A subagent has no AskUserQuestion. At a level, on a Mac, the hook shows its own dialog and waits (up to 120 s; the PreToolUse hook timeout is 130 s). Allow runs the call, Deny refuses it, no answer falls back to the signed permit and the same call is not shown again for 5 minutes.
3. **Signed terminal permit.** Live JADE wiring, a call with a secret-shaped value, a host mode that is neither `auto` nor `bypassPermissions` (headless, `dontAsk`, unknown), a non-macOS host for a subagent, and every call when no level is set.

Around the channels:

- Every answer is stored as a `hint` in the `human_answers` table. A hint decides nothing and goes to no model. `jade review` lists them (read-only).
- While an ask of the session is open (the chat question unanswered) or a dialog is on screen (any session), screen-control calls are refused so that an agent cannot press Allow: the `mcp__computer-use__*` and `mcp__MacOS-MCP__*` tools, and a shell or terminal-tool call that runs `osascript`, `cliclick` or `shortcuts run`.
- A new ask posts one macOS notification (rule and folder name, never the command).
- When the agent tries to stop with an ask that never reached the question, the Stop hook points at it once.
- 30 asks of a session that expired unanswered pause asking for that session until the human answers or they age out of the last hour. Gated calls then take the signed permit path (a permit id to approve in your terminal), so something waits for you when you come back.
- A call that failed fires `PostToolUseFailure`: the decision it ran under closes as an observed failure, so the next identical call is asked again instead of held. An interrupted call may have run in part: its hold stays.

## 4. What only a human can decide

Human-only classes (`ho_ids` on the assessment and on the decision):

| Class | What it is | Channel |
|---|---|---|
| HO-wiring | A write into the running install, the uv tool directory, the launchers, the hook files, the JADE config or state, or `uv tool` install, upgrade or removal of JADE | signed permit |
| HO-checkout-install | An install built from a JADE checkout | signed permit |
| HO-settings-shadow | A project settings write that disables or shadows the JADE hooks | signed permit |
| HO-state-path | A script that names the JADE state paths | signed permit |
| HO-mod | A write under the Claude mod directories | chat or dialog, never memory |
| HO-R17 | Skipping the pre-push guard | chat or dialog |
| HO-terminal | A call through the terminal panel tools (the agent typing into the human's shell) | chat or dialog |
| HO-delete | A delete that cannot be undone: `rm -r` of anything but a build or cache directory or a temporary directory, `find -delete`, `shred`, `git clean -f`, a force push or a remote branch delete, `git reflog expire`, `git filter-repo`, a disk erase, `docker ... prune`, `kubectl delete`, `terraform destroy`, `aws s3 rm`, `gh ... delete`, an HTTP DELETE, `DROP`/`TRUNCATE`, `rsync --delete`, and an MCP tool whose name says delete, destroy, drop, purge or wipe | chat or dialog, never memory |
| HO-money | Moving money: `stripe ... --live` on a money resource, a write to a payment API host (Stripe, PayPal, Square, Plaid ...), a crypto send, an AWS purchase, and an MCP tool that refunds, pays out, withdraws, buys or charges | chat or dialog, never memory |
| HO-release | A public release: `npm`, `cargo`, `uv`, `twine`, `gem`, `helm` publish, `docker push` to a registry that is not this machine, `gh release create`, a git push of a tag (`--tags`, `refs/tags/`, a version-shaped name, or a name that is a tag in the repository), a production deploy (`vercel --prod`, `firebase deploy`, `wrangler deploy`) and an MCP tool that publishes or releases | chat or dialog, never memory |

HO-delete, HO-money and HO-release (W-98b, `jade/humanonly.py`) read a shell command, the commands of a script file the command runs (at most 1 MiB), and the name of an MCP tool. A dry run (`--dry-run`, `-n`), a read (`git fetch --prune`, `get_invoice`, `list_charges`) and a registry on this machine (`docker push localhost:5000/x`) are not in a class; `tests/acceptance/test_human_only_w98b.py` holds the commands that must and must not match. A class never changes a call's rule or level; with a level set it raises the call to high so that a person is asked. Merging a pull request into the default branch is not a release class; it is asked like any other high call. `evals/ho_replay.py` counts what a class would newly ask about over recorded transcripts and prints counts only. Residual: a program in another language that deletes, pays or publishes (an SDK call, `subprocess.run`), a tool the name lists do not know, and a command put together at run time are not read.

JADE's own source in a checkout is plain high work, asked like any other call. `tests/acceptance/test_r13_walk_w100d.py` walks every alternative of R-13 and pins which of these it lands in.

## 5. What you see when something fails

| You see | Cause | Do |
|---|---|---|
| The agent says the gate wants a permit and gives an id | no level is set, or the call is live wiring, a secret, or a mode the chat does not reach | `jade permit approve <id>` in your terminal, or `jade level high` |
| A question starting with `[jade:ask_...]` | the chat channel | Allow or Deny |
| A dialog on your screen | a subagent call | Allow or Deny; no answer in 120 s falls back to the permit |
| A permit id although a level is set | 30 asks of the session expired unanswered, so asking is paused | `jade permit approve <id>`, or answer one ask; it ends within the hour |
| "denied this call 3 times" | you said no three times | nothing: the agent was told to stop |
| `jade check` fails `hooks installed` | hooks from before 0.5.0 (shorter PreToolUse timeout, no failure hook, no AskUserQuestion matcher) | `jade hooks install`, then open a new session |
| Nothing happens on a high call | the hook is dead or the host is older than 2.1.295 and fails open | `jade check`; `jade setup` says what is todo |

## 6. Owner steps for 0.5.0

1. Back up `~/.local/share/jade`.
2. `jade upgrade`: read the file list it prints and type `yes`.
3. `jade hooks install`, then open a new Claude Code session.
4. `jade check` and `jade ledger stats`: read the gate overrides, overlay, hooks installed and the safety block count.
5. `jade gate enforce`, then `jade level high`, then `jade check`.
6. Apply the CLAUDE.md amendment: the rule that a probability is never permission now says that a chat or dialog Allow is a human answer (the text is in `docs/claude-md-jev-section.md`; `jade claude-md apply` is yours to run).

Measure it with `jade ledger stats`: the `asks` block gives answers by channel and terminal approvals per 100 gated calls. The target is terminal approvals only for live JADE wiring.

## 7. Risks accepted

- Any program the agent starts, not only one in your terminal, can change the level or the config file, or fake one hook event. A fake approval covers one call.
- An agent that drives the mouse can press Allow when no ask is open to pause it, through a screen-control tool the name list does not know, through a background `osascript` loop started before the ask, or by a `computer_batch` already in flight.
- `uv tool install --force` of another package can overwrite the `jade` launcher; it is not classed as live wiring.
- The ask button labels are fixed (Allow, Deny), with no per-ask nonce.
- `jade upgrade` lists changed files from GitHub's compare answer, which caps at 300 files and a size; a name that cannot be shown safely is counted and the note says to read the diff on GitHub before typing yes. A ref behind the installed commit is shown as a rollback.
- A mod loaded in Claude Code can answer a chat question or approve a call JADE blocked. Mods are not locked.
- The chat channel rests on host behaviour the Claude Code documentation does not describe (`evals/probes/hook_ask/RESULTS.md`, Claude Code 2.1.314). A host update can break it. `jade check` reports the hooks; the probe can be re-run.
- Hosts older than 2.1.295 ignore `onFailure` and fail open on a hook crash or timeout.
- For a long command you approve a summary plus the head and tail, not the full text.
- Until the new hooks are installed, nothing in 0.5.0 runs: the gate is as it was.

## 8. Not in 0.5.0, and not in 0.6.0

Memory of confirmed answers (`jade review confirm`, `jade knows`, levels low and medium) and Jev deciding in your place are 0.6.0: sections 9 and 10. Still not built: batch asks, a per-project level, signed settings for the gate mode, the level and memory together, an "Allow for this session" option, a SessionStart line when Jev's band turns on, Jev's shadow advice at level off, and a calibration of Jev against 200 labels (backlog W-106).

## 9. Memory of confirmed answers (0.6.0)

Every Allow or Deny you give in the chat or in a dialog is stored as a **hint**. A hint decides nothing. `jade review confirm`, run by you in a terminal, shows the allow answers grouped by kind of call, with the rule id in brackets; you drop a group or take it, then type `yes` after reading how many answers become standing. `jade review confirm --all` takes every group after the one `yes`. A Deny is never made standing.

At level `low` or `medium` the gate looks for a standing answer before it asks. A covered call runs with no question, in any session and in a subagent. It still goes through a decision walked to EXECUTING, so SI-07 holds an unknown outcome, and the decision names the standing answer that covers it. At level `high` nothing is remembered.

**What an answer covers.** Two keys are stored. The *exact* key is the call in the state you answered it in: the canonical gated segments (independent of quoting, prefixes and directory) and the fingerprints the gate computes (the HEAD a push sends, the digest of written content). The *kind* key is the rule plus the sorted set of gated segments, with the number of `gh pr <verb> N` dropped. A kind key exists only where generalising is safe.

| Call | Remembered as |
|---|---|
| a plain gated shell command | its kind and its exact call |
| an inline program (`-c`, `-e`, a heredoc, a here-string, a process substitution), a call the gate could not read, R-14, M-05, M-07 | that exact call only |
| a git push or merge, `gh pr merge`, `glab mr merge`, a write, an MCP call | that exact call only (the head commit or the content is part of the key) |
| a human-only class (section 4), R-21 (egress), R-22 (fetch and run), a call a safety invariant blocks, a call with a secret-shaped value | never: asked every time |

**Lifetime.** A standing answer decides while its last confirm or last use is within 30 days, and never more than 180 days after the confirm. `jade knows list` (read-only) shows each one with the day it stops deciding and marks those within 14 days of the 180-day end. `jade knows rm ID` forgets one; `jade knows edit` opens a preferences file (`preferences.md` in the config directory) that starts as a template with one commented-out sentence per standing answer. `jade review confirm`, `jade knows rm` and `jade knows edit` need a human terminal and SI-09 blocks them for an agent. `jade check` has a row that counts allow answers still waiting to be confirmed.

**Limits.** The class of a gated command is the rule that won plus the segments, so two commands that share both are one kind; a command whose danger sits in an argument the key keeps (a path, a branch) is a different kind. A program the agent writes and then runs is not read by the gate (docs/05 T18), so it is remembered only as that exact text.

**Your verdict on a call (`jade review mark`).** `jade review` lists the last silent runs with their decision ids. `jade review mark DECISION_ID right|wrong`, in your terminal, records your verdict on a call that ran from a standing answer or from your answer in the chat or a dialog. `right` is a hint and changes nothing. `wrong` retires the answer behind the call and every answer of the same exact call or the same kind, standing or not yet confirmed, so the gate asks again; the latest mark of a decision is the one that counts. SI-09 blocks `review mark` for an agent.

**What `jade review` shows.** The silent share (silent runs against questions you answered, for plain-high calls, so human-only classes do not count), the runs you marked and how many were wrong, the 95% upper bound of the wrong share (three over the number of marks when none is wrong: 50 clean marks show the error rate is below about 1 in 17), and how many plain-high calls you have answered of the 50 that a model deciding in your place will need. Counts and rule ids only; the command text is the label you already saw in `jade review confirm`. Cost and latency of that model join this report when it exists.

**Jev in the hook (`jade/proxy.py`).** At level low or medium, a plain high call that no permit and no confirmed answer covers goes to Jev (question `q.human_proxy.would_allow`) with your preferences (`jade knows edit`: the lines of `preferences.md` that are not comments) and the confirmed answers you gave to calls of the same rule. An accept in the level's band lets the call run, through a decision walked to EXECUTING (so SI-07 still holds an unknown outcome); anything else is your question as before. The band is set in `policies/escalation.yaml` (family `human_proxy`): 0.70 at low, 0.85 at medium, and 0.85 for egress (R-21), which is decided at low only. An abstain, a reject, a provider that is down or late, a call that cannot be sent, and a call with neither preferences nor a confirmed answer of its rule are all your question, never a yes.

**Shadow.** Until you have answered 50 distinct plain-high calls (counted in `jade review`; one call answered 50 times, a human-only answer and a retired answer count once or not at all), Jev is shadow: it is asked, its answer is recorded in the ledger, and it decides nothing. At level high it is shadow too and what it said is not shown.

**What Jev is told, and what it is not.** The call (the command, or the path and the whole content of a write, MultiEdit and NotebookEdit included, at most 4 KiB), the rule and its reason, your preferences, up to 8 confirmed plain answers of the same rule, and the fingerprints the gate computed (a pushed HEAD, a content digest). Not the level and not the folder name. Never sent, also when the rule sits in one segment of a longer command: a human-only class, a critical call (only plain high calls are sent), fetch-and-exec (R-22), a write to an agent instruction file (R-13, R-18: AGENTS.md, CLAUDE.md, skills), a blocked call, a merge, a call the gate could not read in any segment, text that redaction changed, a program or write over 4 KiB or more than one inline program, a call you denied before, and a call Jev was already asked about twice this hour in this session (a comment added to the command does not make it a new call). Every accept is a fresh answer: the dedup cache is not used. The command is data in the state; the instructions come from the registry only.

**Time.** One exchange, no retry, at most 3 s and never past what is left of the 6 s hook budget (1.5 s are kept for the rest of the hook). A hung exchange is left behind in a daemon thread and you are asked. Each call that Jev decides adds its round trip (about 0.7 s, up to 1.5 s).

**Your verdict.** `jade review mark DECISION_ID wrong` on a call Jev let run retires any answer of that kind (your own confirmed answers that share the key too) and makes Jev skip the exact call, its kind and, for a write or another call that has no kind, its target (the rule and the path) from then on. It cannot be undone with a later `right`; `jade review` reports how many calls Jev saw, how many it allowed, their cost and median latency.

## 10. Owner page for 0.6.0: using it, and what you see when something fails

**What changes for you.** At level `high` nothing changes from 0.5.0: every high call is one click. At `low` or `medium`, three things take questions away, in this order: a call you confirmed earlier (section 9), then Jev answering for you (section 9, "Jev in the hook"). What is left is asked as before. Four classes are asked at every level but `off`: live JADE wiring, a delete that cannot be undone, money, a public release. Critical calls (a firewall change, a production deploy, key material), fetch-and-run, writes to agent instruction files and merges are asked too.

**How to start, and how to know it works.**

1. Stay at `jade level high` for the first days. Answer the questions; each answer is a hint.
2. `jade review confirm` once a day or so, in your terminal: take the groups you would answer the same way every time.
3. `jade knows edit` once: write plain sentences about what you allow and what you never allow. Jev reads only lines that are not comments.
4. `jade review` shows the count toward the 50 distinct plain-high calls you have answered. Jev is asked but decides nothing before it. After that, `jade level medium` (Jev needs 0.85) or `jade level low` (0.70).
5. `jade review` lists the last silent runs with their decision ids. `jade review mark ID right` or `wrong` is your verdict. `wrong` cannot be undone with a later `right`: it retires the answers of that kind (your own confirmed ones too) and Jev skips that call, its kind and its target for good. The 95% bound of the wrong share is printed; 50 clean marks show it is below about 1 in 17, not that it is zero.
6. Back out at any time: `jade level high` (everything asked), `jade level unset` (0.4.0).

**What it costs.** A call Jev decides adds one round trip to openrouter.ai (about 0.7 s, at most 3 s). The command (or up to 4 KiB of written content), the rule, your preferences and up to 8 confirmed answers of that rule go there, redacted for secret shapes only; ordinary text is not changed. Cost and median latency are in `jade review`.

**What you see when something fails.**

| You see | Cause | Do |
|---|---|---|
| "JADE: Jev judged this ... call from your preferences" on stderr | Jev let a plain high call run (rule, probability and bar are in the line) | Nothing; `jade review mark ID wrong` if it was not what you wanted |
| "JADE: allowed from your confirmed answer" on stderr | memory covered the call | `jade knows rm ID` forgets it |
| A question for a call you expected to run silently | Jev abstained, rejected, was late or down, is still in shadow (fewer than 50 distinct answers), or the call is one of the classes it never sees | Answer it; `jade review` shows the count and the last runs |
| The same kind of call is always asked after you marked it wrong | that is the mark: Jev skips the call, its kind and its target | `jade level high` or accept it; a wrong mark cannot be undone |
| A question for a call you denied before | a call you denied is never decided by Jev | Allow it once if you changed your mind |
| `jade check` says answers to confirm | hints wait for `jade review confirm` | `jade review confirm` in your terminal |
| `jade check` shows a level you did not set | the config file holds a level that no `jade level` event recorded | `jade level unset`, then set it again; read `jade ledger stats` for what ran |
| Jev never decides anything and `jade check` reports no key | no OpenRouter key | add the key yourself (never paste it in the chat); the call is asked as before |
| A high call is slow | Jev is answering; the wait ends at 3 s or at what is left of the 6 s hook budget | nothing; `jade review` shows the median latency |

**Owner steps for 0.6.0.** Back up `~/.local/share/jade`, `jade upgrade` and read the file list, `jade hooks install`, open a new Claude Code session, `jade check`. The level is yours to move from `high`, as in the steps above.

**Risks accepted in 0.6.0.**

- Inside the bands a wrong Jev yes can run a wrong plain high call. The wrong-rate bound shows how sure you can be; it never reaches zero.
- A delete, payment or release written in a form the human-only patterns do not match is plain high and may be decided by memory or Jev (T29 in `docs/05`).
- The model has been judged against fixtures only; its accuracy on your calls is what the 50 answers and your marks measure.
- Jev's authority check in the state machine requires that an accepted call exists, not that it belongs to this decision: it is not reachable by an agent while `human_accept` needs your terminal, and it stays a hardening item (W-106).
- Your preferences text, the command and written content go to openrouter.ai on each Jev call, at levels low and medium and, as shadow, at high.

## 11. Proposed text, not applied

These need your approval; `jade claude-md apply` and the skill are yours to change.

**CLAUDE.md, the rule "a probability is never permission".** Proposed: "A probability is never permission, with one exception the human made: at the autonomy level the human set in a terminal (`jade level low` or `medium`), JADE may let a plain high call run in the human's place when a confirmed answer of theirs covers it or when Jev's accept clears the level's bar. The hook says so on stderr. Never treat Jev's number as an approval yourself, never reword a call to draw another sample, and never run `jade level`, `jade review confirm|mark` or `jade knows`."

**continuous-flow skill, rule 8.** Proposed: "When JADE lets a call run on the human's behalf (a confirmed answer or Jev), carry on: do not ask the human to confirm it again. When JADE asks, ask once in the chat with the question the hook shows, continue with other work while it waits, and never ask the same thing a second time in other words."

