"""Work items (ADR-09): work-items.jsonl is the source, BACKLOG.md is rendered from it."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import schemas

MARK = "## Items"
AGING = (("P2", "P1", 14), ("P3", "P2", 30))


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def save(path: Path, items: list[dict]) -> None:
    for w in items:
        schemas.validate("work-item", w)
    Path(path).write_text("".join(json.dumps(w, ensure_ascii=False) + "\n" for w in items))


def age(items: list[dict], today: date | None = None) -> list[str]:
    """P2 -> P1 after 14 idle days, P3 -> P2 after 30. Only open items age."""
    today = today or date.today()
    changed = []
    for w in items:
        if w["status"] not in ("open", "blocked", "waiting_human"):
            continue
        idle = (today - date.fromisoformat(w["updated_at"][:10])).days
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
    head = backlog.read_text().split(MARK)[0] if backlog.exists() else "# Backlog\n\n"
    text = (head.rstrip("\n") + f"\n\n{MARK}\n\n" + table(items) +
            f"\n\nAging: P2 → P1 after 14 idle days; P3 → P2 after 30. Rendered by `jade work render` on {(today or date.today()).isoformat()}.\n")
    backlog.write_text(text)
    return text
