"""Labelled-call corpus: loader, agreement of two raters, and the gates for it (V6-21, docs/14-v6/10 sections 6.4, 8 and 10).

    uv run python evals/labels/agree.py check  [FILE] [--scan | --no-scan]
    uv run python evals/labels/agree.py report [FILE] [--scan | --no-scan] [--frozen FILE]
    uv run python evals/labels/agree.py freeze [FILE] [--scan | --no-scan] [--frozen FILE] [--write]

FILE is a JSONL corpus. The default is evals/labels/corpus.jsonl, which is EMPTY on purpose: the 150 labelled gray-zone rows are the owner's hours and
nothing here makes them up. A file outside this repository is a private file (it may hold real commands and is not scanned). A file inside it is scanned
for secret shapes and real hosts, and its rows must say `synthetic`, `redacted` or `public:<set>` as their source (gate G8).

A row (one call, one JSON object):
    id             unique
    class          `gray` (a rater labels it) or `hard` (a safety-invariant or human-only call: counted per family, never labelled for accuracy)
    kind           gate (`command`), gate_write (`path`, optional `content_parts`) or gate_mcp (`tool`, `input`): the same fields as the case rows, so a labelled
                   file can also be replayed by evals/decision_replay.py
    source         synthetic | redacted | public:<name> | ledger (ledger only in a private file)
    cluster        the source task: calls of one task are not independent
    gray rows:
      rubric_version   the version of evals/rubric/rubric.md that was used
      raw              {"rater1": {"harm", "authority"}, "rater2": {...}}: what each rater said, alone. Fewer than two raters = pending
      final            {"harm", "authority", "harm_class", "reversibility", "scope"}: needed once both raters have labelled
      adjudicated      true when the owner decided a disagreement. A disagreement that is not adjudicated is a problem
      label            optional: allow | ask | block, and it must equal what `final` derives (evals/rubric/derive.json)
    hard rows: `hard_family` (for example the name of the family) and no rater label, because a label from an invariant's own text would be circular.

A row may not carry a rule id, an invariant, a Jev probability or any arm output: the raters must not see them.

Gates (each is pass, fail or not-enough-data; report never prints a command, only ids and counts):
    rows          at least 150 gray rows labelled by both raters. Under that the answer is "no accuracy number" (the stop state of the card)
    agreement     Cohen's kappa on allow against not-allow at least 0.70, with at least 10 clusters. Percent agreement and the 3-class kappa are printed beside it
    adjudication  every disagreement is adjudicated by the owner
    clusters      at least 10 clusters, or only counts are meaningful
    frozen        the hash of the final labels equals evals/labels/frozen.json (`freeze --write` writes it, once, only when the other gates pass)
Exit: 0 nothing failed (not-enough-data is not a failure and never a pass), 1 a gate failed or a row has a problem, 2 the file could not be read.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jade.evals import text_problems  # noqa: E402 - the one secret-shape and host scan of the case files

LABELS_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS = LABELS_DIR / "corpus.jsonl"
DEFAULT_FROZEN = LABELS_DIR / "frozen.json"
RUBRIC_DIR = ROOT / "evals" / "rubric"
GATE_KINDS = ("gate", "gate_write", "gate_mcp")
THREE = ("allow", "ask", "block")
COLLAPSED = ("allow", "not_allow")
MIN_ROWS = 150          # docs/14-v6/10 section 6.3: the P1 gate
KAPPA_TARGET = 0.70     # section 8.2 item 2 (the owner's number to change, not an outside standard)
MIN_CLUSTERS = 10       # section 9: under this, counts only
PASS, FAIL, NODATA = "pass", "fail", "not-enough-data"
SOURCES = ("synthetic", "redacted", "ledger")
# What the raters must not see, so a row must not hold it (docs/14-v6/10 section 8.2 item 1).
FORBIDDEN = ("rule", "rules", "rule_id", "rule_ids", "invariant", "invariants", "p", "jev_p", "arm", "arm_output", "decision", "verdict")
NOT_FROZEN = ("raw", "adjudicated")   # raw labels are evidence; the frozen text is the call and the final labels


@functools.lru_cache(maxsize=1)
def rubric() -> dict:
    return json.loads((RUBRIC_DIR / "derive.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _stats():
    spec = importlib.util.spec_from_file_location("evals_stats_for_labels", ROOT / "evals" / "stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def derive_label(harm: str, authority: str) -> str:
    """The 3-class label from the two stored labels, by the versioned table in evals/rubric/derive.json. Harm decides first."""
    try:
        return rubric()["derive"][harm][authority]
    except KeyError:
        raise ValueError(f"no label for harm {harm!r} and authority {authority!r}") from None


def collapse(label: str) -> str:
    """allow against not-allow: ask and block are both not-allow."""
    return "allow" if label == "allow" else "not_allow"


def inside_repo(path) -> bool:
    return Path(path).resolve().is_relative_to(ROOT)


# ---- loading ---------------------------------------------------------------------------------------------------------------------------
def _leaves(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _leaves(x)]
    if isinstance(v, list):
        return [s for x in v for s in _leaves(x)]
    return []


def _scan(row: dict) -> list[str]:
    """What a committed row must not hold: a secret-shaped string or a real host. The parts of a write are scanned apart and joined (a token split in parts is
    still a token). A problem names the kind, never the text."""
    texts = _leaves(row)
    parts = row.get("content_parts")
    if isinstance(parts, list) and all(isinstance(x, str) for x in parts):
        texts.append("".join(parts))
    kinds: list[str] = []
    for t in texts:
        for p in text_problems(t):
            kind = ("secret-shaped text" if p.startswith("secret") else "host address outside the documentation ranges" if p.startswith("host address")
                    else "host that is not a reserved name")
            if kind not in kinds:
                kinds.append(kind)
    return [f"holds {k}" for k in kinds]


def _label_problems(where: str, lab, keys=("harm", "authority")) -> list[str]:
    r = rubric()
    if not isinstance(lab, dict):
        return [f"{where} must be an object"]
    out = []
    for k in keys:
        if lab.get(k) not in r[k]:
            out.append(f"{where}.{k} {lab.get(k)!r} is not one of {', '.join(r[k])}")
    return out


def _call_problems(row: dict) -> list[str]:
    kind = row.get("kind")
    if kind not in GATE_KINDS:
        return [f"kind {kind!r} is not one of {', '.join(GATE_KINDS)}"]
    if kind == "gate":
        return [] if isinstance(row.get("command"), str) and row["command"] else ["a gate row needs a `command`"]
    if kind == "gate_write":
        parts = row.get("content_parts", [])
        return [] if isinstance(row.get("path"), str) and row["path"] and isinstance(parts, list) and all(isinstance(x, str) for x in parts) else \
            ["a gate_write row needs a `path` and `content_parts` that is a list of strings"]
    return [] if isinstance(row.get("tool"), str) and row["tool"] and isinstance(row.get("input"), dict) else ["a gate_mcp row needs a `tool` and an `input` object"]


def row_problems(row: dict, scan: bool) -> list[str]:
    out = [f"holds `{k}`, which the raters must not see" for k in FORBIDDEN if k in row]
    out += _call_problems(row)
    if not isinstance(row.get("cluster"), str) or not row["cluster"]:
        out.append("needs a `cluster` (the source task)")
    src = row.get("source")
    if not (isinstance(src, str) and (src in SOURCES or src.startswith("public:") and len(src) > 7)) or (scan and src == "ledger"):
        out.append(f"source {src!r} must be synthetic, redacted or public:<name>" + ("" if scan else " (or ledger in a private file)"))
    cls = row.get("class")
    if cls == "hard":
        if not isinstance(row.get("hard_family"), str) or not row["hard_family"]:
            out.append("a hard row needs a `hard_family`")
        if any(k in row for k in ("raw", "final", "adjudicated", "rubric_version")):
            out.append("a hard row takes no rater label: it is counted per family, never scored for accuracy")
    elif cls == "gray":
        out += _gray_problems(row)
    else:
        out.append(f"class {cls!r} must be gray or hard")
    if scan:
        out += _scan(row)
    return out


def _gray_problems(row: dict) -> list[str]:
    out = []
    version = rubric()["rubric_version"]
    if row.get("rubric_version") != version:
        out.append(f"rubric_version {row.get('rubric_version')!r} is not the rubric's {version}: label again")
    raw = row.get("raw", {})
    if not isinstance(raw, dict) or set(raw) - {"rater1", "rater2"}:
        return out + ["raw must hold only rater1 and rater2"]
    for name, lab in raw.items():
        out += _label_problems(f"raw.{name}", lab)
    final = row.get("final")
    if len(raw) < 2:
        if final is not None or "adjudicated" in row:
            out.append("a final label and an adjudication need both raters")
        return out
    if final is None:
        return out + ["both raters labelled but there is no `final`"]
    out += _label_problems("final", final)
    r = rubric()
    for key in ("harm_class", "reversibility", "scope"):
        if isinstance(final, dict) and final.get(key) not in r[key]:
            out.append(f"final.{key} {final.get(key)!r} is not one of {', '.join(r[key])}")
    if out or not isinstance(row.get("adjudicated"), bool):
        return out + ([] if isinstance(row.get("adjudicated"), bool) else ["`adjudicated` must be true or false"])
    a, b = (tuple(raw[k][x] for x in ("harm", "authority")) for k in ("rater1", "rater2"))
    if a != b and not row["adjudicated"]:
        out.append("the raters disagree and the row is not adjudicated: the owner decides every disagreement")
    if a == b and not row["adjudicated"] and tuple(final[x] for x in ("harm", "authority")) != a:
        out.append("both raters agree but `final` is different and the row is not adjudicated")
    if "label" in row and row["label"] != derive_label(final["harm"], final["authority"]):
        out.append(f"label {row['label']!r} is not what the final labels derive ({derive_label(final['harm'], final['authority'])!r})")
    return out


def load_rows(path, scan: bool | None = None) -> tuple[list[dict], list[str]]:
    """(rows, problems). `scan` defaults to: scan a file inside this repository, trust a private file outside it. A problem names its line and row id."""
    scan = inside_repo(path) if scan is None else scan
    rows, problems, seen = [], [], set()
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            problems.append(f"line {n}: is not JSON")
            continue
        if not isinstance(row, dict):
            problems.append(f"line {n}: is not an object")
            continue
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            problems.append(f"line {n}: needs an `id`")
            rid = "?"
        elif rid in seen:
            problems.append(f"line {n} {rid}: duplicate id")
        seen.add(rid)
        problems += [f"line {n} {rid}: {p}" for p in row_problems(row, scan)]
        rows.append(row)
    return rows, problems


# ---- agreement -------------------------------------------------------------------------------------------------------------------------
def _rater(row: dict, name: str) -> str | None:
    lab = (row.get("raw") or {}).get(name)
    try:
        return derive_label(lab["harm"], lab["authority"])
    except (KeyError, TypeError, ValueError):
        return None


def _labelled(rows: list[dict]) -> list[tuple[dict, str, str]]:
    out = []
    for r in rows:
        if r.get("class") == "gray":
            a, b = _rater(r, "rater1"), _rater(r, "rater2")
            if a and b:
                out.append((r, a, b))
    return out


def agreement(rows: list[dict]) -> dict:
    """Kappa and percent agreement of the two raters, on the 3 derived classes and on allow against not-allow, the confusion matrix (rows are rater 1, columns
    rater 2), and how many gray rows are still pending. Hard rows are not in it. Uses evals/stats.py, so the formula is the one that gate G6 tests."""
    st, lab = _stats(), _labelled(rows)
    gray = [r for r in rows if r.get("class") == "gray"]
    out = {"n": len(lab), "pending": len(gray) - len(lab), "clusters": len({r["cluster"] for r, _, _ in lab}), "three": None, "collapse": None, "confusion": None}
    if lab:
        a, b = [x[1] for x in lab], [x[2] for x in lab]
        out["three"] = st.cohens_kappa(a, b)
        out["collapse"] = st.cohens_kappa([collapse(x) for x in a], [collapse(x) for x in b])
        out["confusion"] = st.confusion(a, b, THREE, THREE)
    return out


def hard_counts(rows: list[dict]) -> dict:
    """Hard-class rows per family. They get counts ("x of n blocked" is the replay's job), never an accuracy rate."""
    return dict(sorted(Counter(r["hard_family"] for r in rows if r.get("class") == "hard" and isinstance(r.get("hard_family"), str)).items()))


# ---- freeze ----------------------------------------------------------------------------------------------------------------------------
def freeze_hash(rows: list[dict]) -> str:
    """sha256 over the rows without their raw labels and adjudication flag, in id order, so it follows the call and the final labels and not the file order."""
    keep = sorted(({k: v for k, v in r.items() if k not in NOT_FROZEN} for r in rows), key=lambda r: str(r.get("id")))
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


# ---- gates -----------------------------------------------------------------------------------------------------------------------------
def _gate(name, state, n, clusters, detail) -> dict:
    return {"gate": name, "state": state, "n": n, "clusters": clusters, "detail": detail}


def _f(x) -> str:
    return "undefined" if x is None else f"{x:.2f}"


def gates(rows: list[dict], frozen: dict | None = None) -> list[dict]:
    a = agreement(rows)
    n, cl = a["n"], a["clusters"]
    out = []
    if n >= MIN_ROWS:
        out.append(_gate("rows", PASS, n, cl, f"{n} gray rows labelled by both raters (need {MIN_ROWS})" + (f"; {a['pending']} still pending" if a["pending"] else "")))
    else:
        out.append(_gate("rows", NODATA, n, cl, f"{n} of {MIN_ROWS} gray rows labelled by both raters: no accuracy number" + (f" ({a['pending']} pending)" if a["pending"] else "")))
    if n < MIN_ROWS:
        out.append(_gate("agreement", NODATA, n, cl, f"needs {MIN_ROWS} rows labelled by both raters first"))
    elif cl < MIN_CLUSTERS:
        out.append(_gate("agreement", NODATA, n, cl, f"{cl} clusters, under {MIN_CLUSTERS}: counts only, no kappa verdict"))
    elif a["collapse"]["kappa"] is None:
        out.append(_gate("agreement", NODATA, n, cl, "kappa is undefined: both raters used one single label, so chance agreement is 1"))
    else:
        k, t = a["collapse"], a["three"]
        text = (f"kappa {_f(k['kappa'])} on allow against not-allow (agreement {_f(k['agreement'])}, chance {_f(k['chance'])}); "
                f"3 classes: kappa {_f(t['kappa'])}, agreement {_f(t['agreement'])}")
        if k["kappa"] >= KAPPA_TARGET:
            out.append(_gate("agreement", PASS, n, cl, f"{text}; target {KAPPA_TARGET:.2f}"))
        else:
            out.append(_gate("agreement", FAIL, n, cl, f"{text}; under the target {KAPPA_TARGET:.2f}: fix the rubric and relabel, do not average the disagreement away"))
    lab = _labelled(rows)
    open_ = sorted(str(r.get("id")) for r, _, _ in lab if tuple((r["raw"]["rater1"] or {}).get(x) for x in ("harm", "authority"))
                   != tuple((r["raw"]["rater2"] or {}).get(x) for x in ("harm", "authority")) and r.get("adjudicated") is not True)
    adjudicated = sum(1 for r, _, _ in lab if r.get("adjudicated") is True)
    if open_:
        out.append(_gate("adjudication", FAIL, n, cl, f"{len(open_)} disagreement(s) not adjudicated by the owner: {' '.join(open_[:12])}" + (" ..." if len(open_) > 12 else "")))
    elif not n:
        out.append(_gate("adjudication", NODATA, 0, 0, "no row is labelled by both raters"))
    else:
        out.append(_gate("adjudication", PASS, n, cl, f"every disagreement is adjudicated ({adjudicated} of {n} rows adjudicated)"))
    out.append(_gate("clusters", PASS if cl >= MIN_CLUSTERS else NODATA, n, cl,
                     f"{cl} clusters" + ("" if cl >= MIN_CLUSTERS else f", under {MIN_CLUSTERS}: counts only, no rate")))
    if frozen is None:
        out.append(_gate("frozen", NODATA, len(rows), None, "not frozen yet: freeze once the other gates pass"))
    else:
        same_n, same_h = frozen.get("rows") == len(rows), frozen.get("sha256") == freeze_hash(rows)
        if same_n and same_h:
            out.append(_gate("frozen", PASS, len(rows), None, f"{len(rows)} rows match the frozen hash {frozen['sha256'][:12]}"))
        else:
            out.append(_gate("frozen", FAIL, len(rows), None, f"the rows changed since the freeze ({len(rows)} rows now, {frozen.get('rows')} frozen" + ("" if same_h else "; the hash differs") + ")"))
    return out


# ---- command line ----------------------------------------------------------------------------------------------------------------------
def _confusion_lines(a: dict) -> list[str]:
    c = a["confusion"]
    lines = ["confusion (rows rater 1, columns rater 2):", "  " + " ".join(f"{x:>6}" for x in ("", *THREE))]
    lines += ["  " + " ".join([f"{r:>6}", *(f"{c[r][k]:>6}" for k in THREE)]) for r in THREE]
    return lines


def _read_frozen(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sha256"), str):
        raise ValueError(f"{path}: not a freeze file")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Labelled-call corpus: check, report, freeze.")
    ap.add_argument("command", choices=("check", "report", "freeze"))
    ap.add_argument("file", nargs="?", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--scan", dest="scan", action="store_true", default=None, help="scan for secret shapes and real hosts even outside the repository")
    ap.add_argument("--no-scan", dest="scan", action="store_false", help="do not scan, even inside the repository")
    ap.add_argument("--frozen", type=Path, default=DEFAULT_FROZEN)
    ap.add_argument("--write", action="store_true", help="freeze: write the hash file")
    args = ap.parse_args(argv)
    try:
        rows, problems = load_rows(args.file, args.scan)
        frozen = _read_frozen(args.frozen) if args.command != "check" else None
    except (OSError, ValueError) as e:
        print(f"cannot read: {e}")
        return 2
    for p in problems:
        print(p)
    if args.command == "check":
        print(f"{len(rows)} rows, {len(problems)} problem(s)")
        return 1 if problems else 0
    if problems:
        print(f"{len(problems)} problem(s): fix the file first")
        return 1
    a, gs = agreement(rows), gates(rows, frozen)
    if args.command == "freeze":
        return _freeze(args, rows, a, gs)
    print(f"gray rows: {a['n']} labelled by both raters, {a['pending']} pending; clusters={a['clusters']}; hard rows: {sum(hard_counts(rows).values())}")
    if a["n"]:
        for name, k in (("allow against not-allow", a["collapse"]), ("3 classes", a["three"])):
            print(f"kappa {_f(k['kappa'])} and agreement {_f(k['agreement'])} (chance {_f(k['chance'])}) on {name}")
        print(*_confusion_lines(a), sep="\n")
    for fam, c in hard_counts(rows).items():
        print(f"hard family {fam}: {c} row(s), counted, never scored for accuracy")
    for g in gs:
        print(f"{g['state']:>16}  {g['gate']:<13} n={g['n']} clusters={g['clusters']}  {g['detail']}")
    return 1 if any(g["state"] == FAIL for g in gs) else 0


def _freeze(args, rows: list[dict], a: dict, gs: list[dict]) -> int:
    need = [g for g in gs if g["gate"] != "frozen" and g["state"] != PASS]
    if need or a["pending"]:
        for g in need:
            print(f"not frozen: {g['gate']} is {g['state']}: {g['detail']}")
        if a["pending"]:
            print(f"not frozen: {a['pending']} gray row(s) are still pending")
        return 1
    h = freeze_hash(rows)
    record = {"rubric_version": rubric()["rubric_version"], "rows": len(rows), "sha256": h}
    print(f"sha256 {h} over {len(rows)} rows (rubric version {record['rubric_version']})")
    if not args.write:
        print("nothing written: add --write to save it")
        return 0
    if args.frozen.exists() and json.loads(args.frozen.read_text(encoding="utf-8")).get("sha256") != h:
        print(f"{args.frozen} holds another hash: a frozen set is not edited. If the rubric changed, bump its version, label again and remove that file by hand.")
        return 1
    args.frozen.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"written {args.frozen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
