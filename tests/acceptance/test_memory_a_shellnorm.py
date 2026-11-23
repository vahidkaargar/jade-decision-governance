"""A-shellnorm: calls the new shell reading rates high (it read a script, a variable or a $'..' word that origin/main rated low or medium) are remembered as an exact answer only.

On origin/main these were low or medium and never asked, so memory never saw them. Now they ask, and an answer must not cover a whole kind:
- W-137 (owner): `launchctl bootout`, `unload` and `remove` are exact only, plain or read out of a written script;
- a key that still has a variable word (`git push origin $b`) stands for every value of that variable, so it is exact only (the values are part of the exact key);
- a call that has a `$'..'` word: the key shows the decoded text, the exact key keeps the spelling.
"""

import pytest

from jade import memory, policy

CWD = "/tmp/case"


def elig(cmd):
    tin = {"command": cmd}
    a = policy.assess("Bash", tin, CWD)
    return a, memory.eligibility(a, "Bash", tin, {})


LAUNCHD_FORMS = [
    'echo "launchctl bootout gui/501/x" > f; command sh f',
    'echo "launchctl bootout gui/501/x" > f; sh f',
    'echo "launchctl unload x" > f; command bash f',
    'echo "launchctl remove x" > f; env sh f',
    'echo "launchctl remove x" > f; doas -u x sh f',
    'echo "launchctl bootout x" > f; source f',
    'echo "launchctl bootout x" > f; . f',
    'echo "launchctl bootout x" > f; builtin source f',
    "launchctl bootout gui/501/x",
    "command launchctl unload x",
    "launchctl remove x",
    "doas -u x launchctl bootout x",
    "v=bootout; launchctl $v x",
    "v=unload; doas -u x launchctl $v x",
]


@pytest.mark.parametrize("cmd", LAUNCHD_FORMS)
def test_launchctl_bootout_unload_remove_are_exact_only_however_they_are_spelled(cmd):
    a, got = elig(cmd)
    assert a.level == "high" and a.rule_id == "R-12", (cmd, a.level, a.rule_id)
    assert got == "exact", cmd


@pytest.mark.parametrize("cmd", ["launchctl kickstart x", "launchctl load x", 'echo "launchctl load x" > f; command sh f'])
def test_control_the_other_launchctl_verbs_are_still_a_kind(cmd):
    a, got = elig(cmd)
    assert a.level == "high" and a.rule_id == "R-12" and got == "class", (cmd, a.level, a.rule_id, got)


VARIABLE_FORMS = [
    'b=feature; g=git; eval "$g push origin $b"',
    'b=feature\ng=git\neval "$g push origin $b"',
    'b=feature && g=git && eval "$g push origin $b"',
    'export b=feature; g=git; eval "$g push origin $b"',
    'h=sh; cat "git push origin HEAD:$b" | $h',
]


@pytest.mark.parametrize("cmd", VARIABLE_FORMS)
def test_a_key_with_a_variable_word_is_exact_only(cmd):
    a, got = elig(cmd)
    assert a.level == "high" and a.rule_id == "R-10" and not a.blocked, (cmd, a.level, a.rule_id)
    assert got == "exact", cmd


@pytest.mark.parametrize("cmd", [
    'g=git; eval "$g reset --hard"',
    "x=--hard; git reset $x",
    'export b=x; g=git; eval "$g reset --hard"',
    "v=kickstart; launchctl $v x",
])
def test_a_call_that_reads_a_variable_it_sets_is_exact_only_even_when_the_key_shows_the_value(cmd):
    a, got = elig(cmd)
    assert a.level == "high" and got == "exact", (cmd, a.level, a.rule_id, got)


def test_two_values_of_the_variable_are_two_exact_keys():
    a1, _ = elig('b=feature; g=git; eval "$g push origin $b"')
    a2, _ = elig('b=release; g=git; eval "$g push origin $b"')
    assert memory._target(a1) != memory._target(a2)


def test_a_dollar_quote_word_is_exact_only_a_plain_reset_is_still_a_kind():
    a, got = elig("git reset $'--hard\\c x'")
    assert a.level == "high" and a.rule_id == "R-11" and got == "exact"
    b, plain = elig("git reset --hard")
    assert b.level == "high" and b.rule_id == "R-11" and plain == "class"
