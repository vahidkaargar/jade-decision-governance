"""Version reporting and the human-run upgrade (REL-1).

`jade --version` / `jade version` say which code runs. `jade upgrade --check` asks GitHub for the latest release (read-only, any caller). `jade upgrade`
replaces the installed tool with a release of the one repository named below, and is human-only like `jade hooks install`: it changes the code that
decides every gate call, so SI-09 blocks it for an agent and `apply` refuses without an interactive human terminal.

The upgrade process never opens the JADE state directory or the configuration. Its only side effects are the `uv tool install` it runs and the `jade check`
it then runs from the new install (offline); that command is the first of the new code, so it opens the ledger, may apply an additive migration and records `last_check`. Nothing here is read from an untrusted response except a
release tag and changelog headings, and both are reduced to a fixed shape before they are used or printed."""

from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import jade

from . import ops, paths, util

REPO = "vahidkaargar/jade-decision-governance"
REPO_URL = f"https://github.com/{REPO}"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RAW_CHANGELOG = f"https://raw.githubusercontent.com/{REPO}/{{tag}}/CHANGELOG.md"
DIST_NAME = "jade-decision-governance"
HTTP_TIMEOUT_S = 5
MAX_BYTES = 1_000_000
INSTALL_TIMEOUT_S = 600
CHECK_TIMEOUT_S = 120
MAX_CHANGES = 50
MAX_FILES = 100          # file lines `jade upgrade` prints before it says how many more there are
COMPARE_LIMIT = 300      # GitHub's compare answer lists at most this many files
WATCHED_DIRS = ("jade/", "policies/", "registry/", "scripts/")   # what decides the gate: a change here is what the human reads before typing yes
LEDGER_SCHEMA_VERSION = "1.0.0"   # `schema_version` of every record the ledger stores (schemas/*.schema.json); a test pins it to the shipped examples
HOOK_ROWS = ("hooks installed", "hook wiring", "pretool matcher")   # rows of `jade check` that say the installed hooks differ from what this version expects

REF_RE = re.compile(r"v?[0-9]+(?:\.[0-9]+){2}|[0-9a-f]{40}")
TAG_RE = re.compile(r"v?[0-9]+(?:\.[0-9]+){2}")
_HEADING = re.compile(r"^##[ \t]+\[([0-9][0-9A-Za-z.+-]{0,31})\](?:[ \t]+-[ \t]+([0-9]{4}-[0-9]{2}-[0-9]{2}))?", re.M)
_FAIL_ROW = re.compile(r"^FAIL[ \t]+([^:\n]+): ", re.M)
_SAFE_PATH = re.compile(r"[A-Za-z0-9._/@+-]{1,200}")
_STATUS = {"added", "modified", "removed", "renamed", "copied", "changed"}
_ALLOWED_HOSTS = frozenset({"api.github.com", "github.com", "codeload.github.com", "raw.githubusercontent.com"})


class UpgradeError(ValueError):
    """A request that cannot be carried out as given (a ref that is not a release or a commit). Nothing was run."""


class FetchError(Exception):
    """A typed network failure: `kind` is one of bad_url, offline, timeout, not_found, rate_limited, http, too_large."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


def _clean(text, n: int = 200) -> str:
    """Printable ASCII only: a terminal must not obey escape sequences that arrived over the network or from an error text."""
    return re.sub(r"[^\x20-\x7e]", "?", str(text))[:n]


# ---- version reporting -----------------------------------------------------------------------------------------------
def _direct_url_commit() -> str | None:
    """The commit uv recorded when it installed this package from git (PEP 610 `direct_url.json`), short form. None for anything else. Never raises."""
    try:
        doc = json.loads(importlib.metadata.distribution(DIST_NAME).read_text("direct_url.json"))
        commit = doc["vcs_info"]["commit_id"]
    except Exception:  # noqa: BLE001 - provenance is best effort: a missing, odd or unreadable record is "not known"
        return None
    return commit[:12] if isinstance(commit, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) else None


def version_info() -> dict:
    """The code version, the commit when known (a checkout's HEAD, else the commit uv recorded for a git install), the ledger schema version and the
    policy_version. Reads no ledger and creates nothing."""
    commit = paths.code_commit()
    source = "checkout" if commit else None
    if not commit:
        commit = _direct_url_commit()
        source = "install record" if commit else None
    try:
        policy_version = paths.policy_version()
    except Exception:  # noqa: BLE001 - a version report must work when a policy file does not
        policy_version = "unknown"
    return {"version": jade.__version__, "commit": commit, "commit_source": source, "schema_version": LEDGER_SCHEMA_VERSION, "policy_version": policy_version}


def version_text(info: dict) -> str:
    commit = f"{info['commit']} ({info['commit_source']})" if info["commit"] else "unknown"
    return f"jade {info['version']}\ncommit: {commit}\nledger schema: {info['schema_version']}\npolicy_version: {info['policy_version']}\n"


def cli_version(a) -> int:
    info = version_info()
    print(json.dumps(info, indent=1) if a.json else version_text(info), end="" if not a.json else "\n")
    return 0


# ---- the network (stdlib only, https only, GitHub only) -----------------------------------------------------------------
def _allowed_url(url: str) -> bool:
    try:
        u = urlsplit(url)
        return u.scheme == "https" and u.port in (None, 443) and u.hostname is not None and (u.hostname.lower() in _ALLOWED_HOSTS or u.hostname.lower().endswith(".githubusercontent.com"))
    except ValueError:
        return False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect stays on https and on GitHub's own hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            raise urllib.error.URLError("redirect to an address that is not https on github.com refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_get(url: str, timeout: float = HTTP_TIMEOUT_S, limit: int = MAX_BYTES) -> bytes:
    """GET `url` and return the body, or raise FetchError. No token, no cookie, a 5 s timeout, at most `limit` bytes."""
    if not _allowed_url(url):
        raise FetchError("bad_url", "refused: not an https address on github.com")
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": f"jade-upgrade/{jade.__version__}"})
    try:
        with urllib.request.build_opener(_SafeRedirect()).open(req, timeout=timeout) as resp:
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as e:
        kind = {404: "not_found", 403: "rate_limited", 429: "rate_limited"}.get(e.code, "http")
        raise FetchError(kind, f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        slow = isinstance(e.reason, (TimeoutError, socket.timeout))
        raise FetchError("timeout" if slow else "offline", _clean(e.reason)) from None
    except TimeoutError:
        raise FetchError("timeout", f"no answer in {timeout:g} s") from None
    except (OSError, ValueError) as e:   # connection reset, TLS failure, a malformed redirect
        raise FetchError("offline", type(e).__name__) from None
    if len(data) > limit:
        raise FetchError("too_large", f"more than {limit} bytes")
    return data


@dataclass
class Latest:
    ok: bool
    tag: str | None = None
    version: str | None = None
    url: str | None = None
    error_kind: str | None = None
    detail: str = ""


def latest_release() -> Latest:
    """The latest published release of the repository. Never raises: every failure is a typed result (offline, timeout, rate_limited, no_release,
    bad_response, unexpected). Only `tag_name` is read, and only when it is a plain version; the page URL is built from it."""
    try:
        try:
            doc = json.loads(_http_get(API_LATEST).decode("utf-8"))
        except FetchError as e:
            return Latest(False, error_kind="no_release" if e.kind == "not_found" else e.kind, detail=e.detail)
        except (ValueError, RecursionError):
            return Latest(False, error_kind="bad_response", detail="the answer is not JSON")
        tag = doc.get("tag_name") if isinstance(doc, dict) else None
        if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
            return Latest(False, error_kind="bad_response", detail="the release has no tag of the form vX.Y.Z")
        return Latest(True, tag=tag, version=tag.lstrip("v"), url=f"{REPO_URL}/releases/tag/{tag}")
    except Exception as e:  # noqa: BLE001 - this is the one place that promises not to raise
        return Latest(False, error_kind="unexpected", detail=type(e).__name__)


def changelog_between(text: str, installed: str, latest: str) -> list[str]:
    """`<version> - <date>` for each CHANGELOG heading newer than `installed` and not newer than `latest`, newest first. Reduced to a version and a date:
    nothing else of a heading line is kept."""
    lo, hi = ops._version_tuple(installed), ops._version_tuple(latest)
    found = []
    for m in _HEADING.finditer(text):
        t = ops._version_tuple(m.group(1))
        if t is not None and lo is not None and hi is not None and lo < t <= hi:
            found.append((t, f"{m.group(1)} - {m.group(2)}" if m.group(2) else m.group(1)))
    found.sort(key=lambda x: x[0], reverse=True)
    return [label for _, label in found][:MAX_CHANGES]


@dataclass
class CheckResult:
    ok: bool
    installed: str = ""
    latest: str | None = None
    tag: str | None = None
    url: str | None = None
    update_available: bool = False
    changes: list[str] = field(default_factory=list)
    changes_error: str | None = None
    error_kind: str | None = None
    detail: str = ""


def check() -> CheckResult:
    """Installed version against the latest release, plus the CHANGELOG headings in between. Read-only; never raises."""
    installed = jade.__version__
    latest = latest_release()
    if not latest.ok:
        return CheckResult(False, installed=installed, error_kind=latest.error_kind, detail=latest.detail)
    a, b = ops._version_tuple(installed), ops._version_tuple(latest.version)
    available = (b > a) if a is not None and b is not None else installed != latest.version
    res = CheckResult(True, installed, latest.version, latest.tag, latest.url, available)
    if available:
        try:
            res.changes = changelog_between(_http_get(RAW_CHANGELOG.format(tag=latest.tag)).decode("utf-8", "replace"), installed, latest.version)
        except FetchError as e:
            res.changes_error = e.kind
        except Exception as e:  # noqa: BLE001
            res.changes_error = type(e).__name__
    return res


def _print_check(r: CheckResult) -> None:
    info = version_info()
    where = f" ({info['commit']})" if info["commit"] else ""
    print(f"installed: {r.installed}{where}")
    print(f"latest:    {r.latest} ({r.tag}) {r.url}")
    if not r.update_available:
        print("up to date")
        return
    print("update available")
    for c in r.changes:
        v, _, d = c.partition(" - ")
        print(f"## [{v}]" + (f" - {d}" if d else ""))
    if r.changes_error:
        print(f"(CHANGELOG not available: {r.changes_error})")
    print(f"install it yourself, in a terminal: jade upgrade   (or jade upgrade --ref {r.tag})")


# ---- the upgrade ---------------------------------------------------------------------------------------------------
def validate_ref(ref) -> str:
    """A release tag (`v1.2.3`; a bare `1.2.3` is read as the tag `v1.2.3`, release tags always carry the v) or a full 40-hex commit. Nothing else
    reaches the command line of `uv`."""
    if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
        raise UpgradeError(f"invalid ref {_clean(ref, 40)!r}: use a release tag (vX.Y.Z) or a full 40-character commit hash, not a branch name")
    return "v" + ref if TAG_RE.fullmatch(ref) and not ref.startswith("v") else ref


def install_argv(ref: str) -> list[str]:
    return ["uv", "tool", "install", "--reinstall", f"git+{REPO_URL}@{validate_ref(ref)}"]


def _run(argv: list[str], timeout: float = INSTALL_TIMEOUT_S, capture: bool = False) -> tuple[int, str]:
    """Run an argv list (never through a shell). `capture` returns stdout and stderr as text; otherwise the program writes to the terminal."""
    kw = {"capture_output": True, "text": True} if capture else {}
    r = subprocess.run(argv, check=False, timeout=timeout, stdin=subprocess.DEVNULL, **kw)  # noqa: S603 - fixed program and a validated ref
    return r.returncode, ((r.stdout or "") + (r.stderr or "")) if capture else ""


@dataclass
class UpgradeResult:
    ok: bool
    ref: str | None = None
    install_rc: int | None = None
    check_rc: int | None = None
    failed_rows: list[str] = field(default_factory=list)
    reinstall_hooks: bool = False
    next_steps: list[str] = field(default_factory=list)
    check_output: str = ""
    error: str | None = None


COMPARE_MAX_BYTES = 25_000_000   # GitHub's compare answer carries every file's patch and the commits: a release step of a few versions is many megabytes


def _compare(base: str, head: str) -> dict:
    """Both ends are already checked: the ref by validate_ref, the installed commit by the hex pattern."""
    return json.loads(_http_get(f"https://api.github.com/repos/{REPO}/compare/{base}...{head}", limit=COMPARE_MAX_BYTES).decode("utf-8"))


def changed_files(ref: str) -> tuple[list[str], str]:
    """The files under jade/, policies/, registry/ and scripts/ that differ between the installed commit and `ref` (GitHub's compare answer), as printable lines, plus a
    note (what is not listed, or why nothing could be). Each path is checked against a plain-character pattern, so nothing a terminal could obey is printed; a file whose name
    fails the check is counted, never dropped. A ref that is behind the installed commit is a rollback: the files that return to their older state are listed. Never raises."""
    base = _direct_url_commit() or paths.code_commit() or ""
    if not re.fullmatch(r"[0-9a-f]{7,40}", base):
        return [], "the installed commit is not known, so the changed files cannot be listed"
    notes: list[str] = []
    flip = False
    try:
        ref = validate_ref(ref)
        doc = _compare(base, ref)
        status = doc.get("status") if isinstance(doc, dict) else None
        if status == "behind":   # `ref` is an ancestor of what is installed: a rollback. The forward compare is empty; the reverse one names what changes back.
            doc, flip = _compare(ref, base), True
            notes.append("this is a rollback: the files below return to their older state")
        elif status == "diverged":
            notes.append("the release and the installed commit have diverged: the list shows what the release has that the installed commit lacks, and may miss what is dropped")
    except FetchError as e:
        return [], f"cannot list the changed files ({e.kind}: {_clean(e.detail, 80)})"
    except (UpgradeError, ValueError, RecursionError):
        return [], "cannot list the changed files (the answer could not be read)"
    files = doc.get("files") if isinstance(doc, dict) else None
    if not isinstance(files, list):
        return [], "cannot list the changed files (the answer has no file list)"
    lines, other, unsafe = [], 0, 0
    flipped = {"added": "removed", "removed": "added"}
    for item in files:
        if not isinstance(item, dict):
            continue
        name, old = item.get("filename"), item.get("previous_filename")
        names = [n for n in (name, old) if n is not None]
        if not names or any(not isinstance(n, str) or not _SAFE_PATH.fullmatch(n) for n in names):
            unsafe += 1
            continue
        if not any(n.startswith(WATCHED_DIRS) for n in names):
            other += 1
            continue
        status = item.get("status") if item.get("status") in _STATUS else "changed"
        status = flipped.get(status, status) if flip else status
        add, dele = (n if isinstance(n, int) and not isinstance(n, bool) else 0 for n in (item.get("additions"), item.get("deletions")))
        shown = f"{name} (from {old})" if old else name
        lines.append(f"{status:<8}  {shown} (+{add} -{dele})")
    if other:
        notes.append(f"{other} other file(s) outside {', '.join(WATCHED_DIRS)} changed and are not listed")
    if unsafe:
        notes.append(f"{unsafe} file(s) with names that cannot be shown safely changed: read the diff on GitHub before you type yes")
    if len(lines) > MAX_FILES:
        notes.append(f"{len(lines) - MAX_FILES} more file(s) not shown")
        lines = lines[:MAX_FILES]
    if len(files) >= COMPARE_LIMIT:
        notes.append(f"GitHub lists at most {COMPARE_LIMIT} files, so there may be more")
    if not lines and not notes:
        notes.append(f"no file under {', '.join(WATCHED_DIRS)} changed")
    return lines, "; ".join(notes)


def apply(ref: str | None = None, stdin_isatty: bool | None = None, say: Callable[[str], None] | None = None, confirm: Callable[[], bool] | None = None) -> UpgradeResult:
    """Install `ref` (a release tag or a commit; the latest release when None), then run `jade check` from the new install. Human only. With `confirm` (the command passes
    one) the changed files are listed first and nothing is installed unless it returns True: the human reads what changes, then types `yes`."""
    say = say or (lambda _s: None)
    why = util.human_tty_refusal(stdin_isatty)
    if why:
        raise ops.HumanOnly(f"jade upgrade replaces the code that gates every agent action; a human must run it in a terminal ({why})")
    steps: list[str] = []
    if ref is None:
        latest = latest_release()
        if not latest.ok:
            return UpgradeResult(False, error=f"cannot find the latest release ({latest.error_kind}: {_clean(latest.detail)}); name one with --ref vX.Y.Z")
        a, b = ops._version_tuple(jade.__version__), ops._version_tuple(latest.version)
        if a is not None and b is not None and b <= a:
            return UpgradeResult(True, ref=latest.tag, next_steps=[f"up to date: installed {jade.__version__} is not older than the latest release {latest.tag}; nothing installed (--ref reinstalls or rolls back)"])
        ref = latest.tag
    else:
        ref = validate_ref(ref)
        a, b = ops._version_tuple(jade.__version__), ops._version_tuple(ref.removeprefix("v")) if TAG_RE.fullmatch(ref) else None
        if a is not None and b is not None and b < a:
            steps.append(f"{ref} is older than the installed {jade.__version__}: this is a rollback")
            say(f"{ref} is older than the installed {jade.__version__}: this is a rollback")
    if not shutil.which("uv"):
        return UpgradeResult(False, ref=ref, error="uv is not on PATH: install uv first (https://docs.astral.sh/uv/)")
    if confirm is not None:
        lines, note = changed_files(ref)
        say(f"Files that change under the installed commit ({', '.join(WATCHED_DIRS)}):" if lines else "Files that change: none could be listed.")
        for line in lines:
            say(f"  {line}")
        if note:
            say(f"  ({note})")
        if not lines:
            say("  Without this list you would install code that decides every gate call without reading what changed.")
        if not confirm():
            return UpgradeResult(False, ref=ref, error="not confirmed: you did not type yes, so nothing was installed")
    argv = install_argv(ref)
    say(f"running: {' '.join(argv)}")
    try:
        rc, _ = _run(argv, timeout=INSTALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return UpgradeResult(False, ref=ref, error=f"uv tool install timed out after {INSTALL_TIMEOUT_S} s; the installed tool may be unchanged: run jade --version")
    except (OSError, subprocess.SubprocessError) as e:
        return UpgradeResult(False, ref=ref, error=f"uv could not be started ({type(e).__name__})")
    if rc != 0:
        return UpgradeResult(False, ref=ref, install_rc=rc, error=f"uv tool install exited {rc}; the installed tool is unchanged unless uv said otherwise")
    check_argv = [ops.jade_bin(), "check", "--offline"]
    say(f"running: {' '.join(check_argv)}")
    try:
        crc, out = _run(check_argv, timeout=CHECK_TIMEOUT_S, capture=True)
    except (OSError, subprocess.SubprocessError) as e:
        return UpgradeResult(False, ref=ref, install_rc=0, next_steps=[*steps, "run `jade check` yourself to see the state of the new install"],
                             error=f"the new install was made, but `jade check` could not run ({type(e).__name__})")
    failed = [n.strip() for n in _FAIL_ROW.findall(out)]
    reinstall = any(n in HOOK_ROWS for n in failed)
    if reinstall:
        steps += ["re-run `jade hooks install` in a terminal: the installed hooks or matcher differ from what this version expects",
                  "then open a new Claude Code session (hooks are read at session start)"]
    elif not failed and crc == 0:
        steps.append("hooks: no change needed (the hook rows of `jade check` are ok)")
    others = [n for n in failed if n not in HOOK_ROWS]
    if others:
        steps.append(f"`jade check` reports failing rows: {', '.join(others)}; read them with `jade check`")
    error = None
    if crc != 0 and not failed:
        error = f"`jade check` from the new install exited {crc} without a report"
        steps.append("run `jade check` yourself to see why")
    steps.append("run `jade check` (without --offline) for the live provider canary; `jade version` shows what runs now")
    return UpgradeResult(error is None, ref, 0, crc, failed, reinstall, steps, out, error)


def _confirm_yes() -> bool:
    try:
        return input("Type yes to install this release; anything else stops: ").strip() == "yes"
    except (EOFError, KeyboardInterrupt):
        return False


def cli_upgrade(a) -> int:
    if a.check:
        r = check()
        if not r.ok:
            print(f"jade: cannot check for updates ({r.error_kind}): {_clean(r.detail)}", file=sys.stderr)
            return 1
        _print_check(r)
        return 0
    try:
        res = apply(a.ref, say=print, confirm=_confirm_yes)
    except (ops.HumanOnly, UpgradeError) as e:
        print(f"jade: {e}", file=sys.stderr)
        return 1
    if res.check_output:
        print(res.check_output, end="" if res.check_output.endswith("\n") else "\n")
    if res.error:
        print(f"jade: {res.error}", file=sys.stderr)
    for s in res.next_steps:
        print(f"next: {s}")
    return 0 if res.ok else 1
