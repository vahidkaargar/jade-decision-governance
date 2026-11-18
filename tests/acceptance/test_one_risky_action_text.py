"""Clear questions, step 2: agents are told to send one risky action per call.

The sentence lives in the jade skill and in the managed CLAUDE.md section. `jade claude-md show` prints the section.
"""

from pathlib import Path

from jade import cli, ops

ROOT = Path(__file__).resolve().parents[2]
SENTENCE = "Send one risky action per call."
RULE = "Do not join a merge, a delete or a push with other commands using `;` or `&&`; the human then sees one clear question."


def test_skill_has_the_sentence():
    text = (ROOT / "skills" / "jade" / "SKILL.md").read_text()
    assert SENTENCE in text
    assert RULE in text


def test_claude_md_section_has_the_sentence():
    text = (ROOT / "docs" / "claude-md-jev-section.md").read_text()
    assert SENTENCE in text
    assert RULE in text
    assert text.startswith(ops.JEV_HEADING)


def test_claude_md_show_prints_the_sentence(capsys):
    assert cli.main(["claude-md", "show"]) == 0
    out = capsys.readouterr().out
    assert SENTENCE in out
    assert RULE in out
