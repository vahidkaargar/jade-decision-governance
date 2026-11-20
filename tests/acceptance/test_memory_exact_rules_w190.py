"""W-190: three guarded high calls that memory used to learn as a kind.

- `git push --all` (and its spellings: `--branches`, an abbreviation, a glob refspec, `git send-pack --all`) can push the default branch. In a repository the gate adds
  the head commit to the answer, so the call is exact already; with no repository (no fingerprint) it was class-eligible. Now exact only in both cases.
- A shell write to a JADE source file (R-16: `sed -i`, `cp`, `tee`) was a kind: one answer for `sed -i s/a/b/ jade/cli.py` also covered `sed -i s/x/y/ jade/cli.py`. Exact only
  (the file tools were already exact: not a shell command).
- `composio execute SLUG` runs an action in a connected app (it sends mail, posts a message). The same call in another project, or with other arguments, is not the same
  call: never remembered, in no form (same group as egress and money). The owner may change this default (W-190).
"""

import pytest

from jade import memory, policy, scopes

PUSH_MANY = [
    "git push --all origin",
    "git push --all",
    "git push origin --all",
    "git push -u --all origin",
    "git push origin --all --dry-run",
    "git push origin feat --all",
    "git push --al origin",
    "git push --branches origin",
    "git push origin '--all'",
    "git push origin --all=x",
    "git -C /w push --all origin",
    "env X=1 git push --all",
    "cd /tmp && git push --all origin",
    "git send-pack --all origin",
    "git push origin 'refs/heads/*:refs/heads/*'",
]

R16 = [
    "cd /work/jade && sed -i '' s/is_human_tty/x/ jade/cli.py",
    "sed -i '' s/a/b/ /work/jade/jade/cli.py",
    "cp x /work/jade/jade/cli.py",
]

COMPOSIO = [
    "composio execute GMAIL_SEND_EMAIL",
    "composio execute GMAIL_SEND_EMAIL -d '{}'",
    "composio  execute SLACK_POST_MESSAGE",
    "composio execute GITHUB_X",
    "cd /tmp && composio execute GMAIL_SEND_EMAIL",
]


def _a(cmd, cwd="/tmp/case"):
    return policy.assess("Bash", {"command": cmd}, cwd)


def _el(cmd, fp=None):
    return memory.eligibility(_a(cmd), "Bash", {"command": cmd}, fp or {})


@pytest.mark.parametrize("cmd", PUSH_MANY)
def test_a_push_of_many_branches_is_exact_only(cmd):
    a = _a(cmd)
    assert a.rule_id == "R-10" and a.level == "high"
    assert _el(cmd) == "exact"


@pytest.mark.parametrize("cmd", PUSH_MANY)
def test_such_a_push_has_no_kind_key_and_no_kind_scope(cmd):
    a = _a(cmd)
    tin = {"command": cmd}
    exact, cls, el = memory.keys(a, "Bash", tin, {})
    assert exact and cls is None and el == "exact"
    assert not [s for s in scopes.offered(a, "Bash", tin, {}, "/tmp/case") if s.kind == "kind_project"]


def test_a_chain_with_one_such_push_is_exact_only():
    cmd = "docker compose up -d && git push --all origin"
    a = _a(cmd)
    assert "R-10" in a.seg_rules
    assert memory.eligibility(a, "Bash", {"command": cmd}, {}) == "exact"


@pytest.mark.parametrize("cmd", R16)
def test_a_shell_write_to_jade_source_is_exact_only(cmd):
    a = _a(cmd)
    assert a.rule_id == "R-16" and a.level == "high"
    assert _el(cmd) == "exact"
    assert memory.keys(a, "Bash", {"command": cmd}, {})[1] is None


def test_two_edits_of_jade_source_do_not_share_an_answer():
    one, two = "sed -i '' s/a/b/ /work/jade/jade/cli.py", "sed -i '' s/x/y/ /work/jade/jade/cli.py"
    assert memory.keys(_a(one), "Bash", {"command": one}, {})[0] != memory.keys(_a(two), "Bash", {"command": two}, {})[0]


@pytest.mark.parametrize("cmd", COMPOSIO)
def test_composio_execute_is_never_remembered(cmd):
    a = _a(cmd)
    assert a.rule_id == "R-10" and a.level == "high"
    assert _el(cmd) == "never"
    exact, cls, el = memory.keys(a, "Bash", {"command": cmd}, {})
    assert cls is None and el == "never"
    assert not scopes.offered(a, "Bash", {"command": cmd}, {}, "/tmp/case")


def test_a_chain_with_composio_execute_is_never_remembered():
    cmd = "docker compose up -d && composio execute GMAIL_SEND_EMAIL"
    assert memory.eligibility(_a(cmd), "Bash", {"command": cmd}, {}) == "never"


# what these calls were on origin/main (and are still): the fix is narrow
UNCHANGED = {
    "git push --force-with-lease origin feat": "never",   # an ordinary force push is already never
    "git push origin '+refs/heads/*:refs/heads/*'": "never",   # a forced glob push is already never
    "git push --atomic origin feat": "class",
    "git send-pack origin HEAD:refs/heads/main": "class",   # the push rule's kind with no repository (A244): not changed here
    "docker compose up -d": "class",
    "git reset --hard HEAD~1": "class",
    "composio search x": "exact",   # not an action: not guarded by R-10, not touched
    "cp x /work/other/file.py": "exact",
}


@pytest.mark.parametrize("cmd,before", sorted(UNCHANGED.items()))
def test_control_the_fix_is_narrow(cmd, before):
    assert _el(cmd) == before
