# Escaped operators are data (W-96)

Status: approved by the owner as part of the redirect gate fix plan (2026-11-15). Builds on PR 47 (`17cdd74`).

## Intent

PR 47 hoists every redirect out of a command so that rules read the words, not the redirect. Review found a regression: `git push origin feature a\&>log :main` is blocked on `main` (by the hook) and passes SI-08 on PR 47. Real bash and zsh run it as a push with the arguments `a&` and `:main`, which deletes `main`. The same holds for `a\\&>log :main` (one literal backslash, then a real `&>`).

## Root cause

1. The scanner (`jade/shellnorm.py`, escape branch near line 1048) emits an escaped character as backslash plus the character: `\&` stays `\&`, `\\` stays `\\`.
2. `REDIRECT` has `(?<!\\)` before its operator, so `\&>` is not read as `&>`. The alternative `>` still matches after the `&`, and `_hoist_redirects` removes `>log`, leaving `a\& :main`.
3. `SPLIT` and `SEG_TOKEN` cut at a bare `&` and know nothing about escapes. The push segment loses `:main`.
4. The lookbehind is also wrong the other way: in `a\\&>log` the `&>` is a real operator, but `(?<!\\)` treats it as escaped.

A regex lookbehind cannot tell an escaped backslash from an escape. The scanner can. Quoted operators already use `_INERT` and become `\x06` ("a quoted `>` is data").

## Goals

- An operator character written with a backslash is data after the scanner, as a quoted one is.
- A real operator is never taken for an escaped one.
- `git push origin feature a\&>log :main`, `a\\&>log :main` and the `\>`, `\|`, `\;`, `\(`, `\{` variants are blocked by SI-08 as on `main` (hook and pattern).

## Non-goals

No change to what is blocked or rated. No change to quoting. No new rule.

## Approaches

1. **Scanner marks escaped operators inert (chosen).** If the escaped character is in `_INERT`, emit `\x06`. An escaped backslash must not be readable as an escape by any regex (emit it so that the next character is judged on its own). Remove `(?<!\\)` from `REDIRECT`. Fixes both directions.
2. Add `(?<!\\)` to `SPLIT`, `SEG_TOKEN`, `NULL_REDIRECT`. Wrong for `\\&>` and leaves four regexes to keep in step. Rejected.
3. Refuse to hoist a word that ends in a backslash. Hides this case only, not the split of `a\& :main`. Rejected.

Cost of approach 1: the word `%h\>x` becomes `%h\x06x`. `test_quoted_and_escaped_operators_are_data` expects `\>` and must change to the inert spelling. Check `jade/egress.py` (`"\\" in d`), `jade/blind.py` and `jade/policy.py` (they already treat `\x06` as "not a plain word").

## Requirements

- No change for commands without a backslash: differential against `origin/main` over `tests/fixtures/*.json` and the PR 47 matrix.
- `a\\&>log` is still hoisted (a real operator after a literal backslash).

## Tests that must exist (review focus)

Backslash and double backslash before every spelling in `FILE_REDIRECTS` and `FD_REDIRECTS`, for all `PUSHES` and `OTHERS`; a backslash at end of line (continuation); `\\\&`; a backslash inside double quotes (`"a\&"`); escaped operators inside `bash -c` and `eval` bodies. Compare with real bash and zsh using a fake `git` function that prints its arguments to stderr (never redirected away).

## Verification

`uv run ruff check`; `uv run pytest -q`, also with an empty HOME and `CI=1`; probe diff against `origin/main` (no case that is BLOCK on `main` may be `pass`); gate p95 unchanged (about 2 ms at 20k rows).

## Implementation notes (added with the fix)

- The scanner change is one line in the escape branch: `_INERT.fullmatch(d)` gives `\x06`. An escaped backslash stays `\\`; the `(?<!\\)` lookbehind is gone from `REDIRECT`.
- The oracle tests (`tests/test_redirect_anywhere.py`) run the command in real bash and zsh with a `git` function that appends its arguments to a file. 2 522 cases agree: the gate stops every command in which the shell pushes to the default branch.
- Differential against `origin/main` (9 373 escaped-operator commands, `diff_wide.py` in the session scratchpad): 336 commands were SI-08 blocked on `main` and are not on the branch. All 336 were checked in bash and zsh: none pushes to the default branch (`a\>&log :main` is the word `a>`, a background `&`, and a separate command `log`). 1 002 commands blocked only on the branch.
- Found while checking: `git push origin feature a>&-main`. bash closes the descriptor and `main` is a separate word, with no blank. `REDIRECT` took `-main` as the target. A `-` right after `>&` or `<&` is now the whole target. Same hole on `origin/main`.
- Not fixed, same on `origin/main` (24 strict xfail tests keep it visible): a `{` or `}` inside a word is a literal in bash and zsh, but `SPLIT` cuts the segment there, so `git push origin feature a{ :main` is not stopped (assessment and hook). It needs its own change: only a `{` or `}` that stands as a word of its own is a group.

## Follow-up (W-108, W-109): what the review of the fix found

Fixed in the same PR, with `tests/test_redirect_zsh_and_braces.py` (real bash and zsh, a recording `git`, also through `sh -c`, heredocs and `| zsh`):

- zsh redirect operators (`>>&`, `>>|`, `&>|`, `&>>|`, `>>&|`, `>>&!`, `>!`, `>>!`, `>&|`, `>&!`): added to `REDIRECT`; a `|` after `>&` does not split.
- `>&-` with a word glued to it: bash closes and the word is an argument, zsh reads a file `-word`. The skeleton holds both readings as two commands.
- A `{` or `}` that is not a command-group brace is data (`\x06`), decided in the scanner (`_group_brace`): the `{`/`}` hole noted above is closed and its 24 xfail tests are removed.
- An unquoted heredoc and `echo`/`printf`/`print` halve `\\` before the next shell reads the text; both texts are read.

Not in this PR (W-110, spec B): `git -C my\ repo push`, `a\ #c`, an odd redirect target word, `>! lg`, escape-halving wrappers, `>& -main`, a quoted `-C` value with an operator.

## Follow-up (W-111, W-113): the merge bar and what it left out

Review of the brace fix (e1a76a3) found regressions against `origin/main`. The merge bar that came out of it: the PR fixes the redirect that hid the rest of a push, it is never weaker than `origin/main` on any command, and it costs at most about 3 times as much on realistic input. A gap that `origin/main` has too goes to spec B (W-114). Fixed here, with `tests/test_redirect_round3.py`:

- E3-3: the redirect hoist took the first word of a here-string for its file, so `cat <<< 'git push origin :main' | sh` lost `git`. A stage's own stdin text (the words after `<<<`, the body of a heredoc inside `<(...)`) is now read as text, as well as the printed words.
- E3-4, E3-9: a prefix word with a backslash or a quote (`\env echo '...' | sh`) hid the echo (the plain-text form of that hole is closed too); an escaped operator has two readings (a word, and a backslash with a live operator), as on `origin/main`.
- DF-6: a decoded `$'\x0f'` could make a brace mark after the sanitiser had run. `_ansi_c` neutralises every mark byte.
- B3-5: a `{` that stands alone where a command starts, and a `}` that closes it, are written as they are: bash and zsh read them the same way, so a script of groups is read once. Ambiguous braces (after `if`, glued to a word, no blank before `}`) keep the five readings.

Not changed (spec B, W-114): zsh short forms such as `if { true } { echo a }` repeated thousands of times cost about 5 times `origin/main` (the word reading adds a segment each); a classification cache for repeated identical segments would cut it.

## Follow-up (W-115): the fourth review

Merge bar as above. Fixed, with `tests/test_redirect_round4.py` (real bash and zsh where a push is claimed):

- R4-O1, CR4-1, DIFF-W2: `_here_strings` takes the here-strings of a stage out of its raw text, quote aware, with the descriptor before a `<<<` and the program before it glued or not. The texts are read as the stdin of the stage; they are no longer part of the printed text, so a chain of here-strings is read once per level, at a quarter of a nesting level each (a chain 13 deep passes, as on `origin/main`; 14 and more pass there too).
- CR4-2: `_stdin_texts` reads every heredoc of a text (a queue of delimiters), not the first; `HEREDOC_AT` does not match the last two `<` of `<<<`.
- R4-O2: `_hoist_piece` keeps the blank in front of a piece.
- R4-O3: `end_stage` keeps a second chain of stages for the reading in which a brace inside a word does not cut (`whole`), and reads the text a producer of that chain hands to the next stage.
- DIFF-W1: `_redirect_word` returns a descriptor close or move as written. Not restored: a leading input redirect before a read of a key is rated like the trailing form (0.6.1 rated it critical because the command starts with a redirect; it is not a write).
- R4-O4, CR4-3, DIFF-P2: `_readings` works on chunks (`_chunks`: lines, joined while a group is open or the line is continued); the live reading of an escaped operator is added to the first brace reading only; `_collapse_runs` cuts a run of one word (or up to eight) to four in every reading of a chunk longer than `DENSE`, unless the run moves the directory or counts (`cd`, `-C ..`, `..`). When a chunk moves the directory its other readings are subshells written first, so the tracker moves once (checked against two nested repositories; the verdicts equal those of `origin/main`).
- Review of this fix: a here-string is found by a quote-aware scanner (`_here_strings`), not by the words (a `<<<` in quotes is text); the refill of the budget is an allowance (`HERE_ALLOWANCE`), and heredoc bodies are read at full depth; `_sub` memoises a text per depth and a deferred text is written once (the pipes of a chain read the same text once, not once per pipe).

Not changed: W-114 (`&>log` rating, zsh short forms). Not covered by the readings any more: a brace reading other than the first combined with the live reading of an escaped operator in one command (0.6.1 reads neither combination).

