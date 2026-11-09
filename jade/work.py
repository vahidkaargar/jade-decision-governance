"""Work items (ADR-09): work-items.jsonl is the source, BACKLOG.md is rendered from it."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from . import schemas

# The generated table starts at a line that is exactly the heading `## Items`. The hand-written text above it may quote the words in prose
# or in a code span (`## Items`); only a heading line counts, and the very first line of the file counts too.
ITEMS_HEADING = re.compile(r"^## Items[ \t]*$", re.M)
MARK = "\n## Items\n"
AGING = (("P2", "P1", 14), ("P3", "P2", 30))
LEASE_DAYS = 1   # an item `in_progress` with no history entry for more than this many days goes back to `open` (the history keeps dates, not times)


def load(path: Path) -> list[dict]:
    """The work items of a JSON Lines file, each validated against the contract: a hand edit that breaks the schema fails here, not later."""
    items = []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if line.strip():
            w = json.loads(line)
            try:
                schemas.validate("work-item", w)
            except Exception as e:  # noqa: BLE001 - jsonschema's error type is not part of this module's contract
                raise ValueError(f"{path}:{n}: not a valid work item ({str(e).splitlines()[0]})") from None
            items.append(w)
    return items


def save(path: Path, items: list[dict]) -> None:
    for w in items:
        schemas.validate("work-item", w)
    Path(path).write_text("".join(json.dumps(w, ensure_ascii=False) + "\n" for w in items))


def age(items: list[dict], today: date | None = None) -> list[str]:
    """P2 -> P1 after 14 idle days, P3 -> P2 after 30 (docs/07 section 2). Only open items age. An item that has been `in_progress` for more
    than a day with no history entry loses its lease and goes back to `open`; the change is recorded in its history. Returns the ids changed."""
    today = today or date.today()
    changed = []
    for w in items:
        idle = (today - date.fromisoformat(w["updated_at"][:10])).days
        if w["status"] == "in_progress" and idle > LEASE_DAYS:
            w["status"], w["updated_at"] = "open", today.isoformat()
            w["history"].append({"at": today.isoformat(), "change": f"lease expired: in_progress with no update for {idle} days, back to open"})
            changed.append(w["id"])
            continue
        if w["status"] not in ("open", "blocked", "waiting_human"):
            continue
        for src, dst, days in AGING:
            if w["priority"] == src and idle >= days:
                w["priority"], w["updated_at"] = dst, today.isoformat()
                w["history"].append({"at": today.isoformat(), "change": f"aged {src} -> {dst} after {idle} idle days"})
                changed.append(w["id"])
                break
    return changed


def update(items: list[dict], wid: str, *, status: str | None = None, note: str = "", next_action: str | None = None,
           trigger: str | None = None, today: date | None = None) -> dict:
    today = (today or date.today()).isoformat()
    w = next((x for x in items if x["id"] == wid), None)
    if w is None:
        raise KeyError(wid)
    parts = []
    if status and status != w["status"]:
        parts.append(f"status {w['status']} -> {status}")
        w["status"] = status
    if next_action:
        w["next_action"] = next_action
    if trigger:
        w["retry_or_revisit_trigger"] = trigger
    w["updated_at"] = today
    w["history"].append({"at": today, "change": "; ".join(parts + ([note] if note else [])) or "touched"})
    # unblock dependants whose dependencies are all done
    done = {x["id"] for x in items if x["status"] == "done"}
    for x in items:
        if x["status"] == "blocked" and x["dependencies"] and set(x["dependencies"]) <= done:
            x["status"], x["updated_at"] = "open", today
            x["history"].append({"at": today, "change": "unblocked: dependencies done"})
    return w


def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def table(items: list[dict]) -> str:
    rows = ["| Id | P | Status | Owner | Phase | Title | Depends on | Trigger | Next action |", "|----|---|--------|-------|-------|-------|------------|---------|-------------|"]
    order = {"in_progress": 0, "open": 1, "waiting_human": 2, "blocked": 3, "done": 4, "dropped": 5}
    for w in sorted(items, key=lambda w: (order.get(w["status"], 9), w["priority"], w["id"])):
        rows.append("| " + " | ".join(_cell(x) for x in (
            w["id"], w["priority"], w["status"], w["owner"], w.get("phase", ""), w.get("title", ""), ", ".join(w["dependencies"]) or "—",
            w["retry_or_revisit_trigger"], w["next_action"])) + " |")
    return "\n".join(rows)


def render(jsonl: Path, backlog: Path, today: date | None = None) -> str:
    """Rewrite everything from the `## Items` heading down; the hand-written part above it is kept."""
    items = load(jsonl)
    head = ITEMS_HEADING.split(backlog.read_text(), maxsplit=1)[0] if backlog.exists() else "# Backlog\n\n"
    text = (head.rstrip("\n") + f"\n{MARK}\n" + table(items) +
            f"\n\nAging: P2 → P1 after 14 idle days; P3 → P2 after 30. Rendered by `jade work render` on {(today or date.today()).isoformat()}.\n")
    backlog.write_text(text)
    return text
