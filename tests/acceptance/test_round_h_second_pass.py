"""Round H, second pass: the variants an independent verifier found. SI-09 sees an alias whose quoted value is the whole approving command and
`env -S '...'`; SI-10 sees a short glob of the key file when the command names JADE's state directory; R-17 names the environment route around
core.hooksPath. (The pre-push guard's variants, init.defaultBranch and jade.protectedBranch, are in test_git_hook_guard.py.)
"""

import pytest

from jade import policy


def blocked(cmd):
    return policy.assess("Bash", {"command": cmd}, "/tmp/x").blocked


# ---- SI-09: quoted alias values and env -S -------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "alias j='jade permit approve'\nj p",
    'alias j="jade permit approve"\nj p',
    "alias j='uv run jade'\nj permit approve p",
    "alias j='/usr/local/bin/jade'",
    "alias j=\"python -m jade\"; j permit approve p",
    "alias j='cd x && jade permit approve p'",
    'env -S "jade permit approve p"',
    "env -S 'jade permit approve p'",
    "env -iS 'jade hooks install'",
    "env --split-string='jade gate off'",
])
def test_si09_sees_the_approving_command_inside_an_alias_value_and_env_split_string(cmd):
    assert "SI-09" in blocked(cmd)


@pytest.mark.parametrize("cmd", [
    "alias ll='ls -la'",
    "alias gs=\"git status\"",
    "env -S 'ls -la'",
    "env -S 'FOO=1 printenv FOO'",
    "echo \"jade permit approve p\"",
    "gh pr review --approve",
    "ACTION=approve ./deploy.sh",
    "jade permit approve --help",
])
def test_si09_still_lets_the_harmless_neighbours_through(cmd):
    assert "SI-09" not in blocked(cmd)


# ---- SI-10: a short glob counts when the command names the state directory ----------------------------------------
@pytest.mark.parametrize("cmd", [
    "cat ~/.local/share/jade/*.key",
    "cat ~/.local/share/jade/perm*",
    "cat ~/.local/share/jade/p*",
    "cat ~/.local/share/jade/*",
    "cat $HOME/.local/share/jade/??????-seal*",
    "cd ~/.local/share/jade && cat *.key",
    "cd $JADE_HOME && cat p*",
    "cat ${JADE_HOME}/*.key",
    "JADE_HOME=/x cat /x/[p]*",
])
def test_si10_sees_a_short_glob_of_the_key_when_the_state_directory_is_named(cmd):
    assert "SI-10" in blocked(cmd)


@pytest.mark.parametrize("cmd", ["rm *.key", "ls *", "cat perm*", "ls jade/*", "wc -l jade/*.py", "cat ~/.ssh/*.pub", "ls /tmp/*", "git add jade/*"])
def test_si10_leaves_ordinary_globs_alone_without_the_state_directory(cmd):
    assert "SI-10" not in blocked(cmd)


def test_si10_state_directory_from_the_environment_counts(monkeypatch):
    monkeypatch.setenv("JADE_HOME", "/srv/custom-state")
    assert "SI-10" in blocked("cat /srv/custom-state/*.key")
    assert "SI-10" not in blocked("cat /srv/other-state/*.key")


# ---- R-17: the environment route around core.hooksPath -------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/x git push origin v1-feat",
    "GIT_CONFIG_PARAMETERS=\"'core.hookspath=/x'\" git push origin v1-feat",
    "export GIT_CONFIG_KEY_0=core.hooksPath",
])
def test_the_environment_route_around_the_guard_is_rated_r17(cmd):
    a = policy.assess("Bash", {"command": cmd}, "/tmp/x")
    assert a.rule_id == "R-17" and a.level == "high"


def test_other_git_config_environment_variables_are_not_r17():
    a = policy.assess("Bash", {"command": "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name git status"}, "/tmp/x")
    assert a.rule_id != "R-17" and a.level == "low"
