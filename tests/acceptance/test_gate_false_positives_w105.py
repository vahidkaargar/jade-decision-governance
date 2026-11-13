"""W-105 (docs/12 plan v3, PR-2): reads that the gate rated as writes to JADE's own code on 2026-11-13.

The commands below are the recorded subjects of decisions the owner had to approve or let expire (dec_1593583203f9, dec_a5dccccf05d6),
copied as written. Each only prints part of a file in a JADE checkout, or asks the shell where `jade` is. `sed` without `-i` (or
`--in-place`) writes nothing; `command -v` only looks a name up. The writes next to them must keep their rating.
"""

from pathlib import Path

import pytest

from jade import policy
from jade.util import LEVELS

ROOT = Path(__file__).resolve().parents[2]

RECORDED_READS = [
    "sed -n 199,215p jade/adapter.py; sed -n 275,296p jade/adapter.py; command -v jade && jade ledger stats",
]

READS = RECORDED_READS + [
    "sed -n 1,20p jade/hooks.py", "sed -n '/def pretool/,/^def /p' jade/hooks.py", "sed -ne 5p jade/policy.py", "sed --quiet 5p jade/policy.py",
    "sed 's/a/b/' jade/hooks.py", "sed 's/a/b/' jade/hooks.py > /tmp/out.py",
    "command -v jade", "command -V jade", "command -v jade && jade check", "type jade", "which jade",
]

WRITES = [
    "sed -i '' s/a/b/ jade/hooks.py", "sed -i.bak s/a/b/ jade/hooks.py", "sed --in-place s/a/b/ jade/hooks.py", "sed -n -i '' 1p jade/hooks.py",
    "sed -ni 1p jade/hooks.py", "sed -e 1p -i jade/hooks.py", "sed 's/a/b/' /tmp/x > jade/hooks.py", "sed -n 1p x | tee jade/hooks.py",
    "sed -n '1w jade/hooks.py' /tmp/x", "sed 's/a/b/w jade/hooks.py' /tmp/x",
    "command cp /tmp/x jade/hooks.py", "command -p cp /tmp/x jade/hooks.py",
]

# A sed script built by a command substitution stays gated (fail closed): the gate cannot know what the substitution prints, and a printed
# `1w jade/hooks.py` line would make sed write the file. Recorded as dec_a5dccccf05d6; the owner pays one click for it from 0.5.0 on.
COMPUTED_SCRIPTS = [
    "grep -n \"def ask_main\" jade/cli.py; sed -n \"$(grep -n 'def ask_main' jade/cli.py | cut -d: -f1),+75p\" jade/cli.py",
    "sed -n \"$(echo 1),+5p\" jade/cli.py",
]


def assess(cmd):
    return policy.assess("Bash", {"command": cmd}, str(ROOT))


@pytest.mark.parametrize("cmd", READS)
def test_reads_of_jade_code_are_not_writes(cmd):
    a = assess(cmd)
    assert LEVELS[a.level] < LEVELS["high"] and not a.blocked, (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", WRITES)
def test_writes_to_jade_code_stay_r16(cmd):
    a = assess(cmd)
    assert a.rule_id == "R-16" and LEVELS[a.level] >= LEVELS["high"], (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", COMPUTED_SCRIPTS)
def test_a_computed_sed_script_stays_gated(cmd):
    assert assess(cmd).rule_id == "R-16", cmd
