"""0.6.0 shipped `jade level` without a dispatch-table entry: the command parsed, then crashed with KeyError: 'level'.

These tests run the command through `cli.main`, the way a terminal does, so a command that is parsed but not dispatched fails here.
"""

import pytest

from jade import cli, paths, util


@pytest.fixture(autouse=True)
def human(monkeypatch):
    """The ancestry check of a human terminal sees the agent that runs this suite; the refusal path is tested elsewhere."""
    monkeypatch.setattr(util, "human_tty_refusal", lambda *a, **k: None)


def test_every_parsed_command_is_named_in_main():
    """A command that build_parser accepts but main() never names ends in KeyError: the 0.6.0 defect, for any command."""
    import inspect

    names = sorted(cli.build_parser()._subparsers._group_actions[0].choices)
    assert len(names) >= 26
    src = inspect.getsource(cli.main)
    assert [n for n in names if f'"{n}"' not in src] == []


def test_level_without_a_value_prints_unset(capsys):
    assert cli.main(["level"]) in (0, None)
    assert capsys.readouterr().out.strip() == "unset"


@pytest.mark.parametrize("value", ["off", "low", "medium", "high"])
def test_level_sets_and_reads_back(value, capsys):
    assert cli.main(["level", value]) in (0, None)
    assert capsys.readouterr().out.strip() == f"level {value}"
    assert paths.level() == value
    assert cli.main(["level"]) in (0, None)
    assert capsys.readouterr().out.strip() == value


def test_level_unset_clears_it(capsys):
    cli.main(["level", "low"])
    capsys.readouterr()
    assert cli.main(["level", "unset"]) in (0, None)
    assert paths.level() is None
