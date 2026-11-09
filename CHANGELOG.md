# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog; versioning: SemVer once code ships.

## [Unreleased]

### Fixed
- Gate bypasses found by the adversarial review (cluster A1: F00-F05, F35). One canonicalisation step, `jade/shellnorm.py`, now runs before every risk rule, safety invariant and the push resolver:
  - git global options (`-C`, `-c`, `--git-dir`, `--work-tree`, `--no-pager`, `-P`, `--exec-path`, `--namespace` ...) no longer hide `git push`, `reset --hard`, `clean`, `branch -D` or SI-08; `git -c` keys that run programs (alias, core.sshCommand/pager/editor/fsmonitor/hooksPath, credential, url ...) and `--exec-path` are the new high rule R-15.
  - Backslash escapes, line continuations, `$'...'` strings and adjacent quotes are resolved before matching; a `<<` inside quotes can no longer swallow the lines after it.
  - Command substitutions inside double quotes, backtick spans and unquoted heredoc bodies are classified; a heredoc, here-string, pipe or process substitution that feeds `sh`/`bash`/`zsh`/interpreters, and the program text of `python -c`, `perl -e`, `node -e`, `awk` and `sed 'w ...'`, are searched for literal commands.
  - The `read_only` list can no longer write: `sort -o`, `uniq IN OUT`, `sed` `w`/`e`/`-i`/`--in-place`, `awk`/`gawk` programs with `system`, redirects or `-i`, `git diff|log|show --output`, `find -fprint/-fls/-okdir`, `xxd -r` or two operands, `yq -i`, `tree -o`, `less -o`, `date -s`, `fd -x`.
  - SI-08 parses `git push` arguments: refspec destinations, `refs/heads/` and `heads/` forms, `-o`/`--push-option`/`--repo` values, `--force-with-lease=...`, `HEAD`/`@`/empty refspec resolved against the real current branch (relative `-C` resolved against the event cwd), `--all` and `--mirror`; the remote's default branch name (`origin/HEAD`) counts too.
  - Write, Edit, MultiEdit and NotebookEdit paths are expanded (`~`), resolved against the event cwd, symlink-resolved and matched case-insensitively (R-01 path, R-04 key files and R-13 are case-insensitive, as the macOS filesystem is); Bash path tokens are folded (`//`, `/./`, `x/../`).

- Gate bypasses found by the second adversarial pass over cluster A1 (F01-F03 variants and new defects N1-N7). The classifier now has one table of what each read-only verb can write or run, and the scanner resolves the words a shell receives:
  - Performance: the scanner is linear (stage text is kept as head and tail, plain runs are matched in bulk) and has a 4-million-character work budget across nested levels; 80 KB of quoted words took 54 s and nested `sh -c` quoting 10 levels deep took 60 s, now both are under 0.2 s and anything past the budget degrades to plain text search.
  - `eval` and `sh -c`/`su -c` take the joined words of the command, so `eval "git" push origin main`, `sh -c "git"' push origin main'`, `sh -c git\ push\ origin\ main` and nested POSIX quote escapes are seen; positional parameters (`sh -c '$0 push' git`), `eval "$(echo ...)"` and `\`` nested backticks are resolved.
  - Pipelines: every earlier stage of a pipeline counts as input of a shell reader at its end (`echo ... | tee /dev/stdout | sh`), and `xargs sh -c`, `source /dev/stdin`, `. /dev/stdin`, `bash /dev/fd/0` read stdin as code; nested `echo '...| bash' | bash` is parsed level by level.
  - Comments end at the newline even after a trailing backslash (`echo hi #\` + newline + `git push origin main` ran the push); brace lists are expanded (`git push origin m{a,}in`).
  - Read-only verbs: `sort --out=`/`--o`, `uniq -f 1 IN OUT`, `xxd -c 8 IN OUT`, `sed 'w/path'`, `sed w/path`, `less --log-file`, relative operands after `cd`; exec options of `less +!`, `man -P`, `rg --pre`, `bat --pager`, `sort --compress-program`, `fd -x`, `find -exec`, `git fetch --upload-pack`, `git rebase --exec`, `git submodule foreach`, `git bisect run` and environment variables such as `LESSOPEN`, `PAGER`, `GIT_PAGER` are classified as the commands they run.
  - cwd tracking: `cd`, `pushd` and `popd` are followed for `git push` HEAD resolution (SI-08) and for relative operands, so `cd main-repo && git push origin HEAD` is blocked and `cd /etc && sort -o pf.conf in` is critical.
  - Low-severity gaps: `git clean -f -d`, `git clean --force -d`, `git branch --delete --force`, `git branch -vD`, `git reset --har`, `rm -r -f`, `rm -fr`, `rm --recursive --force` are R-11; `GIT_SSH_COMMAND`, `GIT_PAGER`, `GIT_EDITOR`, `GIT_ASKPASS`, `GIT_EXEC_PATH`, `GIT_CONFIG_*` set to something that runs a program are R-15; Write/Edit/MultiEdit/NotebookEdit with `path`, `filePath`, `file`, `filepath`, `target_file`, `file_name` or any unknown key are classified; R-12 and the production-path check are case-insensitive.

### Changed
- Second pass, stricter again: a `git push` whose HEAD resolves to main after a `cd` is an SI-08 block; `rm -r -f`, `rm -fr`, `rm --recursive --force` need a permit like `rm -rf`; `git clean -f -d`, `git branch --delete --force` need a permit; setting `GIT_SSH_COMMAND`, `GIT_PAGER`, `GIT_EDITOR` and similar to anything but a plain pager, editor, `true` or `ssh -i key` needs a permit (R-15); a `less`, `man`, `rg`, `bat`, `sed`, `awk`, `uniq`, `xxd` or `sort` invocation that can write is classified by where it writes, so `sed -n '1,5p' ~/.claude/settings.json` stays low but `sed -n 'w ~/.claude/settings.json' f` is high; a sed script with a command the allow-list does not know (for example `sed 's/a/b/w out'`) has its resolved words classified, which is only visible when it names a protected path.
- Stricter, so a few previously allowed commands now need a permit (or are blocked): `git push --all` / `--mirror` and any push whose destination is main/master, `HEAD` or `@` while on main/master, or an unresolvable `$VAR` destination on main/master are SI-08 blocks; `git reset ... --hard` with options in any order, `git branch ... -D`, `git clean` with both `f` and `d` flags in any order are R-11; R-13 now also covers `policies/risk.yaml`, `evals/*/protected/**` and the gate and ledger code (`jade/permits|hooks|ledger|policy|shellnorm|decide|state_machine.py`), so even `git add jade/hooks.py` is high until W-32 scopes it to writes; `awk`, `sed`, `find`, `rg`, `fd` and friends are read-only only without their write or exec flags; harmless `git -c user.name=x ...` stays low.

## [0.2.0] - 2026-11-09

### Added
- `jade` runtime package and `jev` compatibility shim (same flags and output line, plus exit codes 0/3/4/5/6).
- SQLite ledger with write-ahead call rows, journaled state-machine events, dedup, 90-day state purge and in-place migrations.
- OpenRouter adapter with typed errors and bounded retries; redaction with a 42-item corpus and a residual-secret refusal.
- Question registry (10 entries), lint, preflight routing, deterministic `jade fast` checks.
- Permits (single use, 30-minute TTL, plan-hash bound), TTY-only approval, PreToolUse gate for Bash, file edits and MCP tools, Stop-hook disposition reminder, SessionStart resumption, advisory/enforce/off modes.
- Calibration: labels, session-hash splits, metrics with Wilson and paired-bootstrap intervals, adjudication queue, threshold proposals, language slices.
- Bounded improvement loop: ledger miner writing task-observer observations, offline replay, async shadow runner, 20% canary, champion/challenger pins, kill switch, rollback.
- `jade check`, `jade maintain`, launchd plist generator, hook and CLAUDE.md installers that refuse to run for an agent.
- 35 replayable regression and adversarial cases; `skills/jade/SKILL.md`; `docs/08-deployment-runbook.md`.

### Changed
- Policies: read-only commands and quoted or heredoc text no longer trigger rules (found by false positives while building the classifier); `mcp_tools` rules added; SI-09 stops an agent from approving permits or changing its own wiring.

### Added
- Phase 0 design package: inventory and gap register, 12 ADRs, state machine, six JSON Schema contracts with examples, five policy files with a local-overlay mechanism, evaluation protocol, threat model, roadmap, backlog.
- Baseline tooling: transcript miner, policy replay, live performance harness, OpenRouter probe.
- Executable acceptance-test skeletons for Phases 1–5 (skip until the `jade` package exists).
