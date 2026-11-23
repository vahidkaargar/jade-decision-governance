# Human experience specification

This section answers one question: what must a person see and do when JADE needs them, so that the person can decide correctly with the least effort? It covers the question, batching, the channels, the permit screen, how commands are shown, language and right-to-left text, the owner's "simple English" rule, and how to measure friction without watching the person. Every claim carries a tag: [VERIFIED] with its evidence, [INFERENCE], [RECOMMENDATION], or [UNRESOLVED]. Anything that changes safety behaviour is marked "needs human approval". Short English is used on purpose.

**Ref for threat-row ids.** [VERIFIED, `git show origin/main:docs/05-threat-model.md`] A `T` id in this file (T14, T18 to T29) is a row of docs/05 on `origin/main` (e9412f3) unless another ref is named. `T30` is the real-shell harness row on `origin/main`. `T31` and `T32` exist only on unmerged branches (PR 42 and PR 46); the same number means a different row on different refs (d01 B7).

## 0. Base, assumptions, refs, and whether this is worth building

- [VERIFIED, A7 section 0] `origin/main` is release 0.6.1 plus later merges (PR 48). The clear-question work is NOT on main. The plain first lines (`jade/askplain.py`: `RISK_TEXT`, `SEGMENT_TEXT`, `what_line`, `header`) are on PR 42 (`design-clear-questions`, file of 187 lines). The same 187-line file is on PR 44 (`open-items-scopes`) and PR 45 (`open-items-review-output`). [VERIFIED, `git show <branch>:jade/askplain.py`, line counts and symbol counts] PR 43 (`open-items-plain-text`) grows that file to 357 lines: it adds push-line work (`_push_line`, `_plain_push_call`) and `tests/acceptance/test_plain_text_fixes.py`. PR 43 and PR 44 both start from c214d9d and neither contains the other. So PR 44 and PR 45 carry the base lines but not the PR 43 push refinements. This is a merge-order risk, not a text risk.
- [INFERENCE, assumption, reversible] "v6" means the 0.6.x line plus main plus this unmerged stack. Every code claim below names its ref: "main" means `origin/main`; "PR 42" means branch `design-clear-questions`; and so on.
- [VERIFIED, A7 section 11, R6 limits] Nobody ran a live host for this study. What the Claude Code chat box and the macOS dialog really draw (right-to-left runs, bidi characters, Markdown, option descriptions) is [UNRESOLVED]. Section 13 gives a one-hour probe that closes most of it.
- [VERIFIED, A6 section 2] The live level on this Mac is `off`. The ledger holds 100 owner answers: 100 allow, 0 deny. [VERIFIED, A4] At level `off` the `AskUserQuestion` rewrite does nothing.
- [UNRESOLVED] The owner's preferred language for human text. We only know that English is not his first language.

### 0.1 Is a big human-experience build worth it? Probably not yet.

[VERIFIED, 04-adrs section 0.1 bullet 4, A8 section 11] The human sees about 4.5 percent of gated calls (100 asks and 20 permits out of 2,666 in 14 days). This is one owner on one machine. [INFERENCE] The friction problem may be small, and it has no baseline at the level that matters. [VERIFIED, A6 section 2] A record of 100 allow and 0 deny cannot show whether the questions are good or only clicked through. So this section does NOT ask for a large build. It splits the work in three tiers:

| Tier | What | When |
|---|---|---|
| 1. Must do (safety display) | Escape bidi and control characters in the chat and dialog text. A plain line must not say less than the call can do. Fix the two-code bug on the permit screen. Journal a refused permit. State the consequence of no answer in the chat question. | Now. Small, tested, no new design. |
| 2. Measure | Run the owner at a level above `off` for two weeks. Read the ledger for counts and clipped commands (section 9). Run the offline misread test (section 9.3). | After tier 1. |
| 3. Everything else | Fixed order and budget of a bigger question, the message-id package, Persian text, batching, a weekly review sample. | Only when tier 2 data or an owner request shows a need. Each has a trigger in section 11. |

**Correction (master plan check, 2026-11-16).** The table above calls all of tier 1 "Now". The roadmap (d12) splits it. Only the safe display of chat and dialog text (escape bidi and control characters, show the gated part of a clipped command) is in the minimum v6, as V6-09. The rest of tier 1 (plain lines that list every widening flag, the two-code permit fix, journaling a refusal, the "no answer" line, the bundled questions) is V6-26, which is outside the minimum v6 and waits for the host probe V6-15. The roadmap choice wins (d12 section 8; d13 DF-36, LD-15).

[RECOMMENDATION] The baseline against which every addition is judged is "fix the display bugs only" (tier 1). An addition must show, by a test or by ledger data, that it helps the owner decide correctly. "It looks more complete" is not a reason.

## 1. Decisions in one table

| # | Topic | Decision | Safety change? |
|---|---|---|---|
| 0 | Baseline | Tier 1 only (section 0.1). Judge every other row against it. | Display only |
| 1 | One question builder | One function builds the question text for chat, dialog and permit screen. It takes the channel constants as inputs. It lives in `askplain.py` (English, tested). | No |
| 2 | Anatomy | Fixed line order, a length budget, one safe-display function for the command. | Display only |
| 3 | Recommendation | None. JADE states facts only. It never says "suggest Allow", "suggest Deny" or "use the terminal". | Yes: needs approval (it drops a field the owner's spec asked for) |
| 4 | Consequences | Deny and "no answer" are stated in every question, with the numbers of that channel. | No |
| 5 | Batching | Do not build. Write the rules for a later day. | n/a |
| 6 | Channels | Keep the rewritten `AskUserQuestion` as the main channel. Re-probe the native box first. | No |
| 7 | Permit screen | Keep the typed code. Fix the two-code bug, add plain lines, record a refusal. | Yes: needs approval |
| 8 | Clipped command | Show the gated segment in full. Remove chat Allow only if ledger data shows clipped gated calls are common. | Yes: needs approval |
| 9 | Language | Text only from fixed, reviewed templates. Never model-written. Labels stay `Allow` and `Deny`. | Yes: needs approval |
| 10 | Persian now? | No. Wait for the owner's answer and the host probe. | No |
| 11 | Plain mode | Plain is the standard of the first block. No switch. | No |
| 12 | Metrics | Local ledger only. Per rule, not per person. Add an offline misread test. | No |

## 2. Anatomy of a question

### 2.1 The required fields

The owner's list: concise problem, why a human is needed, options, a recommended option, evidence and trade-offs, consequences and reversibility (Deny and no answer included), the minimum action, the working folder, the agent identity. [VERIFIED, `policies/escalation.yaml` on main; A7 section 7] The file already promises five fields (`context`, `impact`, `options`, `recommended_default`, `consequence_of_no_answer`), and no code reads them (backlog W-83; repo docs 03 section 3 promises them too). [RECOMMENDATION] When the builder is written, add a test that every question has each field. That closes W-83 by building the feature. `recommended_default` is read as a fact, not advice (2.7).

### 2.2 Today, field by field

| Field | main (0.6.1) | PR 42 and later (unmerged) | Gap |
|---|---|---|---|
| Problem | Rule id and the raw rationale, then the command. [VERIFIED, A7 s2] The R-10 rationale is 30 words of jargon in `policies/risk.yaml`, 36 with the prefix "JADE gate: high risk, rule R-10." | A plain first line from `SEGMENT_TEXT`, then one plain risk sentence from `RISK_TEXT` (about 35 sentences, each at most 20 words, tested). [VERIFIED, `askplain.py` on PR 42] | The plain line can say less than the call does (2.3). |
| Why a human | The line "only you can decide these" for human-only classes only. | Same, plus what the class lets the call do (`HO_TEXT`). | The usual reason (high risk, no standing answer) is never said. |
| Options | Allow and Deny, with one-line descriptions. [VERIFIED, `askchat.question_for` on main] | Plus up to two "suggest" options (PR 42). | OK |
| Recommendation | None. | None. | By design (2.7) |
| Evidence | The command, clipped to 450 head and 150 tail characters. Write size and hash. No folder. | Plus names of the gated segments. | Folder, agent |
| Consequence of Allow | "Single use, 10 minutes, this session only." | Plus scope lines. | OK |
| Consequence of Deny | In the option text only. | Same. | Partial (2.5) |
| Consequence of no answer | Chat question: never shown. Dialog: shown in short form, "Deny, or no answer, blocks it." [VERIFIED, `askdialog.text_for` on main] | Same. | Gap in the chat question only |
| Reversibility | None. | Inside some risk sentences. | Own line needed |
| Minimum action | Two buttons. | Same. | OK |

### 2.3 A plain line must never say less than the call can do

[VERIFIED, A7 section 2.1; `askplain.py` line 96 and `tests/acceptance/test_ask_plain_text.py` lines 54, 57, 104 on PR 42] The call `gh pr merge 940 --squash --delete-branch` gets the line "Merge pull request 940." The regex accepts `--delete-branch`, `--admin` and `--auto` and drops them. The test pins the short line. The design spec of PR 42 itself wanted "and delete its branch". The opposite error also exists [VERIFIED, A7 s2.1]: a plain `git push origin main` inside a joined call gets the worst-case push line. That is fail-safe, but it teaches the owner to ignore the line.

[RECOMMENDATION, tier 1] Make this a rule for every entry of `SEGMENT_TEXT`: a segment line lists every flag that widens the effect (delete a branch, skip checks, merge later by itself, force). One test per family feeds the widening flags and asserts the words appear. When JADE cannot read a call fully, the line says so in plain words, next to the worst case. A wrong line gives false comfort, so a human reviews the final wording.

### 2.4 The challenge: more fields can hide the command

[VERIFIED, R6 section 1.3, Checkmarx "Lies-in-the-Loop"] Attackers pushed the real command out of view with long text. [VERIFIED, R6 section 1.5 item 3, probe9 on PR 44 (`open-items-scopes`); `_clip` is identical on main, `askchat.py` lines 43-46] A 1,092-character call with a fetch-and-run segment in the middle was shown with "characters not shown", and the question offered Allow and showed no guard for the hidden part. [INFERENCE] Allow would still be granted by a click: only the question text was built, no click was run. So every field we add is a cost.

[RECOMMENDATION, tier 3, only if tier 2 shows a need] A fixed order: (1) what will happen, worst case, at most 20 words; (2) why you, one reason from 2.6; (3) undo, one conservative line; (4) where and who; (5) the command, escaped, with its own label; (6) the ask reference `[jade:ask_...]`. Lines 1 to 4 total at most 6 lines and about 100 words. The command starts before line 12. The consequences of Allow and Deny go into the option descriptions. [INFERENCE] Option descriptions already exist (`askchat.py` on main). Whether the host draws them readably is [UNRESOLVED] until the probe. [VERIFIED, `askchat.settle`] The answer is matched by `TOKEN.search` on the question text, not by position, so the token can move to the last line.

### 2.5 Deny and no answer, as the code really behaves

All of these are [VERIFIED, `jade/askchat.py` and `jade/askdialog.py` on main] unless marked.

- Deny (chat): the call does not run. The agent is told. The same call is not asked again for 600 seconds (`ASK_TTL_S`). The third Deny of the same call ends asking for good (`DENY_CAP = 3`). On refusal the agent gets a message that says not to rephrase around the answer.
- No answer in the chat: the ask expires after 600 seconds. The call stays blocked. `askchat.paused()` is a rolling window. It counts asks of this session with status `EXPIRED` or `GAVE_UP` created in the last 3,600 seconds (`PAUSE_WINDOW_S`) and after the last answer. At 30 or more (`EXPIRE_PAUSE`), no new ask opens. The pause ends when those asks age out of the window or when the human answers. Gated calls then take the signed terminal permit path. Dialog asks that gave up count too.
- No answer in the dialog (subagent): the dialog gives up after 120 seconds (`DIALOG_WAIT_S`), and the signed permit applies. The same call is not shown again for 300 seconds (`GAVE_UP_TTL_S`).
- The dialog's default button is Deny (`askdialog.py`). The chat lists Allow first (`askchat.py`). Whether the host pre-selects the first option is [UNRESOLVED] (R6 section 2.2). Defaults steer people (Johnson and Goldstein, secondary source only, [INFERENCE]).
- [UNRESOLVED, R1 section 3.12] If the owner sets `askUserQuestionTimeout`, the host closes an open question after a delay and "submits options already chosen". [VERIFIED, `settle`] The code grants only when the answer key holds the token and the label is exactly `Allow`. [INFERENCE] So an empty or timed-out response gives no grant. The real response shape is not tested. [RECOMMENDATION, tier 1, no safety change] One test: feed `settle` an empty `answers`, a missing `answers` and a null label, and assert no grant. Add a `jade check` warning when `askUserQuestionTimeout` is set.

[RECOMMENDATION, tier 1] State the no-answer fact in the chat question. The dialog already does. Mock wording, not final: "If you do not answer, the call stays blocked." Numbers appear only when the builder takes them from the channel constants: chat 600 seconds, dialog 120 seconds and 300 seconds, permit 30 minutes. The builder gets the constants as inputs, and one test per channel reads them, so a change of `ASK_TTL_S` cannot leave a false sentence behind. One fixed "10 minutes" for all channels would be false in the dialog. This follows the repo's learned rule: text that promises system behaviour is code.

### 2.6 Why a human is needed: a fixed list, not free text (tier 3)

The reason must come from the decision path, never from a model. [INFERENCE] Five reasons cover today's paths: `ho_class` (only you can decide this kind of call; `a.ho_ids` non-empty), `high_no_memory` (high risk and no standing answer), `memory_expired` (an answer exists but expired), `jev_not_sure` (the model that helps JADE was not sure enough; proxy result below the band), `fallback` (asking was paused, so the call needs a terminal approval; `paused()`).

[VERIFIED, `jade/proxy.py:34` on main, also `jade/ops.py:716`] The Jev proxy and its question id `q.human_proxy.would_allow` are on main (and on PR 42, 43, 44). Only `memory.honoured` and `honoured_allows` (`jade/memory.py:143`) exist on PR 45 (`open-items-review-output`) alone. [UNRESOLVED] Whether the ask row carries the proxy result when `question_json` is built. Do not show `jev_not_sure` until it does.

### 2.7 The recommended option: challenge, then decide

The owner's spec asks for a recommended option. Three honest ways to give one:

- Option A: no recommendation (today). Cheap and neutral. The owner has no help with a long or odd command.
- Option B: show Jev's advice, labelled as advice. [VERIFIED, A2, adapter and parser] Jev returns typed probabilities only. The parser has no explanation field, so the text would be ours, built from a number. [VERIFIED, A6 sections 4 and 6] The proxy has no calibration data and no measured agreement with the owner. [VERIFIED, R6 section 1.2, OWASP ASI09] OWASP asks for plain-language risk text, "not model-generated rationales", and for trust calibration. An uncalibrated probability is not a probability. Showing it would anchor the owner. Rejected.
- Option C: facts only, no suggestion. Show only deterministic facts about the call: what it does, what is not shown, that JADE could not read part of it, which human-only class applies. No line says "suggest" anything.

Why not a "suggest Deny" or "use the terminal" line? [INFERENCE] It is a recommendation channel with nothing to measure it, and "use the terminal" pushes the owner to the channel he abandoned after 18 approvals (repo docs 12 section 1). Why not a history line "you allowed this kind 3 times"? [INFERENCE] In a ledger of 100 allow and 0 deny, it works as a "suggest Allow" by another name. It would also read "0 and 0" for a first call, which is noise. OWASP ASI09 warns against exactly this anchoring. A history line is allowed only in one case: a prior human Deny of the same kind exists. That is a fact that argues for caution. It never shows allow counts.

[VERIFIED, repo `docs/05-threat-model.md` T5 and `jade/decide.py:25`] The only existing rule is that Jev can raise a risk level and never lower it (and since 0.6.0 a Jev accept can let a plain high call run, as the owner's delegation). That rule is about levels. It is not a general rule about text. So "no suggestion in any direction" is a NEW design choice, a proposal, and it sits beside the Jev-decides lever. It needs human approval, because it drops a field the owner's spec asked for. Default: Option C.

The config word `recommended_default` means: the default JADE applies with no answer is always "blocked". That is a fact of the engine, and it is the "no answer" line.

### 2.8 Where and who

[VERIFIED, A7 section 2] The question shows no working folder; the permit screen does (`cwd`). The notification shows the folder name, but `askchat._project()` keeps only `[A-Za-z0-9._ -]`, so a Persian folder name becomes "a project". [VERIFIED, `askchat.applicable`] The chat serves the main session only (`not event.get("agent_id")`). [VERIFIED, `askdialog.applicable`; `evals/probes/hook_ask/RESULTS.md` cell 8c/8d, host 2.1.314] The dialog serves a subagent, and that event carries `agent_id` and `agent_type`. [RECOMMENDATION] Show the agent type in the dialog text and the folder in one line, both through the safe-display function (section 6). [UNRESOLVED] Whether `agent_type` reads well to a human, and what the main-session event carries (section 13). For the main session show "main session".

## 3. Batching

[VERIFIED, `askchat.rewrite` on main; A5 lines 32-35] The hook searches the whole `tool_input` for a token and replaces the questions with exactly one: `{"questions": [q]}`. The shipped continuous-flow skill tells agents to send one `AskUserQuestion` with up to 4 questions. So if an agent bundles its questions with a JADE token, its other questions are dropped without a trace. The 4 is a skill rule, not a verified host limit [UNRESOLVED]. The only test uses a one-element list (`tests/acceptance/test_chat_ask_w100b.py:69`). [VERIFIED, repo docs 03 section 3, repo docs 05 T14, repo docs 12 section 8] Escalations are not batched on purpose (W-35), and batch asks are "still not built". [VERIFIED, `askchat.step`] An open ask for the same exact call is reused: that is dedup, not batching. [VERIFIED, repo docs 12 section 3] Each Allow is one single-use grant for one exact call.

### 3.1 The bundle conflict (tier 1, needs an agent-behaviour test)

- Option A (default): keep the agent's non-token questions after the JADE question. [VERIFIED, A5 recommendation 9] A5 proposes the same. A test checks that no agent question is lost.
- Option B: refuse a bundle with the message "send the JADE question alone". Risk: it collides with the skill rule and the agent may loop.

Either way a test must run the agent after the change and see what it does. This changes an existing path, so it needs human approval.

### 3.2 Rules for batching, for a day when it is wanted

[RECOMMENDATION] Do not build it now: no measured complaint (T14: revisit at the first fatigue complaint). If built, all of these hold, and they need human approval: each item is its own full question with its own ask id and answer (the batch is only a screen); same session, folder and agent; items are independent; each item is below `critical`, not human-only, not irreversible, no money, release, secret or wiring; and never so many items that one command is pushed out of view. Never batch destructive calls (HO-delete, R-11, R-03), money (HO-money), release (HO-release, R-10 publish forms), wiring (HO-wiring, R-13, R-16), key material (R-04), secrets, anything needing the terminal permit, or a scope option: a click that widens authority needs full attention. Better than batching [VERIFIED]: fewer separate risky calls through the agent rule "send one risky action per call" (PR 42, `skills/jade/SKILL.md`, unmerged) and through scopes (PR 44 and 45, unmerged).

## 4. Channels

### 4.1 Order of channels

1. No question at all, when a safe path exists: memory (a confirmed answer, levels low and medium), the Jev proxy inside its band, host sandbox and host rules (all on main), and scopes (PR 44 and 45, unmerged). [VERIFIED, A6, repo docs 12] [VERIFIED, R1 section 4] Anthropic reports 84 percent fewer prompts from sandboxing: an internal vendor figure with no method or sample size given. Do not use it as a target. The 93 percent "approved" figure is [UNRESOLVED] (R1 through a summary, absent in R6). Do not quote it.
2. The native host box. [VERIFIED, `evals/probes/hook_ask/RESULTS.md` cells 1 and 11, host 2.1.314] In `auto` and `bypassPermissions` a hook `ask` did not reach the owner. [UNRESOLVED, R1 3.13] A settings `ask` rule and a `PermissionRequest` hook answer were never probed ("Not run" row of RESULTS.md). [RECOMMENDATION] Do not design on the native box until those cells run. If a settings `ask` rule works for fixed classes, the host would show its own text. That trades JADE's plain lines for host text, so it needs a decision after the probe.
3. The rewritten `AskUserQuestion`. [VERIFIED, `askchat.py`, probe cell 5b] It works in `auto` and `bypassPermissions` in the main session. This is the main channel and stays.
4. The macOS dialog for a subagent. [VERIFIED, `askdialog.py`] Default button is Deny.
5. The terminal permit. [VERIFIED] For live wiring classes, a call with secrets, headless runs, and the paused fallback.

[RECOMMENDATION] The same text builder feeds channels 3, 4 and 5. Today three files copy the layout (`askchat.question_for`, `askdialog.text_for`, `permits.review_text`) and the permit screen prints the raw rationale (A7 section 4).

### 4.2 How to avoid needless interruptions

[VERIFIED, main] An open ask for the same call is reused. No new ask opens in the Deny window. Asking pauses after 30 expired asks in the window. One notification per new ask. [VERIFIED, PR 42 only] The agent rule "send one risky action per call". [VERIFIED, PR 43] The first block says how many other commands in the call JADE rates low. [INFERENCE] The largest cut will come from asking less, not from asking better; the level, memory and Jev-proxy sections of the master plan own that. Section 9.2 shows how to find questions that are always allowed and may be removed.

## 5. The permit screen and the six-character code

### 5.1 Facts

[VERIFIED, A7 section 4, `permits.py:467-515`, `cli.py:172-229`] The screen shows the permit id, risk, rule (raw rationale), tool and folder, a plan hash, and the whole command, escaped, each line prefixed `  | `. A command over 20,000 characters is refused, not clipped. The code is the first six characters of the plan hash (or the content digest for a write). Any other input prints "not approved" and returns 1.

[VERIFIED, A7 section 4] Four defects:

1. Two codes that cannot match. `confirm_code` is the lowercase plan-hash prefix. The signing helper `scripts/jade-approve-sign` shows `code_of(message)`, uppercase, from the whole approval message, and says "the code must equal the one `jade permit approve` showed you". It cannot. The helper also shows no command.
2. The screen prints the raw rationale, not the plain line.
3. A refusal is not recorded and the agent is not told. The permit stays pending until it expires. The chat path does record a deny.
4. ZWNJ (the Persian half-space) is printed as the escape text `\u200c` (`permits.printable`, lines 389-401). That makes Persian commands hard to read.

### 5.2 What does the friction buy?

[INFERENCE] The six-character code is a speed bump. It does not prove the person read the command, because the person can copy it. Its real job is different: the approval comes from a human at a terminal, outside the agent's tools (repo docs 05 T1, T2, `approval_reference = tty:*`). The signed helper adds a second identity. The chat click and the dialog click add "a human in the chat" but are weaker: a mod in the host can answer for the human (repo docs 12 risks, [VERIFIED] residual). None of the channels proves reading. [VERIFIED, repo docs 12 section 1] The owner gave 18 terminal approvals in one afternoon and then stopped. So more friction on the permit path is the wrong direction.

### 5.3 Options and the recommendation

- Option A: make the code a "reading test" (type the last word or the branch). Rejected: more friction, no evidence it raises reading, and it can be copied.
- Option B: a countdown before the code is accepted. Rejected: annoyance without proof.
- Option C (recommended): keep the six-character code. Fix the four defects. Reduce how often the permit path is needed (section 4).

Fixes, all [RECOMMENDATION], all need human approval because they touch the approval path:

1. Make `confirm_code` and `code_of` the same value, or print the correct text, and show the command in the helper. (Tier 1.)
2. Use the same builder as the chat, so the plain first lines show. (Tier 1 for the plain line; tier 3 for the full builder.)
3. Record a refusal. A typed `n` must not silently become a human Deny, because a Deny feeds the hold keys, the 3-Deny cap (`DENY_CAP`) and the learning loop, and a mistyped key or a "not now" would then block a call kind. Two options. Option A (default): `n` means "refuse now": the permit is revoked, the agent is told, the event is journaled as a refusal, and it does not count as a Deny. An explicit word such as `deny` records a human Deny. Option B: keep today's behaviour (the permit stays pending, so the human can come back) and only tell the agent. Today "anything else" leaves the permit pending [VERIFIED].
4. Escape ZWNJ and ZWJ like every other format character in commands (section 6.2). Persian prose does not go through the command path.

## 6. Showing a command faithfully

### 6.1 The facts

- [VERIFIED, R6 section 1.5 item 1, A7 section 3] With a real `policy.assess` and `question_for` on PR 44, U+202E and U+2067 pass into the chat question, and U+202E into the dialog text, unchanged (synthetic check on main). The terminal screen escapes them (`permits.printable`, tested in `test_permits_lifecycle.py:242`). No test covers the chat path.
- [VERIFIED, R6 item 2] `printable()` also escapes U+200C and the isolates, so it must never be used on prose.
- [VERIFIED, R6 section 1.5 item 3] Clipping keeps 450 head and 150 tail characters. The middle can hold the risky part.
- [VERIFIED, R6 section 1.3] A payload was hidden behind a long run of whitespace (Tracebit). Hidden Unicode in rule files survived PR review (Pillar).
- [VERIFIED, R6 section 1.5 item 5] A trailing comment after a bare `sh` or `bash` hides fetch-and-run (R-22) from the classifier on main and PR 44. This is a classifier bug, not a display bug, but it limits this section: the plain line is built from the classifier, so a parser gap becomes a false plain line. The plain line cannot be better than the parser.

### 6.2 The safe-display function (proposal, tier 1)

One function, used by the chat, the dialog, the permit screen, `jade review`, `jade knows` and `jade permit list`. Rules:

1. Escape C0 controls (except newline and tab inside a multi-line command), DEL, C1 and ESC, as visible tokens such as `<U+001B>`.
2. Escape the bidi controls U+202A to U+202E and U+2066 to U+2069, and also U+200E, U+200F, U+061C.
3. Escape zero-width space U+200B, U+2060, U+FEFF, U+200C, U+200D and every other format character (category Cf). [RECOMMENDATION] Do not keep ZWNJ or ZWJ in commands, even between Arabic-script letters. [INFERENCE] A kept invisible mark is exactly the hidden-Unicode channel R6 cites, and keeping it needs new script-aware code in a safety path. No evidence shows how often Persian commands contain ZWNJ. R6 only says `printable()` over-escapes U+200C, it says nothing about joining rules. Revisit only if the owner shows real Persian commands that are unreadable with a visible token. Default: no relaxation.
4. Show a run of 8 or more whitespace characters as `<8 spaces>`.
5. Sanitise first, wrap second. If a value is wrapped in an isolate before it is escaped, a PDI inside the value can break out of the wrap (R6).
6. In a host that renders Markdown, put the command in a fence longer than any run of backticks in the command. [UNRESOLVED] Whether the question box renders Markdown.

Tests: a command with U+202E, with ESC, with a long space run, with a PDI, and with ZWNJ. Each checks that no raw control, bidi or format character reaches the output of any channel.

[UNRESOLVED] Confusable letters (a Cyrillic letter inside a host name). R6 did not read UTR 36. Not covered here.

### 6.3 What to do when the command is clipped

The hole: a click grants on a summary while the risky middle is not shown. Options:

- Option A: show the gated segment in full, up to a cap, then head and tail of the rest. The gated segments are known from `a.seg_keys`, and PR 43 already names them.
- Option B: if the gated segment cannot be shown in full, remove Allow from the chat and send the call to the terminal permit, which shows everything.
- Option C: only note "N characters not shown" (today).

[RECOMMENDATION] A is the default. B is built only if a read-only ledger query shows that clipped asks with a gated segment inside the clipped part are common. If that count is near zero, B is not built. Reason: B pushes the call to the permit path the owner abandoned (5.2), and the cap number is a guess until the real command lengths are read (section 13, item 8). The query is read-only and should run first. Option C is the current hole. A needs human approval (it changes what is shown at a decision). B needs it too (it removes a chat option).

### 6.4 One-line commands: when joining is safe

This is advice to the agent. [VERIFIED, PR 42] The skill text "send one risky action per call" exists but is unmerged. The gate can only present, not rewrite, the agent's command. An agent may join commands (`a && b && c`) only if: the steps are a dependent chain, not `;`; every step is low risk [VERIFIED, T27 in repo docs 05: memory keys a plain shell call by its rule and its sorted gated segments, so low steps do not widen what is remembered]; a failing step stays visible (no `|| true`, no hidden exit code); and it does not mix a read with a destroy or a build with a publish. [INFERENCE] The ledger opens one decision per call (`askchat.run_decision`), so a failure in the middle is one record. Never join unrelated commands, destructive or irreversible ones, money, release, or two different gated kinds. [VERIFIED, A7 section 2.1] The owner's own case joined `cd /tmp && git status && gh pr merge 940 --squash --delete-branch && ls`: one click would cover the merge and its branch delete together with the harmless parts.

Proposal: present each gated segment as its own numbered item in the first block. Enforcement is optional. Option (a, default): present only. Option (b): refuse a call with two different gated kinds and ask for a split (more questions, each clearer). Decide after section 9 data shows whether joined calls are common.

## 7. Language, Persian and right-to-left

### 7.1 What exists

- [VERIFIED, A7 section 3 and section 1 item 1] No language setting, no catalog and no bidi handling exists for human text, on main or on the unmerged branches. All human text is English in code strings. The only language code is `util.py:294`, a tag (`fa`, `en`, `mixed`, `unknown`) used by evals.
- [VERIFIED, R6 section 2.3] The host `language` setting only sets the language of the model's replies. It does not translate text a hook writes. The `Elicitation` input has no locale. [UNRESOLVED] The `PreToolUse` input was not checked for a language field.
- [VERIFIED, R6, issue 45652] Persian text is reversed in some Claude Code output (VS Code terminal on Windows, CLI 2.1.96). The issue was closed as a duplicate with no workaround. Reports for macOS Terminal come from a community mirror only: [INFERENCE].
- [VERIFIED, R6, UAX 9 and W3C] Isolates (LRI, RLI, FSI, PDI) are neutral toward their surroundings. Overrides "should be avoided". For text of unknown direction use FSI. A phrase of the opposite direction that starts or ends with digits or punctuation can display wrongly, so isolate it tightly.

### 7.2 Where the text comes from: fixed templates, never a model

[VERIFIED, A2, adapter and parser] Jev returns only typed probabilities, so Jev cannot write a sentence. The risk is the other model: the agent, and any model asked to explain. Decision (needs human approval, because it closes a feature some will ask for): every human-facing sentence comes from fixed templates written and reviewed by people. Never from the agent, Jev or a model at run time.

Reasons: (1) [VERIFIED, repo docs 05 T25, `askchat.py`] the hook replaces the agent's question so the agent's words never reach the human; a model-written question would put them back. (2) [VERIFIED, R6, OWASP ASI09] "plain-language risk summary (not model-generated rationales)". (3) [VERIFIED, Checkmarx LITL] a model-written dialog was the way to hide a command. (4) [INFERENCE] A translated safety fact can be wrong in a way no test catches: "you cannot undo it" written as "you can undo it" leads to Allow.

The agent's own stated reason is NOT shown in the question (it is the Lies-in-the-Loop door; section 12). Machine translation may draft a text offline, and a native speaker reviews it before it reaches the owner. [VERIFIED, R1 sections 3.9 and 7 item 7] An `Elicitation` hook and a JADE mod for human text are not built. R1's reasons: mods run in-process, are skipped after three worker crashes and by `--safe-mode`, so they cannot carry enforcement; and no Elicitation need was seen.

### 7.3 Language setting, labels and layout (tier 3, later)

- `human_lang` would set the language of text JADE writes (`en` default, later `fa`). Explicit only; no guess from the host `language` string (R6: it cannot do this job). Setting it must be a human-only verb like `jade level`: [INFERENCE] an agent that could set it could switch the human into a language they read badly at the moment of an approval. [VERIFIED, A4 section "config"] Only `level` has a provenance check today, and `gate_mode` has none. The new verb goes on the SI-09 list in `policies/safety.yaml`. Needs human approval.
- A missing message falls back to English, per message. A draft language shows "This text is a draft translation. English is the reference." and the English first line too.
- Never translated: the command, paths, branch names, ids, `[jade:ask_<id>]`, rule ids, host names. The labels `Allow` and `Deny` stay English: [VERIFIED, `askchat.settle`] the answer is accepted only when the label equals `Allow` or `Deny` exactly, and repo docs 05 T25 relies on fixed labels. The option descriptions carry the meaning. The risk level word is shown twice, the local word and the code word.
- Layout [RECOMMENDATION, on top of the UAX 9 and W3C facts]: one sentence per line; the command, path and ids each on their own ASCII line after a label, wrapped in LRI and PDI after sanitising; inserted values inside a Persian sentence in FSI and PDI; the command escaper never runs on prose; digits in commands stay ASCII. In a host with no bidi algorithm ([INFERENCE]: many terminals) the safety facts must not depend on reading order: the level word and the labels are Latin. [UNRESOLVED] The macOS notification and the osascript dialog may not honour isolates.

### 7.4 The mechanism: no message-id package now

[RECOMMENDATION] Now: one question builder inside the existing `askplain.py` (English, 20-word cap per sentence, tested) plus the safe-display function. Do not add a `jade/text/` package with `say(id, **values)` and about 100 ids. No second language is requested, so the need is not shown. [INFERENCE] When one is requested, a Python dictionary per language keyed by message id is the smallest mechanism (no dependency). `gettext` and ICU MessageFormat 2 are not needed for about 100 fixed sentences (R6: CLDR 47 made MF2 Stable on 2025-03-13; the Python runtime was not checked). Tests then: same ids and placeholders in every language, at most 20 words per English sentence, no format character except renderer-added isolates, golden renders with a Persian folder name and a U+202E command, and a manual host probe per release.

The Persian draft mock of the first version is removed: it was AI-written safety text with unchecked terms and no native review. When the section 13 probe runs, use a throwaway Persian fixture there, and publish nothing.

## 8. Plain mode: the owner's own rule

The owner's rule [VERIFIED, global `CLAUDE.md`]: simple words, short sentences, one idea each, say what will happen and what the risk is. [VERIFIED, A7 section 1 item 2] JADE does not carry this rule today. On PR 42 the risk sentences follow it, at most 20 words, tested. `jade review` uses words such as "silent share", "plain-high" and "95% bound" (A7 section 5).

- Option A: plain as the standard of the human layer, no switch. The first block of every question, permit screen, `jade review` and `jade knows` is plain and tested. A second block holds the technical words (rule ids, statistics).
- Option B: a switch `human_style: plain | standard`. Doubles the test surface.
- Option C: leave it to the agent through `CLAUDE.md`. JADE does not control it.

[RECOMMENDATION] A, plus a short text in the `jade claude-md` section and `skills/jade/SKILL.md` for the agent (A7 recommendation 9). How the agent talks to the owner in chat stays advisory.

"Plain" for the repository: at most 20 words per sentence; active voice naming the actor ("JADE", "the agent", "you"); what will happen, then the risk, then the way back; a fixed word list ("standing answer", "ask", "call"); exact numbers, names, paths and commands; no "silent share", "plain-high", "fingerprint" or "band" in a first block. A test can count words per sentence in the templates. Mock for `jade review`: "JADE asked you 12 times. It let 9 calls run without asking. You marked 2 of them right and none wrong." [VERIFIED, A6] The caveat for the 95 percent bound stays in the second block.

## 9. Measuring human friction without watching the person

### 9.1 Principles

1. Local only. All data is in the owner's ledger, on his Mac. Nothing leaves the machine unless the owner runs an export.
2. Existing events only. [VERIFIED, `jade/ledger.py` and `jade/askchat.py`, `jade/askdialog.py` on main] The `asks` table has `created_ts`, `answered_ts`, `status`, `rule_id`, `level`, `tool`, `source`. Events: `ask_shown` (chat only, `askchat.py:206`), `ask_allowed`, `ask_denied`, `ask_gave_up` (dialog, `askdialog.py:143,152`), and others such as `ask_opened`, `ask_consumed`, `ask_nudged`. No keystroke, mouse, screen, camera or reading time is captured. Never add such capture.
3. Per rule and per question type, not per person.
4. A metric is a signal, never a score of the owner. No metric is used to nudge or lock the person.
5. No planted fake questions. They deceive the person and pollute the ledger.
6. [RECOMMENDATION] A faster answer or a higher allow rate is NOT a success sign. Fewer asks are not better unless correctness is kept (the product rule).

### 9.2 Counts the ledger can give (channel health, not quality)

| Metric | Definition | Limits |
|---|---|---|
| Asks per session and per 100 gated calls | By rule id (and by reason id if built) | [UNRESOLVED] No "task" id was found, so no per-task count. |
| Time to answer, chat | `answered_ts` minus the `ask_shown` event time | The dialog writes no `ask_shown`. |
| Time to answer, dialog | `answered_ts` minus `created_ts` (the row is inserted when the dialog shows, `askdialog._insert`) | Channel health only. |
| Expired and gave-up asks | Status `EXPIRED` and `GAVE_UP`; sessions where asking paused | Shows the person was away or the notification failed. |
| Re-ask rate | Same call asked again after Deny or expiry | Shows a question that did not stop the agent. |
| Clipped asks with a gated segment in the clipped part | Read-only query on `question_json` and the command | Decides whether 6.3 option B is built. |

Deny rate and "wrong approvals by marks" are weak signals now: [VERIFIED, A6] 100 allow and 0 deny cannot tell a good question from a rubber stamp, and `review mark` is unsampled (the Feedback and Wrong-rate bound rows of the A6 tables), so the wrong rate is biased low. The earlier "possible approval without reading" metric is removed: it is a latency proxy with no floor and no data, and it scores the owner. No target numbers are set. After two weeks of data at a level above `off`, the owner sets any target.

### 9.3 An offline misread test: does the new text help?

Counts cannot show whether a question is better. [RECOMMENDATION, tier 2] A test the owner can run, read-only:

1. Take 20 past asks from the ledger (read only), a mix of rules.
2. Show each call once with the old question text and once with the new text, in separate sessions, in random order.
3. Ask the owner to say, in his own words, what the call will do and whether it can be undone.
4. Compare with the true effect of the rule (from `policies/risk.yaml` and the command). Count misreads, not seconds.

The new text is better only if it has fewer misreads. If it has not, it does not ship, whatever it looks like. This test also checks the "Undo" line and the plain first line (2.3). Execution success ("the tests passed") is never used as the answer: it is not decision correctness. A weekly random review sample inside `jade review` is a tier 3 idea; it touches the learning loop, so it belongs with the calibration section.

### 9.4 Reading the signals

An all-allow rule with many asks means the question is not informative or the rule is too strict: propose a standing scope or a rule change, which the owner approves because it lowers asking. A high deny rate means the agent keeps trying something the owner does not want: fix the agent's guidance, not the question. A high expired rate is a channel problem or an absent person: fix the channel, not the person. Clipped gated asks point to the hole in 6.3.

## 10. Mock and flows

### 10.1 Mock: the English question (proposal, wording not final)

Built from real strings where they exist: the R-10 sentence is in `RISK_TEXT` (PR 42); the option text is in `askchat.py` (main). The `--delete-branch` clause, the "why you", "undo" and "where" lines are NEW. A mock only. Bidi controls are shown as visible tokens. There is no history line and no suggestion (2.7).

```
Header chip:  JADE: high risk

Merge pull request 940 and delete its branch.
This changes something outside this Mac, such as a merge, a release,
a publish or a push.
Why you: this kind of call is high risk and you have no standing answer for it.
Undo: JADE cannot undo it.
Where: folder iro (/Users/me/work/iro). Asked by: main session.
Also in this call: 2 other commands that JADE rates as low risk.

Command:
<LRI>cd /tmp && git status && gh pr merge 940 --squash --delete-branch && ls<PDI>

Ref: [jade:ask_a1b2c3d4e5f6]

Options
  Allow  - Run this one call once. It is not remembered.
           If you do not answer, the call stays blocked.
  Deny   - Do not run it. The agent is told you said no.
           It is not asked again for <chat Deny window>.
```

The "Undo" sentence must come from a table of classes reviewed by a person; the default is the conservative line. "You can undo it" appears only for classes where this is true by construction. The numbers in the option text come from the channel constants (2.5).

### 10.2 Flow: a high-risk call in the chat

```
gate: rated high, no memory, level above off, chat applies
  -> ask opened; call blocked (exit 2): "call AskUserQuestion with this token"
  -> hook replaces the agent's question with the gate's own   [VERIFIED rewrite]
  -> one macOS notification per new ask                        [VERIFIED]
  -> Allow   : single-use grant, 10 min, this session          [VERIFIED settle]
     Deny    : refused; not asked again 10 min; 3rd Deny ends asking
     nothing : expires at 600 s, call blocked; asking pauses when 30 asks
               expired in the last 3,600 s, until they age out or you answer
  -> agent retries the identical call -> grant consumed -> runs
```

### 10.3 Flow: the terminal permit

```
jade permit approve <id>
  -> plain first lines, rule, folder, plan hash, whole command, escaped
  -> code typed      : approved once, 30 minutes
     n               : permit revoked, refusal journaled (no Deny count),
                       agent told                                  [proposal]
     anything else   : not approved, permit stays pending          [VERIFIED today]
```

## 11. Proposals that need human approval

Each one changes what a human sees at a safety decision or how a decision is recorded. None ships without the owner's yes. The tier says when. The test says how we know it works.

| # | Proposal | Tier | Recommended default | Test |
|---|---|---|---|---|
| 1 | Escape bidi, control and format characters in chat and dialog text (6.2). | 1 | Yes | No raw character in any channel output |
| 2 | Plain lines list every widening flag; unreadable calls say so (2.3). | 1 | Yes | One test per segment family |
| 3 | Fix the two-code mismatch; show the command in the signing helper (5.3 item 1). | 1 | Yes | Code shown equals code accepted |
| 4 | Record a refusal on the permit screen: `n` = refusal, no Deny count; the word `deny` = Deny (5.3 item 3). | 1 | Yes, Option A | Refusal journaled; hold keys unchanged |
| 5 | State "no answer" in the chat question, numbers read from channel constants (2.5). | 1 | Yes | One test per channel reads the constants |
| 6 | Keep the agent's non-token questions in a bundled `AskUserQuestion` (3.2). SUPERSEDED (master plan check): d13 X-15 and d07 section 4.1 reject this (Fix A). The choice is Fix B now (words), Fix C next release (refuse a batch with a JADE token). A yes-to-all must not approve a gated call. | 1 | No (see d13 LD-16) | Two-question payload is refused, no loop |
| 7 | No recommendation, no history-of-allows line; facts only (2.7). | 3 | Yes | Builder test: no "suggest" text |
| 8 | Show the gated segment in full, up to a cap (6.3 A). | 2 | Yes | Ledger query first; golden render |
| 9 | Remove chat Allow when the gated segment cannot be shown (6.3 B). | 2 | No, until the ledger count shows need | Ledger count |
| 10 | Keep ZWNJ and ZWJ in commands (6.2 rule 3). | n/a | No | Revisit only with real owner commands |
| 11 | `human_lang` as a human-only setting with provenance; verb on SI-09 (7.3). | 3 | Only when built | Provenance test |
| 12 | Fixed templates only, never model-written; labels stay `Allow` and `Deny` (7.2, 7.3). | 3 | Yes | Template tests |
| 13 | Option order: whether Deny comes first for high risk. | 2 | Probe first (section 13) | Probe result |
| 14 | Weekly review sample in `jade review` (9.3). | 3 | Not now | n/a |

### 11.1 Migration and rollback, per proposal

[RECOMMENDATION] The roadmap cards V6-09 and V6-26 (d12) hold the same text in their Migration and Rollback lines. [INFERENCE] These are display changes. They store nothing new, except the refusal event (proposal 4) and the `human_lang` key (proposal 11).

| Proposals | Migration | Rollback |
|---|---|---|
| 1, 8, 9 (safe display, full gated segment, clipped-command rule) | None. The escape runs when text is shown. Text already stored in the ledger is not changed here (purge scrubbing is a separate task, V6-11) | Revert the one shared function; text goes back to raw. Card V6-09 |
| 2, 3, 5 (widening flags, permit code, "no answer" line) | None. Numbers are read from the channel constants | Revert one channel at a time. Card V6-26 |
| 4 (refusal on the permit screen) | A new ledger event kind. Old code ignores it. Old rows are not touched | Stop writing the event. The hold keys do not change |
| 6 (keep other questions in a batch) | Not built. Superseded (d13 X-15) | n/a |
| 7 (facts only, no advice) | None | Revert the builder text |
| 11 (`human_lang` setting) | Only when built. An absent key reads `en`. New SI-09 verb; an agent that tried to set it now blocks | Delete the key; English returns. No undo for a Persian text already shown to the owner |
| 12 (fixed templates only) | None. No model text exists today | Delete the table |
| 10, 13, 14 (not built or probe first) | None | n/a |

**Questions only the owner can answer.**

- Which language should JADE's human text use: plain English only, Persian, or both? Default: plain English now; Persian after a native review and the probe.
- Who can review a Persian text? Default: the owner, or one named person. Text with no reviewer must not ship.
- Is a plain `git push origin main` inside a joined call allowed to show the worst-case push line, or should it say "JADE could not read this call fully"? Default: show both.
- Do you accept a question with no recommendation at all? Default: yes (2.7).

## 12. Things that should NOT be built

- Batching of asks now (section 3), model-written questions or rationales, run-time model translation, and an agent-written reason in the question, even in a marked block (7.2).
- Jev's probability or advice shown to the human before calibration exists; any "suggest Allow", "suggest Deny" or "use the terminal" line; any history line that shows allow counts (2.7).
- A permit code that is a "reading test", a countdown, or any extra friction on the permit path without data (5.3).
- Translated `Allow` and `Deny` labels; auto-detecting the human language from the host `language` string (7.3).
- A `jade/text/` message-id package, `gettext` or ICU MessageFormat 2 before a second language is requested; a published Persian text before a native review and the probe (7.4).
- An `Elicitation` hook or a JADE mod for human text (7.2).
- Planted fake questions, screen or keystroke capture, per-person timing dashboards, a "possible approval without reading" metric (section 9).
- A second text source for each channel. One builder only.

## 13. Unresolved items and the one probe that closes most of them

Unresolved:

1. How the chat box draws a mixed Persian and English question, bidi isolates, Markdown fences and option descriptions (R6, A7).
2. How the osascript dialog and the macOS notification draw the same text.
3. Which option the host pre-selects, and whether order nudges the answer.
4. The `AskUserQuestion` limit of 4 questions: a flow-skill rule (A5), not a verified host limit.
5. The real shape of the response when `askUserQuestionTimeout` fires.
6. Whether `agent_type` is a useful readable name for the human, and what the main-session `PreToolUse` event carries (the dialog event carries `agent_id` and `agent_type`, verified), and whether the input has a language field.
7. Whether a settings `ask` rule and a `PermissionRequest` answer work in `auto` (R1 cells never run).
8. Real command lengths in the ledger, to decide 6.3 (read-only query, not run in this study).
9. Whether the ask row can carry the "why you" reason today (2.6).

The probe (owner step, one session, one hour). Use a throwaway folder and the style of `evals/probes/hook_ask/`. Show (a) the English mock, (b) a throwaway Persian fixture, (c) a command with a bidi override and a long space run, (d) a one-sentence-per-line layout, in the chat question, the dialog and the notification. Record screenshots and the host version. Fill the table: renders correctly, reversed, or hides characters, for each channel. Record the pre-selected option and whether option descriptions show. [RECOMMENDATION] Do this before any Persian text ships, and again after a host upgrade.
