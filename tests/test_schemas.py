"""Contract tests: every schema compiles, every example validates, negative cases fail,
cross-field invariants hold, policy files parse and reference only known ids."""
import copy
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
EXAMPLES = SCHEMAS / "examples"
POLICIES = ROOT / "policies"
NAMES = ["decision", "question", "permit", "outcome", "work-item", "improvement"]


def _registry() -> Registry:
    resources = []
    for p in SCHEMAS.glob("*.schema.json"):
        doc = json.loads(p.read_text())
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


REGISTRY = _registry()


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=REGISTRY)


def example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text())


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.mark.parametrize("name", NAMES)
def test_schema_compiles_and_example_validates(name):
    validator(name).validate(example(name))


@pytest.mark.parametrize("name", NAMES)
def test_additional_properties_rejected(name):
    doc = example(name)
    doc["unexpected_field"] = 1
    with pytest.raises(ValidationError):
        validator(name).validate(doc)


@pytest.mark.parametrize("name", NAMES)
def test_every_required_field_is_required(name):
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    v = validator(name)
    for field in schema["required"]:
        doc = copy.deepcopy(example(name))
        del doc[field]
        with pytest.raises(ValidationError, match=field):
            v.validate(doc)


def test_decision_status_enum_covers_state_machine_doc():
    schema = json.loads((SCHEMAS / "decision.schema.json").read_text())
    states = set(schema["properties"]["status"]["enum"])
    doc = (ROOT / "docs" / "02-contracts-and-state-machine.md").read_text()
    mentioned = set(re.findall(r"\b([A-Z][A-Z_]{4,})\b", doc.split("## 3. Contracts")[0]))
    mentioned -= {"RFC", "UTC", "JSON", "FAST", "JEV", "DEEP", "ESCALATE", "TTY", "CLI", "JADE", "PENDING", "ISSUED", "CONSUMED", "REVOKED", "IMMEDIATE", "BEGIN", "REUSE", "MODEL_DRIFT", "QUESTION_DRIFT", "WAITING_", "INVALID_QUESTION", "UNAVAILABLE", "RATE_LIMITED"}
    missing = mentioned - states
    assert not missing, f"states in docs/02 missing from schema enum: {missing}"


def test_decision_timestamps_ordered():
    d = example("decision")["timestamps"]
    assert ts(d["created_at"]) <= ts(d["updated_at"]) < ts(d["expires_at"])


def test_decision_tenant_scope_is_local_prefixed():
    doc = example("decision")
    doc["tenant_scope"] = "remote:someone"
    with pytest.raises(ValidationError):
        validator("decision").validate(doc)
    doc["tenant_scope"] = "local:other-user"
    validator("decision").validate(doc)


def test_permit_high_risk_requires_tty_approval():
    v = validator("permit")
    doc = example("permit")
    assert doc["risk_level"] == "high" and doc["state"] == "ISSUED"
    del doc["approval_reference"]
    with pytest.raises(ValidationError, match="approval_reference"):
        v.validate(doc)
    # a PENDING high-risk permit may lack approval (it is not yet usable)
    doc["state"] = "PENDING"
    v.validate(doc)
    # low risk never needs approval
    doc = example("permit")
    del doc["approval_reference"]
    doc["risk_level"] = "low"
    v.validate(doc)


def test_permit_single_use_and_expiry_invariants():
    p = example("permit")
    assert p["usage_limit"] == 1
    assert ts(p["expires_at"]) > ts(p["issued_at"])
    assert (ts(p["expires_at"]) - ts(p["issued_at"])).total_seconds() <= 3600, "permit TTL must stay short"
    p["uses"] = 2
    with pytest.raises(ValidationError):
        validator("permit").validate(p)
    p = example("permit")
    p["approval_reference"] = "agent:claude@2026-11-08T21:05:00Z"   # only tty:* is an approval
    with pytest.raises(ValidationError, match="approval_reference"):
        validator("permit").validate(p)


def test_outcome_unknown_has_null_success_and_vice_versa():
    v = validator("outcome")
    o = example("outcome")
    o["observation_type"] = "unknown"
    with pytest.raises(ValidationError):
        v.validate(o)             # success=true with unknown observation
    o["success"] = None
    v.validate(o)
    o = example("outcome")
    o["success"] = None
    with pytest.raises(ValidationError):
        v.validate(o)             # observed outcome must carry a boolean


def test_question_thresholds_and_criteria():
    v = validator("question")
    q = example("question")
    assert q["threshold_policy"]["accept"] > q["threshold_policy"]["reject"]
    q["template"]["criteria"] = {"only": "one option"}      # degenerate choice-style criteria
    with pytest.raises(ValidationError):
        v.validate(q)
    q = example("question")
    q["primitive"] = "verify"                               # not an API primitive (inventory S5)
    with pytest.raises(ValidationError):
        v.validate(q)


def test_improvement_cannot_target_protected_components():
    v = validator("improvement")
    for protected in ["execution_authorization", "safety_invariant", "promotion_rule", "human_approval", "protected_holdout", "secret_access", "audit_log"]:
        doc = example("improvement")
        doc["target_component"] = protected
        with pytest.raises(ValidationError):
            v.validate(doc)


def test_work_items_file_validates_and_dependencies_resolve():
    v = validator("work-item")
    items = [json.loads(line) for line in (ROOT / "work-items.jsonl").read_text().splitlines() if line.strip()]
    ids = {i["id"] for i in items}
    assert len(ids) == len(items), "duplicate work item ids"
    for it in items:
        v.validate(it)
        for dep in it["dependencies"]:
            assert dep in ids, f"{it['id']} depends on unknown {dep}"
        if it["parent"]:
            assert it["parent"] in ids
        if it["status"] == "waiting_human":
            assert it["owner"] == "human", f"{it['id']} waits on a human but is not owned by one"
        if it["status"] == "blocked":
            assert it["dependencies"], f"{it['id']} is blocked without a dependency"


def test_backlog_markdown_lists_every_work_item():
    md = (ROOT / "BACKLOG.md").read_text()
    for line in (ROOT / "work-items.jsonl").read_text().splitlines():
        if line.strip():
            wid = json.loads(line)["id"]
            assert re.search(rf"^\| {wid} \|", md, re.M), f"{wid} missing from BACKLOG.md"


# ---- policies -------------------------------------------------------------

def load_policy(name):
    return yaml.safe_load((POLICIES / f"{name}.yaml").read_text())


def test_policies_parse_and_have_schema_version():
    for n in ["routing", "risk", "escalation", "safety", "promotion"]:
        p = load_policy(n)
        assert p["schema_version"] == "1.0.0"


def test_risk_rules_ordered_by_severity_and_regexes_compile():
    risk = load_policy("risk")
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    levels = [order[r["level"]] for r in risk["rules"]]
    assert levels == sorted(levels), "risk rules must be ordered critical -> low (first match wins)"
    assert risk["rules"][-1]["match"] == ".*", "last rule must be the default"
    ids = [r["id"] for r in risk["rules"]]
    assert len(ids) == len(set(ids))
    for r in risk["rules"]:
        re.compile(r["match"])
        assert re.fullmatch(r"R-\d{2}", r["id"])
    assert risk["permit_required_at"] in order and risk["human_approval_required_at"] in order


@pytest.mark.parametrize("cmd,expected", [
    ("git push origin v1-feature-x", "R-10"),
    ("git push --force origin feature/x", "R-10"),            # first match wins: push rule precedes force rule
    ("composio execute GMAIL_FETCH_EMAILS", "R-10"),           # any connected-app action is high (Q3b)
    ("sudo pfctl -f /etc/pf.conf", "R-01"),
    ("kubectl apply -f deploy.yaml", "R-03"),
    ("ovault secret set FOO --project p --file /dev/stdin", "R-04"),
    ("rm -rf /private/tmp/x", "R-99"),
    ("rm -rf ./build", "R-99"),
    ("rm -rf ~/Documents/x", "R-11"),
    ("launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.x.plist", "R-12"),
    ("python3 ~/.claude/hooks/x.py && sed -i '' s/a/b/ ~/.claude/settings.json", "R-13"),
    ("ssh admin@host.example.net 'uptime'", "R-14"),            # any remote shell is high (Q3b)
    ("rsync -a ./site/ host.example.net:/srv/www", "R-14"),
    ("ssh-keygen -t ed25519 -f ./k", "R-99"),                  # not a remote shell
    ("git rebase -i HEAD~3", "R-20"),
    ("git commit -m x", "R-90"),                               # commits are low (Q3b)
    ("pip install requests", "R-90"),                          # installs are low (Q3b)
    ("curl -o x.tar.gz https://example.net/x.tar.gz", "R-90"),
    ("uv run pytest -q", "R-99"),
])
def test_risk_classifier_precedence(cmd, expected):
    from policies.loader import classify, load_risk
    assert classify(load_risk(), cmd)["id"] == expected


def test_safety_invariants_block_patterns_compile_and_catch_known_cases():
    safety = load_policy("safety")
    blocks = {i["id"]: re.compile(i["block"]) for i in safety["invariants"] if "block" in i}
    public_bind = ".".join(["0"] * 4)                       # built, not literal: a shell guard matches the literal
    assert blocks["SI-02"].search("OLLAMA_HOST=" + public_bind + " ollama serve")
    assert blocks["SI-02"].search("uvicorn app:app --host " + public_bind + " --port 8000")
    assert not blocks["SI-02"].search("curl http://127.0.0.1:3333/search?q=x")
    assert blocks["SI-05"].search("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz0123")
    assert blocks["SI-08"].search("git push origin main")
    assert not blocks["SI-08"].search("git push origin feature/main-menu")
    protected = set(load_policy("safety")["protected_targets"])
    schema = json.loads((SCHEMAS / "improvement.schema.json").read_text())
    allowed = set(schema["properties"]["target_component"]["enum"])
    assert not (allowed & protected)


def test_escalation_thresholds_consistent_with_question_example():
    esc = load_policy("escalation")
    q = example("question")
    fam = esc["families"][q["task_family"]]
    assert fam["noul"]["accept"] == q["threshold_policy"]["accept"]
    assert fam["noul"]["reject"] == q["threshold_policy"]["reject"]
    for name, f in esc["families"].items():
        if "noul" in f:
            assert 0 <= f["noul"]["reject"] < f["noul"]["accept"] <= 1, name
        for lvl, over in f.get("by_risk", {}).items():
            assert over["accept"] >= f["noul"]["accept"], f"{name}/{lvl} must not loosen"
    codes = esc["exit_codes"]
    assert codes["accept"] == 0 and len(set(codes.values())) == len(codes)


def test_routing_preflight_ids_unique_and_routes_known():
    routing = load_policy("routing")
    ids = [p["id"] for p in routing["preflight"]]
    assert len(ids) == len(set(ids))
    known = set(routing["routes"]) | {"NEEDS_EVIDENCE", "NEEDS_REVISION", "REUSE"}
    for p in routing["preflight"]:
        for k in ("on_true", "on_false"):
            if k in p:
                assert p[k] in known, p
    assert "cache" not in routing, "the cache block was read by no code and is deleted (W-53); the session dedup is the only reuse"


def test_promotion_auto_scope_is_subset_of_allowed_targets():
    promo = load_policy("promotion")
    schema = json.loads((SCHEMAS / "improvement.schema.json").read_text())
    allowed = set(schema["properties"]["target_component"]["enum"])
    assert set(promo["promote"]["auto_allowed_for"]) <= allowed
    assert set(promo["promote"]["human_required_for"]) | set(promo["promote"]["auto_allowed_for"]) == allowed
    assert promo["canary"]["requires_human_opt_in"] is True
    assert "test" not in promo["offline_replay"]["datasets"] and "protected" not in promo["offline_replay"]["datasets"]
