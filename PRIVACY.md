# What JADE sends, and what it keeps

This page says what JADE sends off your machine and what it stores on it. It says nothing about what the provider does with the data after it arrives: JADE does not control that, and the provider's own terms are the only source. Read them before you turn a level on.

A test checks each line below against the code (`tests/test_front_page.py`). If the code changes, the test fails until this page is changed.

Short version:

- JADE has no analytics, no crash reports and no phone-home.
- JADE sends text to one provider: `https://openrouter.ai/api/alpha/decisions`. The address is fixed in the code. No setting and no environment variable changes it.
- The hook sends a call there only when the gate is in enforce mode, a level is set (`low`, `medium` or `high`), a key exists and the `jev` setting is on. The `jev` setting is on by default at `low` and `medium` and off by default at every other level (V6-28: `jade jev on|off`, human terminal only). So at level `high`, nothing is sent unless you ran `jade jev on`.
- Ordinary source code and commands are not changed by redaction. Redaction hides secret shapes and email addresses. It does not hide private code.
- A job on a timer can send text with no one at the keyboard: the daily job `jade maintain`, when it is installed and a key exists. It sends only two calls with a fixed text (see "Other commands that call the provider").
- `jade upgrade` talks to GitHub. Nothing else does.

## When the hook sends a call

"The hook" is the PreToolUse hook that Claude Code runs before each tool call. "Sent" means: a plain high-risk call that passes every condition in the next list. The table is about the three settings that decide whether the hook may send at all.

| Gate mode | Level | Key | From the hook |
|---|---|---|---|
| off or advisory | any | any | not sent |
| enforce | unset, off | any | not sent |
| enforce | low, medium | yes | sent |
| enforce | high | yes | not sent |
| enforce | low, medium, high | no | not sent |

The `high` row is for the default: at level `high` the `jev` setting is off, so the hook sends nothing. If you ran `jade jev on`, the hook sends there too, and the answer is only recorded.

A key is `OPENROUTER_API_KEY` in the macOS Keychain or in the environment of the shell that starts Claude Code. With no key, the provider call stops before any request is made.

A call is sent only when all of these are true:

1. The gate is in enforce mode, a level is set, a key exists, the `jev` setting is on, and the hook event carries a session id.
2. The call is a plain high-risk call. Critical calls, human-only calls and blocked calls are never sent (next section).
3. You wrote a preferences text (`preferences.md`) or you confirmed at least one allow answer for the same rule. A call with neither is your question as before.
4. You did not deny this call or a call of its kind before, you did not mark it wrong (`jade review mark`), and Jev was not already asked about it twice in this session within the last hour.
5. The question the hook asks is usable and the hook has time left. The call to the provider waits at most 3 seconds, and never past the hook's own limit of 6 seconds. One try, no retry.
6. Redaction did not change the text. If it would hide something, the call is not sent.

Shadow does not stop the sending. At level `high` the answer is not used. At level `low` or `medium` it is not used until the deny gate is met: you have denied 30 kinds of call (from at least 3 rules), Jev has been asked 100 shadow questions over at least 7 days, and Jev never said yes to a call you denied or marked wrong (`jade review` shows the counts). In shadow the call is still sent, asked and recorded.

## What is in the request

The request is one JSON body with three fields:

- `model`: the model id, `typesafe/jev-1.14` unless you set another in your config or with `JEV_MODEL`.
- `state`: what Jev is shown about your call (the next list).
- `questions`: the text of one fixed question (`q.human_proxy.would_allow`) with its two answer criteria. It is the same for every call.

The headers are `Authorization: Bearer <your key>` and `X-Title: jade-decision-governance`. The provider also sees the network address your request comes from. If `https_proxy` is set in the environment, the request goes through that proxy over a TLS tunnel.

What `state` holds from the hook:

- `action`: the command, or the path of a write, with secret shapes and email addresses replaced; the hook sends at most 4 KiB of the command or of the written content: a longer call is not sent, never cut.
- `kind`: the rule id and its short reason, for example `R-11: Destroys local history or files`.
- `preferences`: your `preferences.md` text without comment lines, at most 4 KiB.
- `past_answers`: up to 8 past answers of each kind for the same rule. An allow you confirmed is sent with its label (up to 120 characters, the text you read when you confirmed it). A deny is sent as the rule, the word deny and its age in days, with no text.
- `content_excerpt`: for a write or a tool call that carries content, the whole content (never a part).
- `fingerprints`: for a `git push`, the absolute repository path (it can name a customer or a project) and its current commit id. For a write, a hash of the content.

Your level is not part of it. Your working folder is not a field of its own: it is sent only inside the repository path of a `git push`.

Redaction runs on `state` and on the question before it leaves. It replaces secret shapes (API keys, tokens, private keys, passwords in a URL or a flag, authorization headers) and email addresses, except the addresses you list as `owner_emails` in your config. The state is cut at 32 KiB. If a secret-shaped string is still there after redaction, the call is refused and nothing is sent. `jade ask --dry-run` shows exactly what a call from `jade ask` would send.

Redaction is for secret shapes, not for private code. A function body, a file path, the repository path of a push, a customer name or an internal host name in a command goes to the provider as it is.

## What is never sent

These calls are always your question. The hook does not send them, at any level:

- a human-only class: live JADE wiring, a delete that cannot be undone, money, a public release;
- a critical call, and a blocked call;
- fetch-and-exec, such as `curl ... | sh`, and a write to a file an agent reads its instructions from (`CLAUDE.md`, `AGENTS.md`, a skill);
- a merge;
- a call the gate could not read in full;
- a secret-shaped value;
- an email address, and any other text that redaction changes;
- a program over 4 KiB, a write over 4 KiB, or more than one inline program in one call;
- a call you denied, or marked wrong, before;
- egress (an upload of local data) at any level; it is never sent;
- a call when you wrote no preferences and no confirmed answer exists for its rule.

## Other commands that call the provider

You run most of these yourself. One does not need you: the daily job. Each one uses the same address, key and redaction.

- `jev` and `jade ask`: the text you give them (a claim, a state file, evidence). Give raw evidence, and know that it leaves your machine.
- `jade check` without `--offline`, and `jade maintain` unless it is told `--offline`: two calls with a fixed text about a made-up `make test` run. Nothing from your machine is in them.
- The daily job: `jade setup` can install a job that runs `jade maintain` once a day (a LaunchAgent on macOS; on Linux the cron line or systemd timer that `jade maintain-schedule` prints, if you install it). It does not pass `--offline`. When a key exists it makes the two fixed-text calls above on its own, with no one at the keyboard. With no key it makes no request. To stop this, remove the job (`docs/09-install-and-upgrade.md` section 7) or run it with `--offline`.
- `jade improve shadow` and `jade improve protected`: they send the stored state text of past Jev calls from your ledger again, to compare question versions. It is maintainer tooling.
- `evals/performance/run.py` and `adapters/jev/probe_openrouter.py`: a synthetic fixture, no project text.

## Other network use

`jade upgrade` and `jade upgrade --check` call GitHub: `api.github.com` for release data, `raw.githubusercontent.com` for the changelog, `github.com` and `codeload.github.com` through `uv` for the install, and `*.githubusercontent.com` for redirects. Only `https` and only these hosts are allowed. `uv` itself may also fetch the three Python dependencies from a package index when it installs. That is `uv`, not JADE.

Nothing else in JADE opens a network connection.

## How long it stays on your machine

JADE keeps a ledger in `~/.local/share/jade` (mode 600). It holds the command text of gated calls, the text of Jev calls, decisions, permits and your answers.

- `jade maintain` removes most project text older than 90 days and keeps every number (statuses, hashes, risk levels, timings, costs, probabilities). Change the window with `retention_days` in your config, or run `jade ledger purge --older-than 90d` yourself.
- The queue of the audit of silent runs (`audit_queue`, V6-29) keeps the command text and the folder of each call that ran from a standing answer. The purge clears both once you have checked the call (`right` or `wrong`) and the check is older than the window, and it seals the row again. A call you have not checked yet keeps its text, because `jade review` shows it to you.
- Two paths are not cleared by any purge, also not with `--ask-text`. The working folder of a gated call stays in the permit (its scope holds the plain path). For a `git push`, the repository path and the commit id stay in the permit's precondition. A folder name that names a customer or a project stays in the ledger file. Only removing the ledger file removes them.
- On macOS, `jade setup` can install the job that runs `jade maintain`. Off macOS nothing runs it until you schedule it: `jade maintain-schedule` prints a cron line or a systemd timer. `jade check` warns while the job has not run.
- By default the purge does not clear the chat question text of an ask, and not the labels of asks and answers, which can hold up to 120 characters of a command. `jade ledger purge --ask-text` clears them. It is off until the owner decides (work item W-147), and `jade maintain` never turns it on.
- `jade ledger purge --dry-run` shows what would be cleared, per table, and changes nothing.

## What JADE cannot promise

- What OpenRouter or TypeSafe keep, log, share or train on. JADE does not control it. Read their terms.
- That redaction finds every secret. It finds the shapes it knows. A secret that looks like ordinary text is sent as ordinary text.
- That a different provider is possible. There is one.
