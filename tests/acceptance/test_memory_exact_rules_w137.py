"""W-137: what memory may generalise. A call that makes git run a program (R-15 `git config alias.x '!..'`, `core.sshCommand`, a `GIT_SSH_COMMAND` variable) or that
writes a git config file from the shell (R-23) is remembered by its exact text at most, never as a kind: one confirmed `git config alias.p '!sh a'` in one project must
not cover the same call in another project (on main the kind key, and so the `kind_any` scope, did). The kind key always held the quoted words, so `'!sh b'` was a
different kind on main too. The critical rules (R-01 firewall, R-03 deploy, R-04 key and secret store) are never remembered at all (V6-06); that is pinned here too,
with the calls named in the work item, so that it cannot slip back.

R-12 (service lifecycle), owner decision 2026-11-20: `launchctl bootout`, `unload` and `remove` stop a service and take it out of launchd, so they are remembered by the
exact text only (one answer for `launchctl bootout gui/501/com.a` in one project must not cover the same bootout in every other project; another service, such
as the agent that runs JADE's own upkeep, was a different kind on main too, because the kind key holds the target). The other R-12
calls (`launchctl bootstrap|load|kickstart`, `brew services`, `docker compose up|down`) stay a kind.

`security delete-internet-password` and `security delete-keychain` are in HO-delete (a human-only class, so never remembered): see test_human_only_w98b.py.
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


def _no_kind_answer(cmd, cwds=("/work/one", "/work/two")):
    """The call has no kind key and is offered no kind scope in any of `cwds`, so an answer given in one project cannot cover the same text in another (on main
    the `kind_any` key of the same text was the same in every project). Exact keys always differ between two texts, so comparing them proves nothing."""
    tin = {"command": cmd}
    for cwd in cwds:
        a = policy.assess("Bash", tin, cwd)
        assert memory.keys(a, "Bash", tin, {})[1] is None, (cmd, cwd)
        assert not [s.kind for s in scopes.offered(a, "Bash", tin, {}, cwd) if s.kind in ("kind_project", "kind_any")], (cmd, cwd)


def test_the_two_such_calls_do_not_share_an_answer():
    """The point of the fix: an answer in one project does not decide the same call in another project, nor the other call."""
    one, two = "git config alias.p '!sh a'", "git config alias.p '!sh b'"
    for cmd in (one, two):
        _no_kind_answer(cmd)
    assert memory.keys(_a(one), "Bash", {"command": one}, {})[0] != memory.keys(_a(two), "Bash", {"command": two}, {})[0]


def test_control_an_ordinary_high_call_is_still_a_kind():
    """The fix is narrow: R-11 calls and the R-12 calls that start or restart a service are still class-eligible."""
    for cmd in ("docker compose up -d", "git reset --hard HEAD~1"):
        assert memory.eligibility(_a(cmd), "Bash", {"command": cmd}, {}) == "class", cmd


LAUNCHCTL_STOP = [
    "launchctl bootout gui/501/com.x",
    "launchctl unload ~/Library/LaunchAgents/x.plist",
    "launchctl unload -w x.plist",
    "launchctl remove com.x",
    "/bin/launchctl bootout gui/501/x",
    "launchctl  bootout gui/501/x",
    "'launchctl' bootout gui/501/x",
    "env launchctl bootout gui/501/x",
    "command launchctl remove com.x",
    "nohup launchctl unload x.plist",
    "time launchctl bootout gui/501/x",
    "cd /tmp && launchctl bootout gui/501/x",
    "(launchctl bootout gui/501/x)",
    "xargs launchctl bootout < f",
    "docker compose up -d && launchctl bootout gui/501/x",
    "launchctl bootout gui/501/x; launchctl bootstrap gui/501 x.plist",
]

LAUNCHCTL_KIND = [
    "launchctl bootstrap gui/501 x.plist",
    "launchctl kickstart -k gui/501/x",
    "launchctl load x.plist",
    "brew services restart x",
    "docker compose down",
]


@pytest.mark.parametrize("cmd", LAUNCHCTL_STOP)
def test_launchctl_bootout_unload_remove_is_exact_only(cmd):
    """Owner decision (W-137, R-12): a call that takes a service out of launchd is read from the exact text."""
    a = _a(cmd)
    assert a.level == "high" and "R-12" in ({a.rule_id, *a.seg_rules}), (a.rule_id, a.seg_rules)
    tin = {"command": cmd}
    assert memory.eligibility(a, "Bash", tin, {}) == "exact"
    exact, cls, el = memory.keys(a, "Bash", tin, {})
    assert exact and cls is None and el == "exact"
    assert not [s for s in scopes.offered(a, "Bash", tin, {}, "/tmp") if s.kind == "kind_project"]


def test_two_launchctl_bootouts_do_not_share_an_answer():
    """An answer for one bootout in one project does not cover the same bootout in another project (on main its `kind_any` key did)."""
    one, two = "launchctl bootout gui/501/com.a", "launchctl bootout gui/501/com.jade.maintain"
    for cmd in (one, two):
        _no_kind_answer(cmd)
    assert memory.keys(_a(one), "Bash", {"command": one}, {})[0] != memory.keys(_a(two), "Bash", {"command": two}, {})[0]


@pytest.mark.parametrize("cmd", LAUNCHCTL_KIND)
def test_the_other_service_calls_stay_a_kind(cmd):
    """The rest of R-12 stays class-eligible (owner decision)."""
    a = _a(cmd)
    assert a.rule_id == "R-12"
    assert memory.eligibility(a, "Bash", {"command": cmd}, {}) == "class"


def test_launchctl_bootout_with_sudo_is_still_never():
    cmd = "sudo launchctl bootout system/com.x"
    assert memory.eligibility(_a(cmd), "Bash", {"command": cmd}, {}) == "never"


@pytest.mark.parametrize("cmd", ["security delete-internet-password -s x", "security delete-keychain x.keychain"])
def test_keychain_deletes_are_never_remembered(cmd):
    """HO-delete (owner decision W-137): a human-only class is never remembered, in no form."""
    a = _a(cmd)
    assert "HO-delete" in a.ho_ids
    assert memory.eligibility(a, "Bash", {"command": cmd}, {}) == "never"
