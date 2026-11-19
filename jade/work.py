"""Work items (ADR-09): work-items.jsonl is the source, BACKLOG.md is rendered from it."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import stat
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

from . import schemas

try:
    import fcntl
except ImportError:   # Windows
    fcntl = None

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


LOCK_WAIT_S = 10.0   # how long a writer waits for another writer before it gives up with a TimeoutError
_held = threading.local()   # the lock files this thread holds: `locked` can be entered again (`jade work` holds it over load and save, and save asks for it too)


def lock_path(path: Path) -> Path:
    """The lock is a file next to the data file, never the data file itself: `os.replace` swaps the data file's inode, and a lock on the old inode would guard nothing."""
    p = Path(os.path.realpath(path))
    return p.with_name(p.name + ".lock")


@contextlib.contextmanager
def locked(path: Path):
    """Hold an exclusive lock on `path` (a read, change and save of a work file belong inside one). It waits up to LOCK_WAIT_S and then raises TimeoutError.
    The same thread may enter it again. Where `fcntl` does not exist (Windows) there is no lock and the atomic write is all that protects the file."""
    key = str(lock_path(path))
    mine = getattr(_held, "keys", None)
    if mine is None:
        mine = _held.keys = set()
    if key in mine or fcntl is None:
        yield
        return
    fd = os.open(key, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"another jade command holds the lock on {path} (waited {LOCK_WAIT_S:g}s); try again") from None
                time.sleep(0.05)
        mine.add(key)
        try:
            yield
        finally:
            mine.discard(key)
    finally:
        os.close(fd)   # closing the descriptor drops the lock


def atomic_write(path: Path, text: str) -> None:
    """Write the whole file or none of it: a temp file in the same directory, flushed to disk, given the old file's mode, then renamed over it. A failure
    removes the temp file and leaves the old file as it was. A symlink is followed, so the link stays a link."""
    target = Path(os.path.realpath(path))
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.jade-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def save(path: Path, items: list[dict]) -> None:
    """Validate every item, then write the file atomically under the lock. A reader sees the old file or the new one, never a cut-off line."""
    for w in items:
        schemas.validate("work-item", w)
    with locked(path):
        atomic_write(path, "".join(json.dumps(w, ensure_ascii=False) + "\n" for w in items))


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


ACTIVE = ("open", "in_progress", "waiting_human", "blocked")   # a finished item never blocks a new one with the same title
ID_PATTERN = re.compile(r"^W-([0-9]{2,4})$")
ID_MAX = 9999   # schemas/work-item.schema.json: `^W-[0-9]{2,4}$`


class DuplicateItem(ValueError):
    """An active item with the same normalised title and target exists (W-51); `existing` is its id."""

    def __init__(self, existing: str, title: str):
        super().__init__(f"{existing} already tracks {title!r} (same normalised title and target); update it with `jade work set {existing}`")
        self.existing = existing


def normalise_title(title: str) -> str:
    """Lower case, every run of non-alphanumerics a single space: `Fix  the Gate!` and `fix the gate` are one title."""
    return " ".join(re.sub(r"[\W_]+", " ", str(title).casefold()).split())


def _key(w: dict) -> tuple[str, frozenset]:
    """Title and target. The work item has no target field: its evidence references are the target (what it is about)."""
    return normalise_title(w.get("title", "")), frozenset(w.get("evidence_refs", []))


def _find_duplicate(items: list[dict], title: str, evidence_refs) -> dict | None:
    key = (normalise_title(title), frozenset(evidence_refs))
    return next((x for x in items if x["status"] in ACTIVE and x.get("title") and _key(x) == key), None)


def dedupe(items: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """(items to keep, {duplicate id: id it duplicates}). Of each group of active items with one normalised title and target the first stays;
    finished items are never duplicates. Nothing is changed: the caller decides what to do with the skipped ones."""
    seen: dict[tuple, str] = {}
    keep, skipped = [], {}
    for w in items:
        k = _key(w)
        if w["status"] in ACTIVE and w.get("title"):
            if k in seen:
                skipped[w["id"]] = seen[k]
                continue
            seen[k] = w["id"]
        keep.append(w)
    return keep, skipped


def add(items: list[dict], *, title: str, impact: str, rationale: str, next_action: str, trigger: str, type: str = "followup",  # noqa: A002
        priority: str = "P2", owner: str = "agent", dependencies=(), evidence_refs=(), parent: str | None = None, phase: int | None = None,
        today: date | None = None) -> dict:
    """Append a new open item with the next free id. Validates against the contract before anything changes, refuses a duplicate of an
    active item (`DuplicateItem`) and a dependency or parent that does not exist (`ValueError`)."""
    today_s = (today or date.today()).isoformat()
    title = " ".join(str(title).split())
    if not title:
        raise ValueError("an item needs a title")
    dependencies, evidence_refs = list(dependencies), list(evidence_refs)
    ids = {x["id"] for x in items}
    for ref in [*dependencies, *([parent] if parent else [])]:
        if ref not in ids:
            raise ValueError(f"{ref} is not a work item in this file")
    dup = _find_duplicate(items, title, evidence_refs)
    if dup:
        raise DuplicateItem(dup["id"], title)
    n = max((int(m.group(1)) for x in items if (m := ID_PATTERN.match(x["id"]))), default=0) + 1
    if n > ID_MAX:
        raise ValueError(f"no free id: the contract allows W-{ID_MAX} at most")
    w = {"schema_version": "1.0.0", "id": f"W-{n:02d}", "parent": parent, "title": title, "type": type, "priority": priority, "status": "open", "owner": owner,
         "dependencies": dependencies, "impact": impact, "rationale": rationale, "evidence_refs": evidence_refs, "next_action": next_action,
         "retry_or_revisit_trigger": trigger, "created_at": today_s, "updated_at": today_s, "history": [{"at": today_s, "change": "created"}]}
    if phase is not None:
        w["phase"] = phase
    try:
        schemas.validate("work-item", w)
    except Exception as e:  # noqa: BLE001 - jsonschema's error type is not part of this module's contract
        where = "/".join(str(p) for p in getattr(e, "absolute_path", ()))
        raise ValueError(f"not a valid work item ({where + ': ' if where else ''}{str(e).splitlines()[0]})") from None
    items.append(w)
    return w


def ready(items: list[dict], closed: str | None = None) -> list[dict]:
    """Open items that wait on other items, all of which are done. With `closed`, only those that name that item (what it just freed)."""
    done = {x["id"] for x in items if x["status"] == "done"}
    return [w for w in items if w["status"] == "open" and w["dependencies"] and set(w["dependencies"]) <= done
            and (closed is None or closed in w["dependencies"])]


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
    atomic_write(backlog, text)   # a cut-off BACKLOG.md would lose the hand-written head; two renders of one source give one text, so no lock
    return text
