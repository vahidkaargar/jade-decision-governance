"""Redaction before anything leaves the machine or reaches the ledger (docs/05 section 3).

`redact(text) -> (text, hits)` is pure and deterministic and idempotent (it leaves its own `[REDACTED:...]` marks alone).
`residual(text)` re-checks a result with every high-confidence token pattern (it is built from `PATTERNS`, so the two cannot drift);
a residual match makes `jade ask` refuse the call (exit 6). `secrets_only=True` skips the email pattern: that is the setting for text
that stays on this machine (the ledger), where an address is not a credential.
"""

from __future__ import annotations

import re

_PK = "PRIVATE " + "KEY"
_TOKEN_SHAPES = (
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|glpat-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}|xox[baprse]-[A-Za-z0-9.-]{10,}|xapp-[0-9]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{36}"
    r"|[spr]k_(?:live|test)_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|ck_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_-]{30,}"
    r"|ya29\.[A-Za-z0-9_-]{20,}|GOCSPX-[A-Za-z0-9_-]{20,}|SK[0-9a-f]{32}|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})"
    r"|\bhttps://hooks\.slack\.com/services/[A-Za-z0-9/_-]{16,}"
    r"|\bhttps://(?:[a-z]+\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{16,}")

# Words that make a key name secret. STRONG ones match anywhere inside the name (`MYTOKEN`, `clientSecret`, `x-api-key`); WEAK ones
# only as a whole `_`, `-` or `.` separated word (`DB_PASS`, `pwd`, but not `PASSED` or `BYPASS`).
_STRONG = (r"PASSWORD|PASSWD|PASSPHRASE|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|ACCOUNT[_-]?KEY|SHARED[_-]?ACCESS[_-]?KEY"
           r"|AUTH[_-]?KEY|SIGNING[_-]?KEY|CREDENTIALS?|COOKIE")
_WEAK = r"PASS|PWD"
# The regexes below take a whole run of key characters, once, from its start (a look-behind), after a look-ahead for a key word; `_secret_key`
# then decides whether the name is a credential. Matching the key words around a greedy prefix inside the regex backtracks quadratically on a
# long token (a base64 log line), and this runs on every byte a call sends.
_KEY = r"(?<![A-Za-z0-9_.-])(?=[A-Za-z0-9_.-]*?(?:" + _STRONG + "|" + _WEAK + r"))([A-Za-z0-9_.-]{3,})"
_STRONG_KEY = re.compile(r"[A-Z0-9_.-]*(?:" + _STRONG + r")[A-Z0-9_]*", re.I)
_WEAK_KEY = re.compile(r"(?:[A-Z0-9]+[_.-])*(?:" + _WEAK + r")(?:[_.-][A-Z0-9]+)*", re.I)
_VALUE = (r"(\\\"[^\n]{3,}?\\\"|\\'[^\n]{3,}?\\'|\"[^\"\n]{3,}\"|'[^'\n]{3,}'|\[(?!REDACTED:)[^\]\s,]{3,}\]"
          r"|(?!\[?REDACTED:)[^\s,;\"'\[\\]{3,})")
_AUTH_VALUE = (r"(?:(?:bearer|basic|token|digest|negotiate|apikey)\s+[^\s\"']+|(?!\[?REDACTED:)(?=[^\s\"']*\d)[^\s\"']{8,}|(?!\[?REDACTED:)[^\s\"']{20,})")

PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*" + _PK + r"(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*" + _PK + r"(?: BLOCK)?-----|\Z)"), "[REDACTED:private_key]"),
    ("keychain", re.compile(r"(?m)^([ \t]*(?:[$%>#][ \t]*)?security[ \t]+find-(?:generic|internet)-password\b[^\n|>]*?[ \t]-w\b[^\n|>]*\n)(?:[ \t]*\n)*(?!\[REDACTED:keychain\])[^\n]+"),
                  r"\1[REDACTED:keychain]"),
    ("keychain_add", re.compile(r"(\bsecurity[ \t]+add-(?:generic|internet)-password\b[^\n]*?[ \t]-w[ \t]+)(?!\[REDACTED:)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s]+)"),
                      r"\1[REDACTED:keychain]"),
    ("url_password", re.compile(r"(://[^/\s:@]*):(?!\[REDACTED:|\d+(?:[/?#\s]|$))([^\s]{3,256}?)@(?=[^@\s/]+(?:[/:?#\"'\s]|$))"), r"\1:[REDACTED:url_password]@"),
    ("authorization", re.compile(r"(?i)(authorization(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?)" + _AUTH_VALUE), r"\1[REDACTED:authorization]"),
    ("cookie", re.compile(r"(?i)(\b(?:set-)?cookie\s*:[ \t]*)(?!\[?REDACTED:)[^\r\n]{3,}"), r"\1[REDACTED:cookie]"),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED:bearer]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    ("api_key", re.compile(_TOKEN_SHAPES), "[REDACTED:api_key]"),
    ("bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,50}\b"), "[REDACTED:bot_token]"),
    ("signature", re.compile(r"(?i)([?&;](?:sig|signature|x-amz-signature|x-amz-credential|x-amz-security-token)=)(?!\[?REDACTED:)[A-Za-z0-9%+/=_.~-]{8,}"),
                  r"\1[REDACTED:signature]"),
    ("curl_user", re.compile(r"(?i)(\bcurl\b[^\n|;&]*?\s(?:-u|--user)[ \t]+[^\s:@]*:)(?!\[?REDACTED:)(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"']{3,})"), r"\1[REDACTED:cli_flag]"),
    ("mysql_p", re.compile(r"(\b(?:mysql|mysqldump|mysqladmin|mariadb)\b[^\n|;&]*?\s-p)(?!\[?REDACTED:)(?!\d{1,5}(?:\s|$))([^\s\"']{3,})"), r"\1[REDACTED:cli_flag]"),
    ("flag", re.compile(r"(?i)(\s--(?:password|passwd|token|secret|api-?key|access-?token|auth-?token|client-?secret|private-?key)[ \t]+)(?!\[?REDACTED:|-)"
                        r"(\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s\"']{3,})"), r"\1[REDACTED:cli_flag]"),
    ("annotated", re.compile(r"(?i)" + _KEY + r"(\s*:\s*[A-Za-z_][A-Za-z0-9_.]*(?:\[[^\]\n]*\])?\s*=\s*)" + _VALUE), "assignment"),
    ("assignment", re.compile(r"(?i)" + _KEY + r"(\\?[\"']?\s*[=:]\s*)" + _VALUE), "assignment"),
    ("email", re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b(?!:[A-Za-z0-9_~./-])"), "[REDACTED:email]"),
]
# The patterns whose match is a credential beyond doubt: `residual()` is their union, so it can never disagree with redact().
HIGH_CONFIDENCE = ("private_key", "jwt", "api_key")
RESIDUAL = re.compile("|".join(f"(?:{p.pattern})" for name, p, _ in PATTERNS if name in HIGH_CONFIDENCE))

_NON_SECRET_KEY = re.compile(r"(?i)(?:count|counts|tokens|length|parallelism|limit|name)$")
_NON_SECRET_VALUE = re.compile(
    r"(?i)(?:true|false|null|none|nil|undefined|required|optional|unset|empty|str|string|int|integer|float|bool|boolean|bytes|any|object|number|"
    r"list|dict|optional|union|annotated|secretstr|secret|<[^<>]{1,40}>|\$\{?[A-Za-z_]\w*\}?)")
_LOCAL_PARTS_NOT_EMAIL = {"git", "ssh", "hg", "svn"}   # `ssh://git@github.com/...` is a remote, not a person


def _secret_key(key: str) -> bool:
    return _STRONG_KEY.fullmatch(key) is not None or _WEAK_KEY.fullmatch(key) is not None


def _assignment(m: re.Match) -> str:
    key, sep, value = m.group(1), m.group(2), m.group(3)
    if not _secret_key(key):
        return m.group(0)
    bare = value.strip("\"'\\").rstrip(")]},;:")
    skip = (_NON_SECRET_KEY.search(key) is not None
            or (key.lower() == "pwd" and re.match(r"/[\w.-]", bare) is not None)
            or _NON_SECRET_VALUE.fullmatch(bare) is not None
            or (key.lower() in ("pass", "pwd") and ":" in sep)   # `PASS: tests/x.py` is a test result, not a credential
            or value.startswith("[") and bare.startswith("[REDACTED:"))
    return m.group(0) if skip else f"{key}{sep}[REDACTED:assignment]"


def _email(owners: tuple[str, ...]):
    def sub(m: re.Match) -> str:
        who = m.group(0).lower()
        return m.group(0) if who in owners or who.split("@", 1)[0] in _LOCAL_PARTS_NOT_EMAIL else "[REDACTED:email]"
    return sub


def redact(text: str, owner_emails: tuple[str, ...] = (), *, secrets_only: bool = False) -> tuple[str, int]:
    """Return the redacted text and the number of replacements. `secrets_only` leaves email addresses alone (text that never leaves this machine)."""
    hits = 0
    for kind, pattern, repl in PATTERNS:
        if kind == "email":
            if secrets_only:
                continue
            fn = _email(owner_emails)
            n = sum(1 for m in pattern.finditer(text) if fn(m) != m.group(0))
            text = pattern.sub(fn, text)
        elif repl == "assignment":
            n = 0

            def _count(m: re.Match) -> str:
                nonlocal n
                out = _assignment(m)
                n += out != m.group(0)
                return out
            text = pattern.sub(_count, text)
        else:
            text, n = pattern.subn(repl, text)
        hits += n
    return text, hits


def residual(text: str) -> bool:
    return bool(RESIDUAL.search(text))


def redact_obj(obj, owner_emails: tuple[str, ...] = (), *, secrets_only: bool = False):
    """Redact every string inside a JSON-like value. Returns (value, hits)."""
    if isinstance(obj, str):
        return redact(obj, owner_emails, secrets_only=secrets_only)
    if isinstance(obj, dict):
        out, hits = {}, 0
        for k, v in obj.items():
            out[k], n = redact_obj(v, owner_emails, secrets_only=secrets_only)
            hits += n
        return out, hits
    if isinstance(obj, (list, tuple)):
        out, hits = [], 0
        for v in obj:
            r, n = redact_obj(v, owner_emails, secrets_only=secrets_only)
            out.append(r)
            hits += n
        return out, hits
    return obj, 0
