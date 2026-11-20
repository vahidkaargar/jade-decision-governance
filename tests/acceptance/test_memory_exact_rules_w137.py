"""W-137: what memory may generalise. A call that makes git run a program (R-15 `git config alias.x '!..'`, `core.sshCommand`, a `GIT_SSH_COMMAND` variable) or that
writes a git config file from the shell (R-23) is remembered by its exact text at most, never as a kind: one confirmed `git config alias.p '!sh a'` must not cover
`git config alias.p '!sh b'`. The critical rules (R-01 firewall, R-03 deploy, R-04 key and secret store) are never remembered at all (V6-06); that is pinned here too,
with the calls named in the work item, so that it cannot slip back.

R-12 (service lifecycle: `launchctl`, `brew services`, `docker compose up|down`) stays a kind: whether it should be exact-only is an owner decision (W-137).
"""

import pytest

from jade import memory, policy, scopes

CRITICAL_NEVER = [
    ("sudo pfctl -f /etc/pf.conf", "R-01"),
    ("sudo ufw allow 22", "R-01"),
    ("sudo iptables -F", "R-01"),
    ("kubectl apply -f x.yaml", "R-03"),
    ("helm upgrade x y", "R-03"),
    ("terraform apply", "R-03"),
    ("kubectl rollout restart deploy/x", "R-03"),
    ("security delete-generic-password -s x", "R-04"),
    ("security add-generic-password -s x -w y", "R-04"),
]

EXACT_ONLY = [
    ("git config alias.p '!sh x'", "R-15"),
    ("git config --global alias.p '!sh x'", "R-15"),
    ("git config --global core.pager 'sh x'", "R-15"),
    ("git config core.sshCommand 'ssh -i x'", "R-15"),
    ("git config --file a.cfg alias.x '!x'", "R-15"),
    ("GIT_SSH_COMMAND=id git fetch", "R-15"),
    ("git -c core.sshCommand=x status", "R-15"),
    ("cp x ~/.gitconfig", "R-23"),
    ("printf '[alias]\\n' >> /work/repo/.git/config", "R-23"),
]


def _a(cmd, cwd="/tmp"):
    return policy.assess("Bash", {"command": cmd}, cwd)


@pytest.mark.parametrize("cmd,rule", CRITICAL_NEVER)
def test_a_critical_call_is_never_remembered(cmd, rule):
    a = _a(cmd)
    assert (a.rule_id, a.level) == (rule, "critical")
    assert memory.eligibility(a, "Bash", {"command": cmd}, {}) == "never"


@pytest.mark.parametrize("cmd,rule", EXACT_ONLY)
def test_a_call_that_runs_a_program_through_git_is_exact_only(cmd, rule):
    a = _a(cmd)
    assert a.rule_id == rule and a.level == "high"
    assert memory.eligibility(a, "Bash", {"command": cmd}, {}) == "exact"


@pytest.mark.parametrize("cmd", [c for c, _ in EXACT_ONLY])
def test_such_a_call_has_no_kind_key_and_no_kind_scope(cmd):
    a = _a(cmd)
    tin = {"command": cmd}
    exact, cls, el = memory.keys(a, "Bash", tin, {})
    assert exact and cls is None and el == "exact"
    assert not [s for s in scopes.offered(a, "Bash", tin, {}, "/tmp") if s.kind == "kind_project"]


def test_a_chain_with_one_such_segment_is_exact_only():
    """The rule of a later segment counts, as for R-14: a plain top rule does not hide it."""
    cmd = "docker compose up -d && git config alias.p '!sh x'"
    a = _a(cmd)
    assert "R-15" in a.seg_rules
    assert memory.eligibility(a, "Bash", {"command": cmd}, {}) == "exact"


def test_the_two_such_calls_do_not_share_an_answer():
    """The point of the fix: the answer to the first call does not decide the second."""
    one, two = "git config alias.p '!sh a'", "git config alias.p '!sh b'"
    assert memory.keys(_a(one), "Bash", {"command": one}, {})[0] != memory.keys(_a(two), "Bash", {"command": two}, {})[0]


def test_control_an_ordinary_high_call_is_still_a_kind():
    """The fix is narrow: R-12 and R-11 calls are still class-eligible (R-12 is an owner decision, listed in W-137)."""
    for cmd in ("docker compose up -d", "git reset --hard HEAD~1"):
        assert memory.eligibility(_a(cmd), "Bash", {"command": cmd}, {}) == "class", cmd
