# Open items: PR-B, PR-C, PR-D implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix the plain-text bugs, harden the merge scope against global git config, and make every review/knows line one printable line.

**Architecture:** Three independent branches, each cut from `design-clear-questions` (PR 42, not merged yet; rebase onto `origin/main` after it merges). PR-B touches only `jade/askplain.py`; PR-C only `jade/scopes.py`, `jade/memory.py`, `jade/proxy.py`; PR-D only `jade/util.py`, `jade/ops.py`, plus `jade/scopes.py:_one_line` call sites (one line, see PR-D Task 1). Tests per PR in their own new files.

**Tech stack:** Python 3.13, pytest, ruff, uv.

**Overview plan:** `docs/superpowers/plans/2026-11-15-open-items.md` (categories, roles, order).

## Global constraints

- Owner-facing sentences: simple English, one idea each, at most 20 words, end with ".".
- No subprocess in `jade/scopes.py`: git config is read as plain text.
- Fail closed: when unsure, no scope (return None), never a wider scope.
- Tests first. `uv run ruff check` clean. `uv run pytest -q` green on the whole suite.
- Explicit `git add <paths>`. No push, no PR, no permit approval, no subagents.

## Review focus

1. A refspec with both `+` and `:` (`+HEAD:main`): force wins over plain push.
2. `glab mr merge -R other/repo 12`: `-R` takes a value; the 12 is still the MR.
3. A global config that is unreadable (permission denied) must give no merge scope.
4. A label holding ESC inside a scope label from an old ledger row (stored before 0.7.0) must still print clean.
5. A deny confirmed after an allow of the same group must hide the allow line in the template.

---

## PR-B "plain text fixes" (branch `open-items-plain-text`)

### Task B1: push refspec lines

**Files:** Modify `jade/askplain.py` (SEGMENT_TEXT push rows). Test: `tests/acceptance/test_plain_text_fixes.py` (new).

- [ ] Write failing tests, `policy.assess("Bash", {"command": cmd}, cwd="/tmp")` then `askplain.what_line(a, "Bash")`:

| cmd | line |
|---|---|
| `git push origin :main` | `Delete the remote branch main.` |
| `git push origin :feature` | `Delete the remote branch feature.` |
| `git push origin :refs/heads/main` | `Delete the remote branch main.` |
| `git push origin +main` | `Force push to main.` |
| `git push origin +feature` | `Force push to feature.` |
| `git push origin +HEAD:main` | `Force push to main.` |
| `git push origin HEAD:main` | `Push to main.` |
| `git push origin main:main` | `Push to main.` |
| `git push origin HEAD:refs/heads/main` | `Push to main.` |
| `git push -u origin main` | `Push to the main branch.` (unchanged) |
| `git push --force origin feature` | `Force push to a remote.` (unchanged) |
| `git push origin --delete feature` | `Delete a branch or tag on a remote.` (unchanged) |

- [ ] Add rows before the generic `^git push\b` row, after the `--force/--delete/--tags/main` rows: a delete refspec `\s:(?:refs/heads/)?([\w./-]+)(?:\s|$)`, a force refspec `\s\+(?:[^\s:]+:)?(?:refs/heads/)?([\w./-]+)(?:\s|$)`, a `SRC:DST` refspec `\s[^\s:+-][^\s:]*:(?:refs/heads/)?([\w./-]+)(?:\s|$)`. The `(?:main|master)` row must not catch `:main` or `+main` (it needs `\s` before the name, which these forms do not have; check it).
- [ ] Every new sentence is at most 20 words with a branch name of 60 characters.

### Task B2: glab value flags

- [ ] Tests: `glab mr merge --message 5 12`, `glab mr merge -m 5 12`, `glab mr merge --sha abc 12`, `glab mr merge --squash-message x 12`, `glab mr merge -R o/r 12` give `Merge a merge request.`; `glab mr merge 12 --squash`, `glab mr merge --squash 12`, `glab mr merge -d -y 12`, `glab mr merge --message=5 12` give `Merge merge request 12.`
- [ ] Change the glab number row to allow before the number only no-value flags (`--squash -s --rebase -r --remove-source-branch -d --yes -y --auto-merge`) or `--x=v`, like the `gh pr merge` row.

### Task B3: risk lines without hard words

- [ ] Test `test_no_jargon_in_risk_lines`: no sentence in `RISK_TEXT`, `HO_TEXT`, `GENERIC_RISK`, `GENERIC_HO` contains (case-insensitive word match) any of: `reflog`, `boot service`, `deployment`, `dependencies`, `credential`, `connectors`, `cluster`, `pipeline`, `overlay`, `invariant`, `refspec`.
- [ ] Rewrite the sentences that fail. Use: "a service that starts with the Mac", "a live server or app", "the packages it needs", "a password or key", "links to other apps", "a group of servers", "a build job". R-20: "This rewrites or drops local git history. Git can usually bring it back." is two sentences: keep one: "This rewrites local git history, and git can usually bring it back."

### Task B4: test debt

- [ ] `test_what_line_renderers`: replace `<= 20 + 6` with: the sentence without the `Run: ` fallback text is at most 20 words; for the fallback row assert `len(line) <= len("Run: ") + askplain.FALLBACK_CHARS`.
- [ ] New test: a call with `ho_ids` and no `seg_keys` (`psql -c "DROP TABLE x"`): `header()` has exactly 2 lines, line 0 is `Run a shell command.`, line 1 the HO sentence.
- [ ] Commit: `Plain lines: push refspecs, glab value flags, no jargon in risk lines`.

---

## PR-C "scopes hardening" (branch `open-items-scopes`)

### Task C1: global and system git config in the merge identity

**Files:** Modify `jade/scopes.py:_repo_identity`. Test: `tests/acceptance/test_scopes_global_config.py` (new).

- [ ] Split the parser out: `_config_lines(path: str, keep_sections: tuple[str, ...]) -> list[str] | None` returns the kept lines, `[]` when the file does not exist, None when it exists but cannot be read, is not UTF-8, is over `CONFIG_MAX`, has an unknown header, or has `include`/`includeIf`.
- [ ] `GLOBAL_CONFIGS()`: `$GIT_CONFIG_GLOBAL` is not read (see below); `~/.gitconfig`, `${XDG_CONFIG_HOME:-~/.config}/git/config`. `SYSTEM_CONFIGS`: `/etc/gitconfig`, `/opt/homebrew/etc/gitconfig`, `/usr/local/etc/gitconfig`, `/Library/Developer/CommandLineTools/usr/share/git-core/gitconfig`, `/Applications/Xcode.app/Contents/Developer/usr/share/git-core/gitconfig`.
- [ ] `_repo_identity` returns None when any of these env vars is set (non-empty) in `os.environ`: `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG`, `GH_REPO`, `GH_HOST`.
- [ ] It adds the `remote` and `url` lines of every global and system file, each prefixed `global\t` / `system\t`, sorted with the repo lines. None from any file means None. With no such lines the result is the same list as before (old keys keep matching).
- [ ] Tests (monkeypatch `HOME`, `XDG_CONFIG_HOME`, and the module's system path list to tmp paths):
  - global `url."git@evil:".insteadOf = https://github.com/` changes the merge key vs. no global file;
  - global `[include] path = x` gives no merge scope; `[includeIf "gitdir:~/"]` too;
  - global file with mode 000 gives no merge scope (skip when running as root);
  - each env var above set to `x` gives no merge scope (parametrized);
  - a global file with only `[user]` keeps the key equal to the no-file key.

### Task C2: public names in memory

- [ ] Rename `memory._INLINE` to `memory.INLINE`, `memory._MERGE` to `memory.MERGE`, `memory._valid_since` to `memory.valid_since`. Update `jade/scopes.py` and `jade/proxy.py`. Keep no alias. `grep -rn "memory\._" jade/` returns nothing.

### Task C3: keys_for speed

- [ ] Test `test_keys_for_is_fast`: build one merge call in a tmp repo, time 1000 `scopes.keys_for(...)` calls; assert total under 2.0 s. If it fails, cache `_repo_identity` per (path, mtime_ns, size) of every config file read; never cache a None for a file that later appears.

### Task C4: long label

- [ ] Test: a project path of 300 characters and a family of 200 characters: `_label` result is at most `LABEL_MAX` characters, starts with the command text, and contains `…`.
- [ ] Commit: `Merge scope: read global and system git config, fail closed on env overrides; public memory names`.

---

## PR-D "review and knows output" (branch `open-items-review-output`)

### Task D1: one printable line everywhere

**Files:** `jade/util.py` (add `one_line`), `jade/scopes.py` (use it), `jade/ops.py`. Test: `tests/acceptance/test_review_output_lines.py` (new).

- [ ] Move `scopes._one_line` to `util.one_line(text: str | None) -> str` (None gives ""). `scopes._label` calls `util.one_line`; delete `scopes._one_line`.
- [ ] In `ops.py`, every `label` and `scope_label` printed by `review_lines`, `review_confirm`, `_scope_lines`, `knows_list`, `knows_template` goes through `util.one_line`.
- [ ] Tests: insert `human_answers` rows (use the ledger helpers the existing tests in `tests/acceptance/test_review_scope.py` use) whose `label` and `scope_label` hold `"a\x1b[2Kb\x07c\nd"`; the output of `knows_list()`, `review_confirm(all_=True, ask=lambda _: "no", say=out.append, stdin_isatty=True)`, `knows_template(rows)` and `review_lines` holds no `\x1b`, `\x07`, and no newline inside a printed line.

### Task D2: short confirm summary

- [ ] Replace the summary `say(...)` in `review_confirm` with three sentences, each at most 20 words:
  - `"{N} answer(s) in {G} group(s) will become standing."`
  - `"At level low or medium, a matching call then runs without a question."`
  - `"It lasts {USE_DAYS} days after its last use, never more than {CAP_DAYS} days. Undo with jade knows rm ID."` (split if over 20 words).
- [ ] Test: each printed summary sentence is at most 20 words.

### Task D3: a newer deny hides an older allow in the template

- [ ] `knows_template`: compute the group key for every row, allow or deny. The first (newest) row of a group decides: a deny marks the group seen and prints nothing.
- [ ] Tests: rows newest first `[deny X, allow X]` give no `I am fine with` line for X; `[allow X, deny X]` gives one line.
- [ ] Commit: `Review and knows output: one printable line, short summary, newer deny wins`.

---

## After all three

- Lead runs `uv run ruff check` and `uv run pytest -q` in each worktree.
- Security engineer (most capable model) reviews all three diffs adversarially.
- One fix wave, scoped re-review.
- CHANGELOG `[Unreleased]` lines: one per PR, written by the lead after review.
- Owner approves push and PRs.
