"""The human-only classes HO-delete, HO-money and HO-release (W-98b, docs/12 section 4).

A call in one of these classes is one a person decides every time: no memory of past answers and no model may decide it (0.6.0), and at a level (`jade level`) the gate raises
the call to high so that a person is asked. The classes name what the gate can read in a command line or in the name of a tool. Nothing here changes a call's rule or level.

Every pattern is paired with a corpus of commands it must not match (`tests/acceptance/test_human_only_w98b.py`): a false positive costs a click for a harmless command, a false
negative lets a delete, a payment or a release past the person. Known residuals (docs/05): a program in another language that does these things (`subprocess.run(["rm", ...])`,
an SDK call), a tool the name lists do not know, a command assembled at run time, a script in a language the segmenter does not read.

This module is pure: it imports nothing from `policy`, so the caller passes the segment with its launcher prefixes already removed.
"""

from __future__ import annotations

import os
import re
import shlex

HO_DELETE, HO_MONEY, HO_RELEASE = "HO-delete", "HO-money", "HO-release"
ALL = (HO_DELETE, HO_MONEY, HO_RELEASE)

# ------------------------------------------------------------------ shell words

_SAFE_DIRS = {"node_modules", "dist", "build", "target", "coverage", "htmlcov", "out-tsc", "__pycache__", ".venv", "venv", ".tox", ".nox", ".eggs", ".cache", ".next", ".nuxt",
              ".turbo", ".parcel-cache", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".gradle", ".DS_Store"}
_SAFE_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/", "$TMPDIR", "${TMPDIR", "/dev/shm/")
_SAFE_SUFFIXES = (".pyc", ".pyo", ".egg-info", ".log")
_RECURSIVE = re.compile(r"^-[A-Za-z]*[rR][A-Za-z]*$")


def _tokens(bare: str) -> list[str]:
    try:
        return shlex.split(bare)
    except ValueError:
        return bare.split()


def _program(toks: list[str]) -> tuple[str, list[str]]:
    """The command word (no directory) and its arguments, past `sudo` and `VAR=value` words."""
    i = 0
    while i < len(toks) and (toks[i] in ("sudo", "doas", "command", "exec", "nohup", "time") or re.fullmatch(r"[A-Za-z_]\w*=.*", toks[i])):
        i += 1
    if i >= len(toks):
        return "", []
    return toks[i].rsplit("/", 1)[-1], toks[i + 1:]


def _flags(args: list[str]) -> list[str]:
    out = []
    for a in args:
        if a == "--":
            break
        if a.startswith("-"):
            out.append(a)
    return out


def _operands(args: list[str]) -> list[str]:
    out, rest = [], False
    for a in args:
        if rest or not a.startswith("-"):
            out.append(a)
        elif a == "--":
            rest = True
    return out


def _safe_target(t: str) -> bool:
    """A build or cache directory, or anything under a temporary directory: removing it loses nothing that is not made again."""
    if not t or t.startswith("-"):
        return False
    t = t.rstrip("/")
    if ".." in t.split("/"):
        return False
    if t.startswith(_SAFE_PREFIXES) or t in ("$TMPDIR", "${TMPDIR}"):
        return True
    if t.startswith(("/", "~", "$")):   # an absolute or home path outside a temporary directory is the person's to confirm, whatever its last name is
        return False
    name = t.rsplit("/", 1)[-1]
    return name in _SAFE_DIRS or name.endswith(_SAFE_SUFFIXES)


# ------------------------------------------------------------------ git

_VERSION_TAG = re.compile(r"v?\d+\.\d+\.\d+(?:[-+.][\w.+-]*)?")


def _git_dir(start: str) -> str | None:
    """The git directory (where refs live) of the repository that holds `start`: the `.git` directory or, for a worktree or submodule, the common directory it points at."""
    d = os.path.abspath(os.path.expanduser(start or os.getcwd()))
    for _ in range(64):
        g = os.path.join(d, ".git")
        if os.path.isdir(g):
            return g
        if os.path.isfile(g):
            try:
                with open(g, encoding="utf-8", errors="replace") as fh:
                    first = fh.readline().strip()
                if first.startswith("gitdir:"):
                    gd = os.path.normpath(os.path.join(d, first[7:].strip()))
                    cf = os.path.join(gd, "commondir")
                    if os.path.isfile(cf):
                        with open(cf, encoding="utf-8", errors="replace") as fh:
                            return os.path.normpath(os.path.join(gd, fh.readline().strip()))
                    return gd
            except OSError:
                return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def _is_tag_here(name: str, dirs: tuple[str, ...], cwd: str) -> bool:
    """Is `name` a tag in the repository the command runs in (a loose ref or a line of packed-refs)? A file lookup: the hook has no time for a git process."""
    if not name or name.startswith(("-", "/", ".")) or ".." in name or "\x00" in name:
        return False
    for d in (dirs or (cwd,)):
        g = _git_dir(d or cwd)
        if not g:
            continue
        if os.path.isfile(os.path.join(g, "refs", "tags", *name.split("/"))):
            return True
        try:
            with open(os.path.join(g, "packed-refs"), encoding="utf-8", errors="replace") as fh:
                want = f" refs/tags/{name}"
                for line in fh:
                    if line.rstrip("\n").endswith(want) and not line.startswith("#"):
                        return True
        except OSError:
            continue
    return False


def _git(args: list[str], dirs: tuple[str, ...], cwd: str) -> set[str]:
    out: set[str] = set()
    if not args:
        return out
    sub, rest = args[0], args[1:]
    flags = _flags(rest)
    ops = _operands(rest)
    if sub == "push":
        if any(f in ("--force", "-f", "--mirror", "--delete", "-d") or f.startswith("--force-with-lease") or (re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", f) and not f.startswith("--"))
               for f in flags):
            out.add(HO_DELETE)
        refspecs = ops[1:] if len(ops) > 1 else []   # the first operand is the remote
        if any(r.startswith(":") or r.startswith("+") for r in refspecs):
            out.add(HO_DELETE)   # `:ref` deletes the remote ref, `+ref` forces it
        if "--tags" in flags or "--follow-tags" in flags or any("refs/tags/" in r for r in refspecs):
            out.add(HO_RELEASE)
        else:
            for r in refspecs:
                name = r.lstrip("+:").split(":", 1)[-1] if ":" in r.lstrip("+:") else r.lstrip("+:")
                if _VERSION_TAG.fullmatch(name) or _is_tag_here(name, dirs, cwd):
                    out.add(HO_RELEASE)
    elif sub == "clean":
        if any(re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*|--force", f) for f in flags) and not any(f == "--dry-run" or re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", f) for f in flags):
            out.add(HO_DELETE)
    elif sub == "reflog":
        if ops[:1] in (["expire"], ["delete"]):
            out.add(HO_DELETE)
    elif sub == "gc":
        if any(f.startswith("--prune=now") or f == "--prune=all" for f in flags):
            out.add(HO_DELETE)
    elif sub in ("filter-branch", "filter-repo"):
        out.add(HO_DELETE)
    return out


# ------------------------------------------------------------------ programs

_SQL_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "mongosh", "mongo", "redis-cli", "clickhouse-client", "cqlsh", "sqlcmd"}
_SQL_DESTROY = re.compile(r"(?i)\b(?:drop\s+(?:table|database|schema|index|user|view)|truncate(?:\s+table)?\s+\w|flush(?:all|db)\b|dropDatabase|delete\s+from\s+[\w.\"`]+\s*(?:;|$|['\"]))")
_REMOTE_DELETE_VERB = re.compile(r"^(?:delete|remove|terminate|destroy)(?:-[\w-]+)?$")
_DOCKER_PRUNE_AREAS = {"system", "volume", "image", "container", "network", "builder", "buildx"}
_MONEY_HOSTS = {"api.stripe.com", "api.paypal.com", "api-m.paypal.com", "api.squareup.com", "api.plaid.com", "api.adyen.com", "api.coinbase.com", "api.wise.com", "api.mercury.com",
                "api.revolut.com", "api.gocardless.com", "api.mollie.com", "api.razorpay.com", "api.braintreegateway.com"}
_URL_HOST = re.compile(r"^(?:https?://)?(?:[^/@\s]+@)?([A-Za-z0-9.-]+)(?::\d+)?(?:/|$)")
_STRIPE_MONEY = {"charges", "payment_intents", "refunds", "payouts", "transfers", "invoices", "invoiceitems", "subscriptions", "topups", "checkout", "payment_links", "credit_notes",
                 "disputes", "balance_transactions", "reversals", "transfer_reversals"}
_STRIPE_READ = {"list", "listen", "logs", "tail", "retrieve", "get", "events", "open", "samples", "status", "version", "config", "login", "trigger", "fixtures", "resources", "help"}
_LOCAL_REGISTRY = re.compile(r"^(?:localhost|127(?:\.\d{1,3}){3}|\[::1\]|::1|host\.docker\.internal|[\w-]+\.localhost|[\w-]+\.test)(?::\d+)?$")
_DEPLOY_PROD = {"vercel": ("--prod", "--production"), "netlify": ("--prod", "--prodIfUnlocked")}


def _remove(prog: str, args: list[str]) -> bool:
    ops = _operands(args)
    flags = _flags(args)
    if prog in ("rm", "rmdir"):
        recursive = any(_RECURSIVE.match(f) or f == "--recursive" for f in flags)
        return recursive and bool(ops) and not all(_safe_target(o) for o in ops)
    if prog in ("shred", "srm", "wipe"):
        return True
    if prog == "find":
        has = "-delete" in args or any(a in ("-exec", "-execdir") and i + 1 < len(args) and args[i + 1].rsplit("/", 1)[-1] in ("rm", "shred", "srm") for i, a in enumerate(args))
        starts = []
        for a in args:
            if a.startswith("-") or a in ("(", "!"):
                break
            starts.append(a)
        return has and not (starts and all(_safe_target(s) for s in starts))
    return False


def _methods(prog: str, args: list[str], ops: list[str]) -> set[str]:
    """The HTTP methods a curl, wget, httpie or `gh api` command line names (`-X DELETE`, `-XDELETE`, `--request=POST`, `--method POST`, httpie's first word)."""
    out: set[str] = set()
    for i, a in enumerate(args):
        if a in ("-X", "--request", "--method") and i + 1 < len(args):
            out.add(args[i + 1].upper())
        elif a.startswith("--request=") or a.startswith("--method="):
            out.add(a.split("=", 1)[1].upper())
        elif a.startswith("-X") and len(a) > 2:
            out.add(a[2:].upper())
    if prog in ("http", "https") and ops and re.fullmatch(r"[A-Z]{3,7}", ops[0]):
        out.add(ops[0])
    return out


def _sends_body(args: list[str]) -> bool:
    for a in args:
        if a in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form", "--json", "-T", "--upload-file", "--post-data", "--post-file", "--body-data") or \
                a.startswith(("--data=", "--data-raw=", "--data-binary=", "--json=", "--post-data=", "--post-file=", "--body-data=")):
            return True
    return False


def _hosts(args: list[str]) -> set[str]:
    out = set()
    for a in args:
        if a.startswith("-"):
            continue
        m = _URL_HOST.match(a)
        if m and ("://" in a or "." in a):
            out.add(m.group(1).lower())
    return out


def _docker_push_is_remote(ops: list[str]) -> bool:
    ref = next((o for o in reversed(ops) if not o.startswith("-")), "")
    if not ref:
        return False
    first = ref.split("/", 1)[0] if "/" in ref else ""
    return not (first and _LOCAL_REGISTRY.match(first))


def _program_classes(prog: str, args: list[str], dirs: tuple[str, ...], cwd: str) -> set[str]:
    out: set[str] = set()
    flags = _flags(args)
    ops = _operands(args)
    if prog == "git":
        return _git(args, dirs, cwd)
    if _remove(prog, args):
        out.add(HO_DELETE)
    dry = any(f in ("--dry-run", "-n") or (re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", f) and prog in ("rsync",)) for f in flags)
    # ---- delete
    if prog == "rsync" and not dry and any(f == "--delete" or f.startswith("--delete-") and f != "--delete-excluded" for f in flags):
        out.add(HO_DELETE)
    if prog in ("dropdb", "dropuser") or prog.startswith("mkfs"):
        out.add(HO_DELETE)
    if prog == "dd" and any(a.startswith("of=/dev/") for a in args):
        out.add(HO_DELETE)
    if prog == "diskutil" and ops and re.match(r"(?i)(?:erase\w*|partitiondisk|secureerase|zerodisk|reformat|apfs)$", ops[0]) and not (ops[0].lower() == "apfs" and "delete" not in " ".join(ops).lower()):
        out.add(HO_DELETE)
    if prog in ("docker", "podman") and ops:
        if ops[0] in _DOCKER_PRUNE_AREAS and "prune" in ops[1:2]:
            out.add(HO_DELETE)
        if ops[:2] == ["volume", "rm"] or ops[:2] == ["volume", "remove"]:
            out.add(HO_DELETE)
    if prog == "kubectl" and "delete" in ops[:3]:   # `kubectl -n x delete pod`: an option value is an operand here
        out.add(HO_DELETE)
    if prog == "helm" and ("uninstall" in ops[:3] or "delete" in ops[:3]):
        out.add(HO_DELETE)
    if prog in ("terraform", "tofu", "pulumi") and (ops[:1] == ["destroy"] or ("-destroy" in args and ops[:1] == ["apply"])):
        out.add(HO_DELETE)
    if prog == "aws":
        if ("s3" in ops[:3] and ("rm" in ops[:4] or "rb" in ops[:4])) or any(_REMOTE_DELETE_VERB.match(o) for o in ops[:4]):
            out.add(HO_DELETE)
    if prog in ("gcloud", "az", "doctl", "heroku", "fly", "flyctl", "railway") and any(o in ("delete", "destroy", "apps:destroy") for o in ops[:4]):
        out.add(HO_DELETE)
    if prog == "gh" and ops[1:2] == ["delete"]:   # `gh repo delete`, `gh release delete`: the word after the group; `--title delete` is an option value
        out.add(HO_DELETE)
    methods = _methods(prog, args, ops)
    if "DELETE" in methods and (prog in ("curl", "wget", "http", "https") or (prog == "gh" and ops[:1] == ["api"])):
        out.add(HO_DELETE)
    # ---- money
    if prog == "stripe":
        live = "--live" in args or any(a.startswith("sk_live") or "sk_live_" in a for a in args)
        if live and (set(ops) & _STRIPE_MONEY) and not (set(ops) & _STRIPE_READ):
            out.add(HO_MONEY)
    if prog in ("curl", "wget", "http", "https") and (_hosts(args) & _MONEY_HOSTS) and (methods & {"POST", "PUT", "PATCH", "DELETE"} or _sends_body(args)):
        out.add(HO_MONEY)
    if prog == "bitcoin-cli" and ops[:1] and ops[0] in ("sendtoaddress", "sendmany", "send", "sendall", "walletpassphrase"):
        out.add(HO_MONEY)
    if prog == "cast" and ops[:1] == ["send"] or prog == "solana" and ops[:1] == ["transfer"] or prog == "lncli" and ops[:1] and ops[0] in ("sendpayment", "payinvoice", "sendcoins"):
        out.add(HO_MONEY)
    if prog == "aws" and any(re.match(r"^(?:purchase|buy)-", o) or o == "create-savings-plan" for o in ops[1:3]):
        out.add(HO_MONEY)
    # ---- release
    publish = prog in ("npm", "yarn", "pnpm", "bun", "cargo", "uv", "poetry", "flit", "twine", "gem", "mvn", "gradle", "gradlew", "helm", "oras") or (prog == "pod" and ops[:2] == ["trunk", "push"])
    if publish and not dry and "--dry-run" not in args:
        if prog in ("npm", "yarn", "pnpm", "bun") and ops[:1] in (["publish"], ["unpublish"]):
            out.add(HO_RELEASE)
        elif prog == "cargo" and ops[:1] in (["publish"], ["yank"]):
            out.add(HO_RELEASE)
        elif prog in ("uv", "poetry", "flit") and ops[:1] == ["publish"]:
            out.add(HO_RELEASE)
        elif prog == "twine" and ops[:1] == ["upload"]:
            out.add(HO_RELEASE)
        elif prog == "gem" and ops[:1] == ["push"]:
            out.add(HO_RELEASE)
        elif prog == "mvn" and ops[:1] == ["deploy"]:
            out.add(HO_RELEASE)
        elif prog in ("gradle", "gradlew") and any(o.startswith("publish") for o in ops):
            out.add(HO_RELEASE)
        elif prog in ("helm", "oras") and ops[:1] == ["push"]:
            out.add(HO_RELEASE)
        elif prog == "pod":
            out.add(HO_RELEASE)
    if prog in ("docker", "podman", "buildah", "nerdctl"):
        if ops[:1] == ["push"] and _docker_push_is_remote(ops[1:]):
            out.add(HO_RELEASE)
        if ops[:2] == ["manifest", "push"] and _docker_push_is_remote(ops[2:]):
            out.add(HO_RELEASE)
        if "--push" in args and (ops[:1] in (["build"], ["bake"]) or ops[:2] in (["buildx", "build"], ["buildx", "bake"])):
            out.add(HO_RELEASE)
    if prog == "gh":
        if ops[:2] in (["release", "create"], ["release", "edit"], ["release", "upload"], ["release", "delete"]):
            out.add(HO_RELEASE)
        if ops[:2] == ["workflow", "run"] and any(re.search(r"(?i)release|publish", o) for o in ops[2:3]):
            out.add(HO_RELEASE)
        if ops[:1] == ["api"] and any(a.upper() == "POST" for a in args) and any(re.search(r"/releases(?:/|$)", o) for o in ops[1:]):
            out.add(HO_RELEASE)
    if prog in _DEPLOY_PROD and any(a in _DEPLOY_PROD[prog] for a in args):
        out.add(HO_RELEASE)
    if prog == "firebase" and ops[:1] == ["deploy"] or prog == "wrangler" and ops[:1] in (["deploy"], ["publish"]) or prog in ("serverless", "sls", "cdk", "eb") and ops[:1] == ["deploy"]:
        out.add(HO_RELEASE)
    return out


def _git_c_dirs(raw: str) -> tuple[str, ...]:
    """The directories named by `git -C dir ...` in the raw segment (the normalised one drops the option)."""
    toks = _tokens(raw)
    prog, _ = _program(toks)
    if prog != "git":
        return ()
    out = []
    for i, t in enumerate(toks):
        if t == "-C" and i + 1 < len(toks):
            out.append(toks[i + 1])
        elif t.startswith("-C") and len(t) > 2:
            out.append(t[2:])
        elif t in ("push", "fetch", "clean", "reflog", "gc", "tag", "commit", "status"):
            break
    return tuple(out)


def shell_classes(bare: str, dirs: tuple[str, ...] = (), cwd: str = "", raw: str = "") -> set[str]:
    """The classes of one simple shell command (a segment, launcher prefixes and git global options removed). `raw` is the segment as written, for what the
    normalised one drops (`git -C dir`)."""
    prog, args = _program(_tokens(bare))
    if not prog:
        return set()
    if raw and prog == "git":
        extra = tuple(os.path.join(d, c) if d and not os.path.isabs(os.path.expanduser(c)) else os.path.expanduser(c) for c in _git_c_dirs(raw) for d in (dirs or (cwd,)))
        dirs = extra + tuple(dirs or ())
    return _program_classes(prog, args, tuple(dirs or ()), cwd)


def is_sql_client(bare: str) -> bool:
    prog, _ = _program(_tokens(bare))
    return prog in _SQL_CLIENTS


def sql_destroys(text: str) -> bool:
    """Does the raw command text hold a statement that destroys data? The segmenter blanks quoted text, and a SQL statement is a quoted argument."""
    return bool(_SQL_DESTROY.search(text))


# ------------------------------------------------------------------ tools by name

_READ_FIRST = {"get", "list", "retrieve", "search", "read", "fetch", "find", "describe", "show", "count", "check", "preview", "simulate", "estimate", "calculate", "validate", "export",
               "download", "query", "lookup", "view", "inspect", "status", "is", "has", "can", "dry", "plan", "diff", "audit", "history", "stats", "summarize", "explain", "test"}
_MONEY_SERVER = re.compile(r"(?i)stripe|paypal|payment|bank|plaid|square|adyen|wise|coinbase|venmo|billing|brex|ramp|mercury|revolut|invoice|finance|ledger")
_MONEY_SURE = {"refund", "refunds", "payout", "payouts", "withdraw", "withdrawal", "withdrawals", "purchase", "buy", "pay", "charge"}
_MONEY_IN_SERVER = {"transfer", "transfers", "deposit", "payment", "payments", "order", "checkout"}
_RELEASE_NOT = {"lock", "locks", "mutex", "memory", "port", "session", "claim", "hold", "resource", "handle", "seat", "connection", "slot", "cursor"}
_DELETE_TOKENS = {"delete", "destroy", "drop", "purge", "wipe", "erase", "terminate", "truncate", "prune"}
_RELEASE_TOKENS = {"publish", "release", "releases", "unpublish"}


def _name_tokens(tool: str) -> tuple[str, list[str]]:
    server, _, last = tool.removeprefix("mcp__").partition("__")
    if not last:
        server, last = "", server
    last = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", last).lower()
    return server, [t for t in re.split(r"[_\-\s.]+", last) if t]


def tool_classes(tool: str) -> set[str]:
    """The classes an MCP tool falls in by what its name does. A name that starts with a reading verb (`get_invoice`, `list_charges`, `preview_publish`) is never one."""
    server, toks = _name_tokens(tool)
    if not toks or toks[0] in _READ_FIRST:
        return set()
    out: set[str] = set()
    t = set(toks)
    if t & _DELETE_TOKENS:
        out.add(HO_DELETE)
    if t & _RELEASE_TOKENS and not (t & _RELEASE_NOT):
        out.add(HO_RELEASE)
    money_server = bool(_MONEY_SERVER.search(server))
    if t & _MONEY_SURE:
        out.add(HO_MONEY)
    elif money_server and t & _MONEY_IN_SERVER and (t & {"transfer", "transfers", "deposit"} or toks[0] in {"create", "make", "send", "submit", "execute", "process", "confirm", "capture", "authorize", "place", "issue", "initiate"}):
        out.add(HO_MONEY)
    elif t & {"send"} and t & {"money", "funds", "payment"}:
        out.add(HO_MONEY)
    elif t & {"place"} and t & {"order"}:
        out.add(HO_MONEY)
    elif t & {"transfer"} and t & {"funds", "money", "balance"}:
        out.add(HO_MONEY)
    elif toks[0] in {"make", "create", "submit", "execute", "process"} and t & {"payment", "payments"}:
        out.add(HO_MONEY)
    return out
