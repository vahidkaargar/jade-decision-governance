"""W-98b: the human-only classes HO-delete, HO-money and HO-release (docs/12 section 4).

A call in one of these classes is never decided by memory or by Jev (0.6.0): a person is asked every time. In 0.5.0 a level raises the call to high, so a person is asked
instead of the call running. The cost of a false positive is a click for a harmless command, so every pattern has a corpus of commands it must NOT match
(`git fetch --prune`, `get_invoice`, `docker push localhost:5000/x`, `rm -rf node_modules` ...). The classes name what the gate can read; a program that does these things
through a script in another language, or through a tool the lists do not know, is a residual (docs/05).
"""

import json
import os
import subprocess

import pytest

from jade import humanonly, ledger, paths, policy

CWD = "/tmp/proj-w98b"


@pytest.fixture(autouse=True)
def level(monkeypatch):
    cfg = paths.config()
    cfg["level"] = "high"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))


def ids(cmd, cwd=CWD):
    return policy.assess("Bash", {"command": cmd}, cwd).ho_ids


def tool_ids(tool, tool_input=None):
    return policy.assess(tool, tool_input or {}, CWD).ho_ids


DELETE = [
    "rm -rf ./data", "rm -rf ~/Documents/old", "rm -r src/legacy", "rm -Rf /Users/x/project", "sudo rm -rf /var/lib/app", "rm -rf build_out ./dist/../keep",
    "rm -rf node_modules ./data",   # one unsafe operand is enough
    "find . -name '*.bak' -delete", "find /srv -type f -mtime +30 -exec rm {} +",
    "shred -u secrets.txt", "git clean -fd", "git clean -ffdx", "git push --force origin topic", "git push -f", "git push origin --delete old-branch",
    "git push origin :old-branch", "git push origin +topic", "git push --mirror backup", "git reflog expire --expire=now --all", "git gc --prune=now",
    "git filter-branch --tree-filter 'rm x' HEAD", "git filter-repo --path x --invert-paths",
    "diskutil eraseDisk APFS X disk3", "dd if=/dev/zero of=/dev/disk2", "docker system prune -af", "docker volume rm data", "docker image prune -a",
    "kubectl delete pod web-1", "helm uninstall web", "terraform destroy", "aws s3 rm s3://bucket --recursive", "aws s3 rb s3://bucket --force",
    "aws ec2 terminate-instances --instance-ids i-1", "gcloud compute instances delete vm1", "gh repo delete me/x --yes", "gh release delete v1.0.0",
    "curl -X DELETE https://api.example.com/items/1", "psql -c 'DROP TABLE users'", "mysql -e 'truncate table logs'", "redis-cli flushall",
    "sqlite3 db.sqlite 'delete from users'", "rsync -a --delete src/ dst/", "dropdb prod",
]
DELETE_NOT = [
    "rm -rf node_modules", "rm -rf ./node_modules ./dist ./build", "rm -rf .venv", "rm -rf /tmp/scratch-1", "rm -rf /private/tmp/x/y", "rm -rf $TMPDIR/build",
    "rm -rf target", "rm -rf __pycache__ .pytest_cache", "rm file.txt", "rm -f a.log", "rm -i old.txt", "git fetch --prune", "git branch -d merged", "git branch -D topic",
    "git clean -n", "git clean -nfd", "git clean --dry-run -fd", "git push origin feature", "git push -u origin my-branch", "git status",
    "git reset --hard HEAD~1", "docker ps", "docker rm -f web", "docker volume ls", "kubectl get pods", "kubectl describe pod x", "helm list", "terraform plan", "aws s3 ls",
    "aws ec2 describe-instances", "gcloud compute instances list", "gh repo view me/x", "gh release list", "curl https://api.example.com/items/1", "curl -X GET https://x/y",
    "psql -c 'select 1'", "mysql -e 'select * from t'", "redis-cli get k", "sqlite3 db.sqlite 'delete from users where id = 1'", "rsync -a src/ dst/", "rsync -an --delete src/ dst/",
    "find . -name '*.py' -print", "find . -type f | wc -l", "echo rm -rf /", "grep -r 'rm -rf' docs", "ls -la",
]
MONEY = [
    "stripe refunds create --charge ch_1 --live", "stripe charges create --amount 100 --currency usd --live", "stripe payouts create --amount 100 --live",
    "stripe --live transfers create --amount 5 --destination acct_1", "stripe payment_intents confirm pi_1 --api-key sk_live_x",
    "curl -X POST https://api.stripe.com/v1/refunds -d charge=ch_1", "curl https://api.stripe.com/v1/charges -d amount=100", "curl -u k: https://api.stripe.com/v1/payouts --data amount=1",
    "curl -X POST https://api-m.paypal.com/v2/payments/captures/1/refund", "curl --request POST https://api.squareup.com/v2/payments", "wget --post-data=a=1 https://api.plaid.com/transfer/create",
    "bitcoin-cli sendtoaddress bc1q 0.1", "cast send 0xabc 'transfer(address,uint256)'", "solana transfer ADDR 1", "lncli sendpayment --pay_req=x",
    "aws ec2 purchase-reserved-instances-offering --reserved-instances-offering-id x", "aws savingsplans create-savings-plan --savings-plan-offering-id x",
]
MONEY_NOT = [
    "stripe listen", "stripe logs tail", "stripe charges list", "stripe charges create --amount 100 --currency usd", "stripe trigger payment_intent.succeeded",
    "stripe refunds create --charge ch_1", "stripe customers list --live", "stripe events list --live", "stripe balance retrieve --live",
    "curl https://api.stripe.com/v1/charges -H 'Authorization: Bearer x'", "curl -X GET https://api.stripe.com/v1/balance", "curl -X POST https://api.example.com/v1/refunds -d a=1",
    "curl -X POST https://api-m.sandbox.paypal.com/v1/payments", "bitcoin-cli getbalance", "cast call 0xabc 'balanceOf(address)'", "solana balance", "aws ec2 describe-reserved-instances",
    "aws savingsplans describe-savings-plans", "grep -r stripe src", "npm install stripe",
]
RELEASE = [
    "npm publish", "npm publish --access public", "pnpm publish", "yarn publish", "cargo publish", "twine upload dist/*", "uv publish", "poetry publish", "gem push x.gem",
    "pod trunk push X.podspec", "helm push chart.tgz oci://registry.example.com/charts", "docker push me/app:1", "docker push ghcr.io/me/app:1", "podman push quay.io/me/x",
    "docker buildx build --push -t me/app .", "gh release create v1.2.3", "gh release edit v1.2.3 --draft=false", "gh release upload v1.2.3 dist/*",
    "git push origin v0.5.0", "git push origin v1.2.3-rc1", "git push --tags", "git push origin --tags", "git push --follow-tags", "git push origin refs/tags/x",
    "git push origin :refs/tags/old", "vercel --prod", "netlify deploy --prod", "firebase deploy", "wrangler deploy", "wrangler publish", "serverless deploy --stage prod", "cdk deploy",
    "gh api -X POST repos/me/x/releases -f tag_name=v1", "npm unpublish x@1.0.0",
]
RELEASE_NOT = [
    "docker push localhost:5000/x", "docker push 127.0.0.1:5000/x", "docker push [::1]:5000/x", "npm publish --dry-run", "cargo publish --dry-run", "uv publish --dry-run",
    "twine check dist/*", "git push origin feature-x", "git push -u origin my-branch", "git push origin HEAD", "git tag -l", "git tag v1.2.3", "git tag -d v1.2.3", "git fetch --tags",
    "gh release list", "gh release view v1", "gh release download v1", "gh pr create --fill", "gh pr merge 5 --merge", "npm run publish-docs", "npm install", "npm pack",
    "echo npm publish", "grep -r 'npm publish' docs", "vercel", "vercel --help", "netlify deploy", "firebase emulators:start", "docker build -t x .", "docker pull me/app:1",
    "git push origin release/1.2.3", "git log --tags",
]


@pytest.mark.parametrize("cmd", DELETE)
def test_delete_is_human_only(cmd):
    assert humanonly.HO_DELETE in ids(cmd), cmd


@pytest.mark.parametrize("cmd", DELETE_NOT)
def test_not_delete(cmd):
    assert humanonly.HO_DELETE not in ids(cmd), cmd


@pytest.mark.parametrize("cmd", MONEY)
def test_money_is_human_only(cmd):
    assert humanonly.HO_MONEY in ids(cmd), cmd


@pytest.mark.parametrize("cmd", MONEY_NOT)
def test_not_money(cmd):
    assert humanonly.HO_MONEY not in ids(cmd), cmd


@pytest.mark.parametrize("cmd", RELEASE)
def test_release_is_human_only(cmd):
    assert humanonly.HO_RELEASE in ids(cmd), cmd


@pytest.mark.parametrize("cmd", RELEASE_NOT)
def test_not_release(cmd):
    assert humanonly.HO_RELEASE not in ids(cmd), cmd


def test_a_class_never_changes_the_rule_or_the_level():
    a = policy.assess("Bash", {"command": "stripe refunds create --charge ch_1 --live"}, CWD)
    assert humanonly.HO_MONEY in a.ho_ids and a.rule_id == "R-99" and a.level == "low"
    b = policy.assess("Bash", {"command": "rm -rf ./data"}, CWD)
    assert humanonly.HO_DELETE in b.ho_ids and b.rule_id == "R-11" and b.level == "high"


def test_each_command_of_a_chain_is_read():
    assert {humanonly.HO_DELETE, humanonly.HO_RELEASE} <= set(ids("ls && rm -rf ./data; npm publish"))
    assert ids("ls && rm -rf node_modules; npm test") == []


def test_a_quoted_or_echoed_name_is_data():
    assert ids("echo 'npm publish and rm -rf ./data and stripe refunds create --live'") == []
    assert ids("git commit -m 'rm -rf ./data; npm publish'") == []


# ------------------------------------------------------------------ MCP tools by name

MCP_YES = [
    ("mcp__stripe__create_refund", humanonly.HO_MONEY), ("mcp__stripe__refund_payment", humanonly.HO_MONEY), ("mcp__payments__make_payment", humanonly.HO_MONEY),
    ("mcp__bank__send_money", humanonly.HO_MONEY), ("mcp__shop__place_order", humanonly.HO_MONEY), ("mcp__x__payout", humanonly.HO_MONEY),
    ("mcp__stripe__pay_invoice", humanonly.HO_MONEY), ("mcp__stripe__transfer_funds", humanonly.HO_MONEY), ("mcp__brex__transfer", humanonly.HO_MONEY),
    ("mcp__github__create_release", humanonly.HO_RELEASE), ("mcp__npm__publish_package", humanonly.HO_RELEASE), ("mcp__blog__publish_post", humanonly.HO_RELEASE),
    ("mcp__fs__delete_directory", humanonly.HO_DELETE), ("mcp__db__drop_table", humanonly.HO_DELETE), ("mcp__s3__purge_bucket", humanonly.HO_DELETE),
    ("mcp__k8s__destroy_cluster", humanonly.HO_DELETE),
]
MCP_NO = [
    "mcp__stripe__get_invoice", "mcp__stripe__list_charges", "mcp__stripe__retrieve_balance", "mcp__github__list_releases", "mcp__github__get_release", "mcp__github__create_pull_request",
    "mcp__fs__file_transfer", "mcp__github__transfer_issue", "mcp__x__search_orders", "mcp__stripe__create_customer", "mcp__db__list_deleted_rows", "mcp__github__get_file_contents",
    "mcp__docs__read_page", "mcp__shop__get_order", "mcp__blog__preview_publish", "mcp__stripe__estimate_payout",
]


@pytest.mark.parametrize(("tool", "klass"), MCP_YES)
def test_an_mcp_tool_is_classed_by_what_its_name_does(tool, klass):
    assert klass in tool_ids(tool, {"x": 1}), tool


@pytest.mark.parametrize("tool", MCP_NO)
def test_an_mcp_tool_that_reads_or_does_something_else_is_not_classed(tool):
    assert not ({humanonly.HO_DELETE, humanonly.HO_MONEY, humanonly.HO_RELEASE} & set(tool_ids(tool, {"x": 1}))), tool


def test_an_mcp_tool_that_carries_a_command_is_read_as_that_command():
    assert humanonly.HO_DELETE in tool_ids("mcp__Desktop_Commander__start_process", {"command": "rm -rf ./data"})
    assert tool_ids("mcp__Desktop_Commander__start_process", {"command": "ls"}) == []


# ------------------------------------------------------------------ tag lookup: a name that is a tag in this repository

def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git("init", "-q", "-b", "main", cwd=r)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x", cwd=r)
    return r


def test_a_push_of_a_name_that_is_a_tag_here_is_a_release_push(repo):
    git("tag", "release-candidate", cwd=repo)
    assert humanonly.HO_RELEASE in ids("git push origin release-candidate", str(repo))
    assert humanonly.HO_RELEASE not in ids("git push origin topic", str(repo))


def test_a_packed_tag_is_found_too(repo):
    git("tag", "stable", cwd=repo)
    git("pack-refs", "--all", cwd=repo)
    assert not (repo / ".git" / "refs" / "tags" / "stable").exists()
    assert humanonly.HO_RELEASE in ids("git push origin stable", str(repo))


def test_a_branch_with_the_same_name_as_no_tag_is_not_a_tag(repo):
    git("branch", "stable", cwd=repo)
    assert humanonly.HO_RELEASE not in ids("git push origin stable", str(repo))


def test_the_lookup_finds_the_repository_from_a_subdirectory_and_from_a_dash_c(repo):
    git("tag", "v-lookup-1", cwd=repo)
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert humanonly.HO_RELEASE in ids("git push origin v-lookup-1", str(sub))
    assert humanonly.HO_RELEASE in ids(f"git -C {repo} push origin v-lookup-1", "/tmp")


def test_a_directory_that_is_not_a_repository_has_no_tags(tmp_path):
    assert humanonly.HO_RELEASE not in ids("git push origin sometag", str(tmp_path))


# ------------------------------------------------------------------ a script the command runs is read too

def test_a_script_file_the_command_runs_is_read_for_the_classes(tmp_path):
    s = tmp_path / "ship.sh"
    s.write_text("#!/bin/sh\nset -e\nnpm test\nnpm publish\nrm -rf ./data\nstripe refunds create --charge c --live\n")
    got = set(ids(f"sh {s}", str(tmp_path)))
    assert {humanonly.HO_RELEASE, humanonly.HO_DELETE, humanonly.HO_MONEY} <= got


def test_a_harmless_script_adds_no_class(tmp_path):
    s = tmp_path / "ok.sh"
    s.write_text("#!/bin/sh\nnpm test\nrm -rf node_modules\necho 'npm publish'\n")
    assert not ({humanonly.HO_RELEASE, humanonly.HO_DELETE, humanonly.HO_MONEY} & set(ids(f"sh {s}", str(tmp_path))))


def test_a_script_over_one_mebibyte_is_not_read(tmp_path):
    s = tmp_path / "big.sh"
    s.write_text("x" * (1 << 20) + "\nnpm publish\n")
    assert humanonly.HO_RELEASE not in ids(f"sh {s}", str(tmp_path))


def test_a_missing_script_is_not_a_class(tmp_path):
    assert ids(f"sh {tmp_path}/nope.sh", str(tmp_path)) == []


# ------------------------------------------------------------------ the gate

def test_a_level_raises_a_class_call_to_high_even_when_its_rule_is_low():
    from jade import hooks

    ev = {"tool_name": "Bash", "tool_input": {"command": "stripe refunds create --charge ch_1 --live"}, "session_id": "s-w98b", "cwd": CWD, "permission_mode": "auto"}
    os.environ["JADE_GATE_MODE"] = "enforce"
    try:
        r = hooks.pretool(ev)
    finally:
        os.environ.pop("JADE_GATE_MODE", None)
    assert r.exit_code == 2 and "[jade:ask_" in r.stderr
    d = ledger.open(timeout=5)
    try:
        assert "HO-money" in d.con.execute("SELECT question_json FROM asks WHERE session_id=?", ("s-w98b",)).fetchone()[0]   # the question names the class
    finally:
        d.close()


def test_without_a_level_a_class_changes_nothing():
    cfg = paths.config()
    cfg.pop("level", None)
    (paths.config_dir() / "config.json").write_text(json.dumps(cfg))
    from jade import hooks

    ev = {"tool_name": "Bash", "tool_input": {"command": "stripe refunds create --charge ch_1 --live"}, "session_id": "s-w98b-2", "cwd": CWD, "permission_mode": "auto"}
    os.environ["JADE_GATE_MODE"] = "enforce"
    try:
        r = hooks.pretool(ev)
    finally:
        os.environ.pop("JADE_GATE_MODE", None)
    assert r.exit_code == 0


def test_the_class_ids_are_the_documented_ones():
    assert (humanonly.HO_DELETE, humanonly.HO_MONEY, humanonly.HO_RELEASE) == ("HO-delete", "HO-money", "HO-release")
    assert policy.HO_DELETE == humanonly.HO_DELETE
