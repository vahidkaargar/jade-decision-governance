"""Redaction before anything leaves the machine or reaches the ledger (docs/05 section 3).

`redact(text) -> (text, hits)` is pure and deterministic. `residual(text)` re-checks the result with the
SI-05 block pattern; a residual match makes `jade ask` refuse the call (exit 6).
"""

from __future__ import annotations

import re

_PK = "PRIVATE " + "KEY"
PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*" + _PK + r"-----[\s\S]*?(?:-----END [A-Z ]*" + _PK + r"-----|\Z)"), "[REDACTED:private_key]"),
    ("url_password", re.compile(r"(://[^/\s:@]+):([^@\s/]{3,})@"), r"\1:[REDACTED:url_password]@"),
    ("authorization", re.compile(r"(?i)(authorization\s*[:=]\s*)(?:(?:bearer|basic|token)\s+)?[^\s\"']+"), r"\1[REDACTED:authorization]"),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED:bearer]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    ("api_key", re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|glpat-[A-Za-z0-9_-]{20,}"
        r"|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{36}"
        r"|[sr]k_(?:live|test)_[A-Za-z0-9]{20,}|ck_[A-Za-z0-9]{20,})"), "[REDACTED:api_key]"),
    ("bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,50}\b"), "[REDACTED:bot_token]"),
    ("assignment", re.compile(
        r"(?i)\b([A-Z0-9_.-]*(?:PASSWORD|PASSWD|PASSPHRASE|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIALS?)[A-Z0-9_]*)"
        r"([\"']?\s*[=:]\s*)(\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s,;\"'\[]{3,})"), r"\1\2[REDACTED:assignment]"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED:email]"),
]
RESIDUAL = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|gho_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*" + _PK + ")")


def redact(text: str, owner_emails: tuple[str, ...] = ()) -> tuple[str, int]:
    """Return the redacted text and the number of replacements."""
    hits = 0
    for kind, pattern, repl in PATTERNS:
        if kind == "email":
            kept = [m for m in pattern.findall(text) if m.lower() in owner_emails]
            text, n = pattern.subn(lambda m, _r=repl: m.group(0) if m.group(0).lower() in owner_emails else _r, text)
            n -= len(kept)
        else:
            text, n = pattern.subn(repl, text)
        hits += n
    return text, hits


def residual(text: str) -> bool:
    return bool(RESIDUAL.search(text))


def redact_obj(obj, owner_emails: tuple[str, ...] = ()):
    """Redact every string inside a JSON-like value. Returns (value, hits)."""
    if isinstance(obj, str):
        return redact(obj, owner_emails)
    if isinstance(obj, dict):
        out, hits = {}, 0
        for k, v in obj.items():
            out[k], n = redact_obj(v, owner_emails)
            hits += n
        return out, hits
    if isinstance(obj, list):
        out, hits = [], 0
        for v in obj:
            r, n = redact_obj(v, owner_emails)
            out.append(r)
            hits += n
        return out, hits
    return obj, 0
