"""Redaction before anything leaves the machine or reaches the ledger (docs/05 section 3).

`redact(text) -> (text, hits)` is pure and deterministic and idempotent (it leaves its own `[REDACTED:...]` marks alone).
`residual(text)` re-checks a result with every high-confidence token pattern (it is built from `PATTERNS`, so the two cannot drift);
a residual match makes `jade ask` refuse the call (exit 6). `secrets_only=True` skips the email pattern: that is the setting for text
that stays on this machine (the ledger), where an address is not a credential.

The cost is linear in the length of the text, whatever an adversarial command looks like: every quantifier that could run on past the end
of a value is bounded (a run of key characters, a `[...]` or `$(...)` value, the look-back to a command word), so no pattern rescans the
rest of the text from each of many start points. This runs on every byte a call sends and on every gated command before the hook's
timeout, so a quadratic pattern is a way to make the gate fail open (`test_redaction_is_linear_on_adversarial_text`).
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable

_PK = "PRIVATE " + "KEY"
_TOKEN_SHAPES = (
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|glpat-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}|xox[a-z]-[A-Za-z0-9.%+/=-]{10,}|xapp-[0-9]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{36}"
    r"|[spr]k_(?:live|test)_[A-Za-z0-9]{20,}|whsec_[A-Za-z0-9]{20,}|ck_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_-]{30,}"
    r"|ya29\.[A-Za-z0-9_-]{20,}|GOCSPX-[A-Za-z0-9_-]{20,}|SK[0-9a-f]{32}|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})"
    r"|\b(?:https?://)?hooks\.slack(?:-gov)?\.com/(?:services|workflows|triggers)/[A-Za-z0-9/_-]{16,}"
    r"|\b(?:https?://)?(?:[a-z]+\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{16,}")

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
# A command substitution as a value: one level, bounded. `_benign` reads it (a fetch from a store is a reference, anything else may carry the secret).
_SUBST = r"\$\([^)\n]{1,256}\)|`[^`\n]{1,256}`"
# A `[value]` is read up to 256 characters; a longer or unclosed one is taken up to the next blank (the second alternative), so a `password=[`
# with no `]` costs one pass, never a rescan of the rest of the text from every `[`.
_VALUE = (r"(\\\"[^\n]{3,}?\\\"|\\'[^\n]{3,}?\\'|\"[^\"\n]{3,}\"|'[^'\n]{3,}'|\[(?!REDACTED:)[^\]\s,]{3,256}\]"
          r"|\[(?!REDACTED:)[^\]\s,]{256}[^\s,;\"']*|" + _SUBST + r"|(?!\[?REDACTED:)[^\s,;\"'\[\\]{3,})")
_AUTH_VALUE = (r"(?:(?:bearer|basic|token|digest|negotiate|apikey)\s+[^\s\"']+|(?!\[?REDACTED:)(?=[^\s\"']*\d)[^\s\"']{8,}|(?!\[?REDACTED:)[^\s\"']{20,})")
_CLI_VALUE = r"(\"[^\"\n]{3,}\"|'[^'\n]{3,}'|" + _SUBST + r"|[^\s\"']{3,})"
_LOOK_BACK = 2000   # how far back a command word is looked for from a flag (one command, within this many characters)

# The `\1<value>` of a Keychain read: the command line (a prompt, a path, `2>/dev/null` and combined flags such as `-wa` or `-gw` are read; a pipe,
# a `>` redirect of stdout or a `$(...)` have no output line to hide) plus the next non-empty line.
_KC_LINE = r"(?:[^\n|>]|[ \t]\d>(?:&\d|[^\s|>]*)){0,300}"
_KC_LEAD = r"(?m)^([ \t]*(?:[$%>#][ \t]*)?(?:[^\s/]*/){0,8}security[ \t]+find-(?:generic|internet)-password\b"

PATTERNS: list[tuple[str, re.Pattern, str | Callable[[re.Match], str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*" + _PK + r"(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*" + _PK + r"(?: BLOCK)?-----|\Z)"), "[REDACTED:private_key]"),
    ("keychain", re.compile(_KC_LEAD + _KC_LINE.replace("{0,300}", "{0,300}?") + r"[ \t]-[A-Za-z]*w[A-Za-z]*(?=[ \t\r]|$)" + _KC_LINE
                            + r"\n)(?:[ \t]*\n)*(?!\[REDACTED:keychain\])[^\n]+"), r"\1[REDACTED:keychain]"),
    ("keychain_add", re.compile(r"(\bsecurity[ \t]+add-(?:generic|internet)-password\b[^\n]{0,300}?[ \t]-[A-Za-z]*w[ \t]+)(?!\[REDACTED:)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s]+)"),
                      r"\1[REDACTED:keychain]"),
    ("url_password", re.compile(r"(://[^/\s:@]*):(?!\[REDACTED:|\d+(?:[/?#\s]|$))([^\s]{3,256}?)@(?=[^@\s/]+(?:[/:?#\"'\s]|$))"), r"\1:[REDACTED:url_password]@"),
    ("authorization", re.compile(r"(?i)(authorization(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?)" + _AUTH_VALUE), r"\1[REDACTED:authorization]"),
    ("cookie", re.compile(r"(?i)(\b(?:set-)?cookie\s*:[ \t]*)(?!\[?REDACTED:)[^\r\n]{3,}"), r"\1[REDACTED:cookie]"),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED:bearer]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,512}+\.[A-Za-z0-9_-]{8,8192}+\.[A-Za-z0-9_-]{8,2048}+"), "[REDACTED:jwt]"),
    ("api_key", re.compile(_TOKEN_SHAPES), "[REDACTED:api_key]"),
    ("bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,50}\b"), "[REDACTED:bot_token]"),
    ("signature", re.compile(r"(?i)((?:[?&;](?:sig|signature|x-amz-signature|x-amz-credential|x-amz-security-token)|[?&]key)=)(?!\[?REDACTED:)[A-Za-z0-9%+/=_.~-]{8,}"),
                  r"\1[REDACTED:signature]"),
    # The command-line forms below are found by the flag alone and then checked for the command word behind it (`_cli`): scanning from the command
    # word forward to the flag rescans the line from every command word and is quadratic.
    ("curl_user", re.compile(r"(?i)(\s(?:-u[ \t]*|--user(?:[ \t]+|=))[^\s:@]*:)(?!\[?REDACTED:)" + _CLI_VALUE), "cli:curl"),
    ("mysql_p", re.compile(r"(?i)(\s-p)(?!\[?REDACTED:)(?!\d{1,5}(?:\s|$))(" + _SUBST + r"|[^\s\"']{3,})"), "cli:mysql"),
    ("docker_p", re.compile(r"(\s-p(?:[ \t]+|=))(?!\[?REDACTED:)" + _CLI_VALUE), "cli:docker"),
    ("sshpass_p", re.compile(r"(\s-p[ \t]*)(?!\[?REDACTED:)" + _CLI_VALUE), "cli:sshpass"),
    ("redis_a", re.compile(r"(\s(?:-a|--pass)[ \t]+)(?!\[?REDACTED:)" + _CLI_VALUE), "cli:redis"),
    ("mongo_p", re.compile(r"(\s-p[ \t]+)(?!\[?REDACTED:)" + _CLI_VALUE), "cli:mongo"),
    ("upper_p", re.compile(r"(\s-P[ \t]*)(?!\[?REDACTED:)" + _CLI_VALUE), "cli:upper_p"),
    ("ldap_w", re.compile(r"(\s-w[ \t]+)(?!\[?REDACTED:)" + _CLI_VALUE), "cli:ldap"),
    ("openssl_pass", re.compile(r"(\s-pass(?:in|out)?[ \t]+pass:)(?!\[?REDACTED:)" + _CLI_VALUE), "cli:"),
    ("htpasswd", re.compile(r"(\bhtpasswd[ \t]+-(?![A-Za-z]*n)[A-Za-z]*b[A-Za-z]*[ \t]+(?:[^\s-]\S*[ \t]+){2})(?!\[?REDACTED:)" + _CLI_VALUE), "cli:"),
    ("htpasswd_n", re.compile(r"(\bhtpasswd[ \t]+-(?=[A-Za-z]*n)[A-Za-z]*b[A-Za-z]*[ \t]+(?:[^\s-]\S*[ \t]+){1})(?!\[?REDACTED:)" + _CLI_VALUE), "cli:"),
    ("flag", re.compile(r"(?i)(\s--(?:(?:http|ftp|proxy)-password|password|passwd|pass|passphrase|token|secret|api-?key|access-?token|auth-?token|client-?secret|private-?key)[ \t]+)(?!\[?REDACTED:|-)" + _CLI_VALUE), "cli:"),
    ("annotated", re.compile(r"(?i)" + _KEY + r"(\s*:\s*[A-Za-z_][A-Za-z0-9_.]*(?:\[[^\]\n]{0,128}\])?\s*=\s*)" + _VALUE), "assignment"),
    ("assignment", re.compile(r"(?i)" + _KEY + r"(\\?[\"']?\s*[=:]\s*)" + _VALUE), "assignment"),
    ("spaced", re.compile(r"(?i)\b((?:api|secret|access|auth|private|client|account|signing)[ ]+(?:key|secret|token))(\s*[:=]\s*)" + _VALUE), "assignment"),
    ("email", re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b(?!:[A-Za-z0-9_~./-])"), "[REDACTED:email]"),
]
# The patterns whose match is a credential beyond doubt: `residual()` is their union, so it can never disagree with redact().
HIGH_CONFIDENCE = ("private_key", "jwt", "api_key")
RESIDUAL = re.compile("|".join(f"(?:{p.pattern})" for name, p, _ in PATTERNS if name in HIGH_CONFIDENCE))

# A key that ends in one of these is not a credential (a count, a type, a location): `max_tokens`, `TOKEN_TYPE`, `token_url`, `secret_ref`, `password_file`.
_NON_SECRET_KEY = re.compile(r"(?i)(?:count|counts|tokens|length|parallelism|limit|name)$")
_NON_SECRET_SUFFIX = re.compile(r"(?:[_.-]|(?<=[a-z])(?=[A-Z]))(?i:type|url|uri|ref|file|path|dir|ttl|expiry|expires|expires[_-]?in|expires[_-]?at|endpoint|header|prefix|alg|algorithm|kind|scope)$")
_NON_SECRET_VALUE = re.compile(
    r"(?i)(?:true|false|null|none|nil|undefined|required|optional|unset|empty|str|string|int|integer|float|bool|boolean|bytes|any|object|number|"
    r"list|dict|optional|union|annotated|secretstr|secret|<[^<>]{1,40}>|\$\{?[A-Za-z_]\w*\}?)")
# A value that is a reference, not a credential: `$TOKEN`, `${TOKEN}`, `$(cat /run/secrets/x)`, `$(op read op://v/i)`. The remedy the gate's block message
# gives (move the secret out of the command) must not itself count as a secret. `$(echo S3cret)` or `$(cat <<< S3cret)` is not a fetch and stays redacted.
_ENV_REF = re.compile(r"\$\{?[A-Za-z_]\w*\}?")
_FETCH = re.compile(r"\$\(\s*(?:command\s+)?(?:\S*/)?(?:cat|op|pass|security|vault|aws|gcloud|az|doppler|bw|lpass|gpg|sops|secret-tool|printenv|pbpaste|ovault|kubectl|keyring)"
                    r"(?:\s[\w@%+=:,./~$\"'{} -]*)?\)")
_LOCAL_PARTS_NOT_EMAIL = {"git", "ssh", "hg", "svn"}   # `ssh://git@github.com/...` is a remote, not a person

# command word each `cli:` pattern needs earlier in the same command (a `;`, `&`, `|` or line end ends one)
_SCOPES = {
    "cli:curl": re.compile(r"\bcurl\b", re.I),
    "cli:mysql": re.compile(r"\b(?:mysql|mysqldump|mysqladmin|mariadb)\b", re.I),
    "cli:docker": re.compile(r"\b(?:docker|podman|nerdctl)[ \t]+login\b", re.I),
    "cli:sshpass": re.compile(r"\bsshpass\b", re.I),
    "cli:redis": re.compile(r"\bredis-cli\b", re.I),
    "cli:mongo": re.compile(r"\b(?:mongo|mongosh)\b", re.I),
    "cli:upper_p": re.compile(r"\b(?:sqlcmd|zip|unzip|7z|7za|7zr|smbclient)\b", re.I),
    "cli:ldap": re.compile(r"\bldap(?:search|modify|add|delete|passwd|whoami)\b", re.I),
}


def _secret_key(key: str) -> bool:
    key = key.replace(" ", "_")
    return _STRONG_KEY.fullmatch(key) is not None or _WEAK_KEY.fullmatch(key) is not None


def _benign(value: str) -> bool:
    """True for a reference to a secret rather than the secret: an environment variable or a fetch from a store."""
    bare = value.strip("\"'\\")
    return _ENV_REF.fullmatch(bare) is not None or _FETCH.fullmatch(bare) is not None


def _assignment(m: re.Match) -> str:
    key, sep, value = m.group(1), m.group(2), m.group(3)
    if not _secret_key(key):
        return m.group(0)
    bare = value.strip("\"'\\").rstrip(")]},;:")
    skip = (_NON_SECRET_KEY.search(key) is not None or _NON_SECRET_SUFFIX.search(key) is not None
            or (key.lower() == "pwd" and re.match(r"/[\w.-]", bare) is not None)
            or _NON_SECRET_VALUE.fullmatch(bare) is not None
            or _benign(value)
            or (key.lower() in ("pass", "pwd") and ":" in sep)   # `PASS: tests/x.py` is a test result, not a credential
            or value.startswith("[") and bare.startswith("[REDACTED:"))
    return m.group(0) if skip else f"{key}{sep}[REDACTED:assignment]"


_SEP = re.compile(r"[\n|;&]")


def _in_command(text: str, pos: int, starts: list[int]) -> bool:
    """Is the command word (`starts`: where it occurs in `text`, ascending) in the same command as `pos`: the nearest one before `pos`, within
    `_LOOK_BACK` characters, with no `;`, `&`, `|` or line end between?"""
    i = bisect_left(starts, pos) - 1
    return i >= 0 and starts[i] >= pos - _LOOK_BACK and _SEP.search(text, starts[i], pos) is None


def _cli(tag: str) -> Callable[[re.Match], str]:
    scope = _SCOPES.get(tag)
    seen: list = [None, []]   # the text the command words were found in, and where (`pattern.sub` hands every match the same string object)

    def sub(m: re.Match) -> str:
        if _benign(m.group(2)):
            return m.group(0)
        if scope is not None:
            if seen[0] is not m.string:
                seen[0], seen[1] = m.string, [x.start() for x in scope.finditer(m.string)]
            if not _in_command(m.string, m.start(), seen[1]):
                return m.group(0)
        return f"{m.group(1)}[REDACTED:cli_flag]"
    return sub


_CLI = {tag: _cli(tag) for tag in (*_SCOPES, "cli:")}


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
        elif repl == "assignment" or repl in _CLI:
            fn = _assignment if repl == "assignment" else _CLI[repl]
            n = 0

            def _count(m: re.Match, fn=fn) -> str:
                nonlocal n
                out = fn(m)
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
