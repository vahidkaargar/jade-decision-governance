# Labelled calls (V6-21)

`corpus.jsonl` is empty on purpose. It holds the labelled calls that the decision benchmark needs (docs/14-v6/10 section 8). The labelling is the owner's work: about 150 gray-zone rows per rater, about 5 hours each. No row here is made up and no label is guessed.

## What is here

- `../rubric/rubric.md` tells a rater how to label a call: harm, reversibility, scope. It names no rule. `../rubric/derive.json` is the versioned table that turns the two stored labels (`harm`, `authority`) into allow, ask or block.
- `agree.py` loads a corpus, checks every row, computes kappa and percent agreement of the two raters, counts hard-class rows per family, and freezes the final labels by hash. Its docstring has the row format.
- `corpus.jsonl` is the file for committed rows. Only `synthetic` or `redacted` rows (or `public:<set>` rows) may go in. A secret scan and a host scan run over every committed row.

## Steps for the owner

1. Choose a private folder outside the repository for real rows. Real commands, paths and host names stay there.
2. Draw gray-zone rows. Write them as JSONL in the format of `agree.py`. A model may draft labels, but it is never a rater and its drafts enter no gate.
3. Rater 1 (the owner) and rater 2 (a second person, or a different reader of the rubric) label every row alone, blind.
4. `uv run python evals/labels/agree.py report /private/path/rows.jsonl` shows the counts, kappa and the gates. Target: kappa 0.70 or more on allow against not-allow. If it is lower, fix the rubric and label again.
5. The owner decides every disagreement and sets `adjudicated` to true.
6. `agree.py freeze FILE --write` saves the hash of the whole rows (both raters' raw labels too) in `FILE.frozen.json` beside the file. A frozen set is not edited. If the rubric changes, its version goes up and the rows are labelled again.
7. Rewrite or redact the rows you want to commit, put them in `corpus.jsonl`, and run `agree.py check`. The committed corpus has its own record, `frozen.json`: it is a different set of rows, so it has a different hash. Freeze it the same way once its rows are final.

## Stop state

If the owner cannot label 150 rows, this phase ends with "no accuracy number". The deterministic gates stay the only claim. `agree.py report` says so while fewer than 150 rows are labelled by both raters.
