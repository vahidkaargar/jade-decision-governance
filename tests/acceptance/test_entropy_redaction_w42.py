"""W-42: a secret with no vendor shape and no key name (a bare random token) is still recognised by a conservative structural check.

The check is a heuristic: it redacts a run of 20 to 256 characters that has a digit, an upper-case and a lower-case letter, is not made of words,
a slug or a path, and has a Shannon entropy of 3.5 bits per character or more. Its twins (git SHAs, UUIDs, file hashes, base64 image headers,
paths, identifiers) live in `tests/fixtures/redact_twins.json` and must pass through unchanged. Every token here is synthetic and stored in parts."""
import json
import random
import string
import subprocess
import time
from pathlib import Path

import pytest

from jade import adapter, ask, ledger, redact

ROOT = Path(__file__).resolve().parents[2]
FX = ROOT / "tests" / "fixtures"
CORPUS = [i for i in json.loads((FX / "redact_corpus.json").read_text()) if i["kind"].startswith("entropy_")]
TWINS = json.loads((FX / "redact_twins.json").read_text())
B62 = string.ascii_letters + string.digits


def _random_token(rng: random.Random, n: int, alphabet: str = B62) -> str:
    while True:
        t = "".join(rng.choice(alphabet) for _ in range(n))
        if any(c.isdigit() for c in t) and any(c.isupper() for c in t) and any(c.islower() for c in t):
            return t


def test_the_corpus_carries_the_new_cases():
    assert len(CORPUS) >= 15


@pytest.mark.parametrize("item", CORPUS, ids=lambda i: i["kind"])
def test_a_bare_random_token_is_redacted_as_entropy(item):
    secret = "".join(item["secret"])
    text, hits = redact.redact(item["template"].replace("@@", secret))
    assert secret.rstrip("=") not in text and hits >= 1
    assert "[REDACTED:entropy]" in text
    assert redact.redact(text) == (text, 0), "idempotent"


@pytest.mark.parametrize("item", CORPUS, ids=lambda i: i["kind"])
def test_the_ledger_pass_redacts_it_too(item):
    secret = "".join(item["secret"])
    text, hits = redact.redact(item["template"].replace("@@", secret), secrets_only=True)
    assert secret.rstrip("=") not in text and hits >= 1


@pytest.mark.parametrize("item", TWINS, ids=lambda i: i["kind"])
def test_a_twin_passes_unchanged(item):
    text = item["template"].replace("@@", "".join(item["parts"]))
    assert redact.redact(text) == (text, 0)
    assert redact.redact(text, secrets_only=True) == (text, 0)
    assert not redact.residual(text)


def test_false_positive_rate_on_the_twins_is_zero_and_the_twins_are_a_fair_sample():
    """The measured rate: flagged twins / twins. The twins must hold every kind the backlog names, and at least 100 of them."""
    kinds = {i["kind"] for i in TWINS}
    assert {"git_sha", "uuid", "sha256_file", "png_header", "path", "ident"} <= kinds
    assert len(TWINS) >= 100
    flagged = [i["kind"] for i in TWINS if redact.redact(i["template"].replace("@@", "".join(i["parts"])))[1]]
    assert flagged == []


def test_ordinary_repository_text_is_never_flagged():
    """The project's own docs, skills and source are real ordinary text (no synthetic secrets in them): none of it may be redacted as entropy."""
    files = subprocess.run(["git", "ls-files", "docs", "skills", "jade", "policies", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert len(files) > 30
    run = next(p for name, p, _ in redact.PATTERNS if name == "entropy")
    scanned = candidates = 0
    for f in files:
        try:
            text = (ROOT / f).read_text(encoding="utf8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += len(text)
        for m in run.finditer(text):
            candidates += 1
            assert not redact._random_looking(m.group(0)), (f, len(m.group(0)))
    assert scanned > 200_000 and candidates > 0


def test_detection_rate_on_random_tokens_is_high_for_every_length():
    rng = random.Random(42)
    for n, floor in ((20, 0.93), (24, 0.95), (32, 0.97), (43, 0.98), (64, 0.99)):
        found = sum("[REDACTED:entropy]" in redact.redact(f"saw {_random_token(rng, n)} here")[0] for _ in range(1000))
        assert found / 1000 >= floor, (n, found)


def test_the_length_floor_is_twenty_and_the_ceiling_is_a_blob():
    rng = random.Random(5)
    short = _random_token(rng, 19)
    assert redact.redact(f"saw {short} here") == (f"saw {short} here", 0)
    blob = _random_token(rng, 400)
    assert redact.redact(f"saw {blob} here")[1] == 0, "a run over 256 characters is a blob (a dump, an attachment), not a credential"


@pytest.mark.parametrize("text", [
    "a" * 40,                                  # one class
    "abcdefghij0123456789klmnopqrstuv",        # no capital
    "ABCDEFGHIJ0123456789KLMNOPQRSTUV",        # no lower case
    "AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEf",        # no digit
    "AbAbAbAbAbAbAbAb1AbAbAbAbAbAbAbAb",       # low entropy
])
def test_each_condition_is_needed(text):
    assert redact.redact(f"v {text} w") == (f"v {text} w", 0)


def test_a_key_name_is_not_needed_and_does_not_change_the_kind():
    t = "Zq8" + "vK2mN5pQ8sT1vW4xZ7aB3cD9eF"
    out, hits = redact.redact(f"pin={t}")
    assert t not in out and hits == 1
    out, hits = redact.redact(f"password={t}")
    assert "[REDACTED:assignment]" in out and t not in out and hits == 1, "a name the older rules know keeps its own kind"


def test_pure_hex_is_not_redacted_and_that_is_the_documented_limit():
    """A 64-digit hex string is a SHA-256 as often as a key: the twins need it (F23). Named in docs/05 section 3 (accepted risk)."""
    for n in (32, 40, 64):
        h = "".join(random.Random(n).choice("0123456789abcdef") for _ in range(n))
        assert redact.redact(f"v {h} w") == (f"v {h} w", 0)


def test_residual_is_still_the_prefix_check_and_is_not_an_entropy_check():
    t = "Zq8" + "vK2mN5pQ8sT1vW4xZ7aB3cD9eF"
    assert redact.residual(f"saw {t}") is False
    assert redact.residual("saw " + "sk-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1") is True


def test_a_bare_token_never_reaches_the_provider_or_the_ledger():
    t = "Zq8" + "vK2mN5pQ8sT1vW4xZ7aB3cD9eF"
    prov = adapter.FixtureProvider(lambda s, q, m: {k: {"type": "noul", "noul": 0.9} for k in q})
    ask.run(ask.verify_specs(), {"claim": "deployed", "evidence": f"output line\n{t}\nend"}, provider=prov, session="s")
    assert t not in json.dumps(prov.calls)
    assert all(t not in (r.state_text or "") for r in ledger.open().all("SELECT state_text FROM jev_calls"))


def test_a_command_with_a_sha_a_pod_name_or_a_path_stays_approvable():
    """A redaction makes a gated command unapprovable (the human cannot read what the hash covers), so a false positive here is a real cost."""
    for cmd in (
        "git push origin " + "9fceb02d0ae598e95dc970b74767f19372d61af8" + ":refs/heads/topic",
        "kubectl delete pod web-5d8f7c9b6-x7k2p -n prod",
        "rm -rf build/Release-iphonesimulator/MyApp.app/Frameworks/Alamofire.framework",
        "docker rmi ghcr.io/org/app@sha256:" + "a" * 8 + "0123456789abcdef" * 3 + "01234567",
        "terraform destroy -target=module.Network2.aws_subnet.PrivateSubnet2",
    ):
        assert redact.redact(cmd, secrets_only=True) == (cmd, 0), cmd


@pytest.mark.parametrize("unit", ["Ab1", "Zq8vK2mN5pQ8sT1vW4xZ", "a" * 19 + " ", "Ab1" * 6 + "-", "Ab1/" * 8, "x" * 19 + "A1 "])
def test_the_entropy_pass_is_linear(unit):
    n = 150_000
    for text in (unit * (n // len(unit)), ("x " + unit) * (n // (len(unit) + 2))):
        t0 = time.monotonic()
        redact.redact(text)
        assert time.monotonic() - t0 < 4, f"super-linear on {unit!r}"
