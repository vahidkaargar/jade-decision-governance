# Clear questions and bigger scopes (design)

Status: **approved by the owner on 2026-11-14. No code is changed yet; the build plan is next.** Date: 2026-11-14. Written in simple English on purpose (the owner's English is not their first language).

## 1. Why

JADE was built so the owner is asked less. Today a question in the chat is hard to read and hard to act on:

- The risky part is hidden. A call can join several commands with `;`. In the example that started this, only `gh pr merge 940 --squash --delete-branch` was risky. Three other commands only read. The owner had to find the risky part in a long line.
- The words are hard (for example "refspec", "connected-app action").
- There are only two choices: Allow once, or Deny. The answer is "not remembered", so the same question comes back.
- JADE never suggests a bigger scope, such as `gh pr merge *` in one project.
- `jade knows edit` fills its template with one raw, long command per answer. The owner wants short sentences by kind and scope, like the two they wrote by hand (an ssh host; GitHub and GitLab projects).

Success, one month after release: the owner sees one clear line for what will happen, picks a scope in a few seconds, and sees fewer repeat questions for safe work. Nothing risky becomes silent without the owner choosing it.

## 2. Goals, non-goals, anti-goals

**Goals**
- A question the owner can understand in five seconds: what, risk, size of the answer.
- Scope choices inside the question flow, with what each one covers and how to undo it.
- A `jade knows edit` template that offers sentences by kind and scope.

**Non-goals**
- No change to which calls are human-only (delete that cannot be undone, money, public release, live JADE wiring).
- No change to the 50-answer line for Jev.
- Not the repo skill for duplication and issue watching. That idea is separate and still open.

**Anti-goals (a failure even if it works)**
- A click in the chat must not create standing power. An agent that drives the mouse could press it. Owner decision 7 stays: only `jade review confirm` in a human terminal makes an answer standing.
- A bigger scope must never cover a delete, a force push, a tag, or a release. A merge scope is allowed only as the owner decided on 2026-11-14 (section 7): `gh pr merge *` in one named project, and never with a sensitive option.
- The question text must never come from the agent. Today it comes only from the gate. That stays.

## 3. What exists today (checked in the code)

- `jade/askchat.py` `question_for` builds the text: rule line, tool, the command (clipped to head 450 and tail 150 characters), content, then the two options Allow and Deny.
- `jade/askdialog.py` builds the same kind of text for the macOS dialog.
- `jade/memory.py` already has an exact key and a class key. A class key is the rule plus the sorted gated segments, with the PR number dropped. These are exact only: R-14, M-05, M-07, inline programs, unreadable calls, any push or merge, merges into the default branch, and every write. These are never remembered: R-21, R-22, and the human-only classes.
- `jade review confirm` already prints either "would be standing for this kind of call" or "for this exact call only" per answer.
- So the "kind of call" exists. It is just never shown when the question is asked, and the owner learns about it only later at confirm.

## 4. Approaches considered

- **A. JADE checks the scope itself.** Saves a narrow pattern rule (tool, command family, project folder). Works at once and is exact. It is a new matcher, so it adds attack surface, and merges need an explicit exception.
- **B. Sentences for Jev only.** Safest to build. Does nothing before 50 owner answers, and a model can be wrong.
- **C. Both, starting small.** First the clear question and the better template (no risk). Then pattern scopes for a short safe list. Merges and deletes keep asking.

**Picked by the owner: C, starting small.** Reason: the first two parts carry no risk and fix the daily pain; the matcher comes later and stays narrow.

## 5. Design (C, in three steps)

### Step 1. A question that is easy to read (no new power)

New layout of the chat question and the dialog. JADE writes all of it from the gate's own data.

1. **Plain first line:** what will happen. Built from the extracted actions, not from the agent. Example: "Merge pull request 940 in the iro project, and delete its branch."
2. **Risk line:** one short sentence per rule, from a table of plain texts (one per rule id). Example for R-10: "A merge or release changes shared work. It is hard to undo."
3. **Also in this call:** the other segments, grouped. "Also in this call: 3 read-only commands (safe)." If a segment is risky it is listed first, not last.
4. **The full command:** last, clipped as today, with "N characters not shown" when cut.
5. **Options, with sizes:** "Allow once" and "Deny" stay. The scope choices from step 2 are added once they exist.

Also in step 1: one rule for agents. Send one risky action per call, so the question and any scope match one thing. This goes into `skills/jade/SKILL.md` and `docs/claude-md-jev-section.md` as a plain rule, and the gate message for a joined call says it.

### Step 2. Scope choices that respect decision 7

The chat shows the choice; a terminal confirms it. Chat options:

- **Allow once** (as today).
- **Allow once, and suggest "this kind of call" for later.** The click records a *proposal* (a `hint` row with the chosen scope). It decides nothing now. It appears in `jade review confirm`, where the owner sees the scope in plain words and types `yes`.
- **Deny.**

The scope text is built only from facts the gate extracted, for example `gh pr merge *` in `/Users/mobogift/Documents/iro`. Each scope says: what it covers, how long (30 days after last use, never more than 180), and how to undo (`jade knows rm ID`).

`jade review confirm` gets a scope ladder per answer: this exact call; this kind of call in this project; this kind of call anywhere. Only the rungs that the safety table allows are shown. A rung that is not allowed says why in one line.

**Safety table (what a scope may cover).** Kept in one place and tested.

| Call | Widest scope allowed |
|---|---|
| Read-only `gh` / `git` / `glab` | kind, any project |
| Writes to issues, comments, labels (not delete) | kind, one project |
| `gh pr merge` (owner decision, section 7) | kind, one named project, only without a sensitive option (list in section 7); with one, exact call only or ask |
| Anything with delete, force, tag, release | never; always asks |
| Unreadable or inline programs | exact only (as today) |

### Step 3. A better `jade knows edit` template

Instead of one commented raw command per answer, group by kind and project and write short sentences, all commented out with a one-line risk note. Two examples go first as models, the same two sentences the owner wrote. The owner removes the `#` of the ones they want. Reminder in the file: lines with `#` are ignored; a sentence is read by Jev only after 50 owner answers.

## 6. Constraints

- Tests first, one change at a time; the full gate must stay green on this Mac and on Linux CI.
- No question text from the agent. No secret in any text. The owner's secrets rule stays.
- Every plain-text table (risk lines, scope lines) is in simple English, short sentences.
- Release: this is a minor release (0.7.0). The tag stays the owner's.

## 7. Decisions

**Decided by the owner (2026-11-14):**

1. **Merges.** `gh pr merge *` in one named project may be a "kind of call" scope. It must never cover sensitive commands such as delete. Excluded options, so a call with any of them is not covered and asks as today: `--delete-branch` / `-d`, `--admin`, `--auto` (auto-merge), and any other option that deletes, forces or bypasses a check. The scope names one project folder; a merge in another project is not covered. These are my proposed words for "sensitive" and the builder lists them in one place with a test; the owner can add to the list.
   - This changes today's rule in `jade/memory.py` (a merge is exact only). The change is narrow: only the merge segment, only with the named project, only without an excluded option.
   - What stays: the `Never push to the main branch` sentence in the preferences, the human-only classes, and the rule that a release or tag always asks.

2. **Where the "suggest" click lives.** In the chat question (decided 2026-11-14). The click records a proposal only; the scope becomes standing only at `jade review confirm` in a terminal.

## 8. What is left to the builder

Exact wording of each risk line; the extraction of the plain first line for MCP tools; layout limits of the macOS dialog; how the hint row stores the proposed scope (new column or inside `detail_json`); test cases for the safety table.

## 9. Not covered here

- The repo skill for duplication, issue watching, self-improvement and suggested challenges. Next brainstorm.
- Pattern scopes checked by JADE for non-merge families (the matcher in approach A). Start only after steps 1 to 3 are in use.
