"""W-37 (docs/05 T19): the residuals the first A2 passes left open, closed where a deterministic rule exists. Every true positive has a false-positive twin.

* A patch applied by `patch` / `git apply` / `git am` changes the files its headers name. The headers are read when the patch is a heredoc or here-string of the
  command, a file the same command writes, a file on disk (`< p`, `-i p`, `git apply p`, `cat p | patch`), and each target is classified as if it had been written
  by a redirect. A patch that cannot be read (a pipe from `git diff` or `curl`, a variable, a file another command of the line rewrites) is recorded as the R-31
  blind spot `patch-body`, never gated.
* A link made by one command (`ln -s ~/.claude c && echo x > c/settings.json`) is followed by the commands after it.
* `pyproject.toml` and `uv.lock` of a JADE checkout (R-24); `uv add|remove|lock|version` in one.
* `is_human_tty` also needs this process to be in a session led by an interactive shell on its own controlling terminal (`util.tty_session_refusal`).

Hosts and paths are synthetic. Nothing here runs a command: the cases go through `policy.assess`, `hooks.pretool` and synthetic process tables.
"""
import os

import pytest

from jade import hooks, policy, util
from jade.util import LEVELS, Proc

REAL_TTY_SESSION = util.tty_session_refusal   # tests/conftest.py replaces it for every test; this is the real one


@pytest.fixture(autouse=True)
def cold_policy():
    policy.reset_cache()
    yield
    policy.reset_cache()


@pytest.fixture
def work(tmp_path, monkeypatch):
    """A working directory with a HOME of its own (no gitconfig: no alias lookup reads the machine that runs the tests)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    w = tmp_path / "work"
    w.mkdir()
    return w


def sh(cmd, cwd):
    return policy.assess("Bash", {"command": cmd}, str(cwd))


def high(a):
    return LEVELS[a.level] >= LEVELS["high"] or bool(a.blocked)


def diff(*paths, new=False, strip="ab"):
    """A unified diff that touches `paths`, written `a/<path>` and `b/<path>` unless `strip` is empty."""
    out = []
    for p in paths:
        a, b = (f"a/{p}", f"b/{p}") if strip else (p, p)
        out += [f"diff --git {a} {b}", f"--- {'/dev/null' if new else a}", f"+++ {b}", "@@ -1 +1 @@", "-old", "+new"]
    return "\n".join(out) + "\n"


def put(dirpath, name, text):
    p = dirpath / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


PROTECTED = ".claude/settings.json"


# ---------------------------------------------------------------- a patch read from a file on disk
FROM_FILE = [
    "patch -p1 < p.patch",
    "patch -p1 -i p.patch",
    "patch -p1 --input=p.patch",
    "patch -p1 -N < p.patch && echo ok",
    "git apply p.patch",
    "git apply -p1 p.patch",
    "git apply --index p.patch",
    "git apply --3way p.patch",
    "git apply -R p.patch",
    "git -C . apply p.patch",
    "git apply --whitespace=nowarn p.patch",
    "cat p.patch | patch -p1",
    "cat p.patch | git apply -",
    "cat p.patch | git apply",
    "git am < p.patch",
    "git am p.patch",
    "sudo patch -p1 < p.patch",
    "env LC_ALL=C patch -p1 < p.patch",
    "(git apply p.patch)",
    "{ git apply p.patch; }",
    "cd . && git apply p.patch 2>&1 | tail -3",
    "sh -c 'git apply p.patch'",
    "bash -c \"patch -p1 < p.patch\"",
]


@pytest.mark.parametrize("cmd", FROM_FILE)
@pytest.mark.parametrize("target", [PROTECTED, "policies/risk.yaml", "AGENTS.md", "jade/hooks.py"])
def test_a_patch_file_that_names_a_protected_file_is_gated(work, cmd, target):
    put(work, "p.patch", diff(target))
    a = sh(cmd, work)
    assert high(a), (cmd, target, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", FROM_FILE)
def test_the_same_patch_on_an_ordinary_file_stays_low(work, cmd):
    put(work, "p.patch", diff("src/app.py", "docs/policies/readme.md", "tests/test_x.py"))
    a = sh(cmd, work)
    assert not high(a), (cmd, a.rule_id, a.level)
    assert not a.blind, (cmd, a.blind)


NO_WRITE = [
    "git apply --check p.patch",
    "git apply --stat p.patch",
    "git apply --numstat p.patch",
    "git apply --summary p.patch",
    "git apply --cached p.patch",
    "patch --dry-run -p1 < p.patch",
    "git apply -h",
    "git am --abort",
    "cat p.patch",
    "less p.patch",
    "git diff > out.patch",
    "git apply --check p.patch && echo fine",
]


@pytest.mark.parametrize("cmd", NO_WRITE)
def test_a_patch_that_is_only_checked_or_read_writes_nothing(work, cmd):
    put(work, "p.patch", diff(PROTECTED, "policies/risk.yaml", "jade/hooks.py"))
    a = sh(cmd, work)
    assert not high(a), (cmd, a.rule_id, a.level)


def test_check_then_apply_in_one_line_is_gated_by_the_apply(work):
    put(work, "p.patch", diff(PROTECTED))
    assert high(sh("git apply --check p.patch && git apply p.patch", work))


# ---------------------------------------------------------------- a patch on the command line
HEREDOC = [
    "git apply <<'EOF'\n{body}EOF",
    "git apply - <<'EOF'\n{body}EOF",
    "patch -p1 <<'EOF'\n{body}EOF",
    "patch -p1 <<EOF\n{body}EOF",
    "git am <<'EOF'\n{body}EOF",
    "cat <<'EOF' | git apply\n{body}EOF",
    "cat <<'EOF' | patch -p1\n{body}EOF",
    "cd . && patch -p1 <<'EOF'\n{body}EOF",
    "bash <<'EOF'\ngit apply p.patch\nEOF",
]


@pytest.mark.parametrize("tpl", HEREDOC[:-1])
@pytest.mark.parametrize("target", [PROTECTED, "policies/safety.yaml", "AGENTS.md", "jade/policy.py"])
def test_a_heredoc_patch_that_names_a_protected_file_is_gated(work, tpl, target):
    a = sh(tpl.format(body=diff(target)), work)
    assert high(a), (tpl, target, a.rule_id, a.level)


@pytest.mark.parametrize("tpl", HEREDOC[:-1])
def test_a_heredoc_patch_on_ordinary_files_stays_low(work, tpl):
    a = sh(tpl.format(body=diff("src/app.py", "README.md")), work)
    assert not high(a) and not a.blind, (tpl, a.rule_id, a.level, a.blind)


def test_a_patch_in_a_heredoc_that_a_shell_reads_is_found(work):
    put(work, "p.patch", diff(PROTECTED))
    assert high(sh(HEREDOC[-1], work))
    assert not high(sh("bash <<'EOF'\necho hi\nEOF", work))


GROUPED = [
    "(git apply <<'EOF'\n{body}EOF\n)",
    "( cd . && patch -p1 <<'EOF'\n{body}EOF\n)",
    "((git apply <<'EOF'\n{body}EOF\n))",
    "(cd .claude 2>/dev/null; git apply --directory=.claude <<'EOF'\n{body}EOF\n)",
    "{ git apply <<'EOF'\n{body}EOF\n}",
    "if true; then git apply <<'EOF'\n{body}EOF\nfi",
    "echo start; (patch -p1 <<'EOF'\n{body}EOF\n) && echo done",
    "(cat <<'EOF' | git apply\n{body}EOF\n)",
    "x=$(git apply <<'EOF'\n{body}EOF\n)",
]


@pytest.mark.parametrize("tpl", GROUPED)
def test_a_heredoc_patch_inside_a_group_or_subshell_is_read(work, tpl):
    assert high(sh(tpl.replace("{body}", diff(PROTECTED)), work)), tpl
    ok = sh(tpl.replace("{body}", diff("src/app.py")), work)
    assert not high(ok) and not ok.blind, (tpl, ok.rule_id, ok.blind)


def test_parentheses_and_quotes_inside_a_patch_body_do_not_confuse_the_reader(work):
    body = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-print('a)')\n+print(\"b(\")  # ) (\n"
    for tpl in ("(git apply <<'EOF'\n{b}EOF\n)", "git apply <<'EOF'\n{b}EOF"):
        a = sh(tpl.format(b=body), work)
        assert not high(a) and not a.blind, (tpl, a.rule_id, a.blind)
    body2 = body + "--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1 +1 @@\n-x\n+y\n"
    assert high(sh("(git apply <<'EOF'\n" + body2 + "EOF\n)", work))


def test_a_here_string_patch_is_found(work):
    assert high(sh("git apply <<< '--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1 +1 @@\n-a\n+b'", work))
    assert not high(sh("git apply <<< '--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-a\n+b'", work))


def test_a_patch_text_that_is_only_written_to_a_file_is_data(work):
    body = diff(PROTECTED)
    assert not high(sh(f"cat > notes.txt <<'EOF'\n{body}EOF", work))
    assert not high(sh(f"cat <<'EOF'\n{body}EOF", work))
    assert not high(sh(f"git commit -m x && cat > n.txt <<'EOF'\n{body}EOF", work))


# ---------------------------------------------------------------- a patch the same command writes and applies
@pytest.mark.parametrize("cmd", [
    "cat > p <<'EOF'\n{body}EOF\ngit apply p",
    "cat > p <<'EOF'\n{body}EOF\npatch -p1 < p",
    "cat > p <<'EOF'\n{body}EOF\npatch -p1 -i p",
    "cat > ./p <<'EOF'\n{body}EOF\ngit apply p",
    "cat > p <<'EOF'\n{body}EOF\ncat p | patch -p1",
    "tee p <<'EOF' >/dev/null\n{body}EOF\ngit apply p",
    "cat > p <<'EOF'\n{body}EOF\ngit apply --check p && git apply p",
])
def test_a_patch_the_command_writes_and_applies_is_read(work, cmd):
    assert high(sh(cmd.format(body=diff(PROTECTED)), work)), cmd
    ok = sh(cmd.format(body=diff("src/app.py")), work)
    assert not high(ok) and not ok.blind, (cmd, ok.rule_id, ok.blind)


def test_an_echoed_patch_is_read(work):
    assert high(sh("printf '%s\\n' '--- a/AGENTS.md' '+++ b/AGENTS.md' > p && git apply p", work))
    assert not high(sh("printf '%s\\n' '--- a/x.py' '+++ b/x.py' > p && git apply p", work))


def test_a_patch_written_and_never_applied_is_data(work):
    assert not high(sh(f"cat > p <<'EOF'\n{diff(PROTECTED)}EOF\ngit status", work))


# ---------------------------------------------------------------- strip level, directory, file operand, header spellings
def test_the_directory_options_move_where_the_patch_lands(work, tmp_path):
    put(work, "p.patch", diff("settings.json"))
    for cmd in ("patch -p1 -d .claude < p.patch", "patch -p1 --directory=.claude < p.patch", "git apply --directory=.claude p.patch",
                "git -C .claude apply ../p.patch", "cd .claude && git apply ../p.patch", f"patch -p1 -d {tmp_path}/home/.claude < p.patch"):
        assert high(sh(cmd, work)), cmd
    for cmd in ("patch -p1 -d src < p.patch", "git apply --directory=src p.patch", "git apply p.patch", "patch -p1 < p.patch"):
        assert not high(sh(cmd, work)), cmd


def test_the_strip_level_decides_what_the_header_means(work):
    put(work, "p0.patch", diff("jade/hooks.py", strip=""))
    put(work, "p1.patch", diff("jade/hooks.py"))
    assert high(sh("patch -p0 < p0.patch", work))
    assert high(sh("patch -p1 < p1.patch", work))
    assert high(sh("git apply -p0 p0.patch", work))
    assert high(sh("git apply p1.patch", work))
    put(work, "nested.patch", diff("a/b/.claude/settings.json", strip=""))
    assert high(sh("patch -p2 < nested.patch", work))   # `a/b/` stripped: `.claude/settings.json`
    put(work, "far.patch", diff("a/b/.claude/hooks/x.sh", strip=""))
    assert high(sh("patch < far.patch", work))   # no -p: every strip level is tried


def test_an_absolute_header_is_read(work, tmp_path):
    put(work, "p.patch", f"--- {tmp_path}/home/.claude/settings.json\n+++ {tmp_path}/home/.claude/settings.json\n@@ -1 +1 @@\n-a\n+b\n")
    assert high(sh("patch -p0 < p.patch", work))
    assert high(sh("patch < p.patch", work))


def test_an_explicit_target_file_is_a_write(work):
    put(work, "p.patch", diff("src/app.py"))
    assert high(sh("patch .claude/settings.json < p.patch", work))
    assert high(sh("patch -p1 policies/risk.yaml p.patch", work))
    assert not high(sh("patch src/app.py < p.patch", work))
    assert high(sh("patch -o AGENTS.md src/app.py < p.patch", work))   # -o writes the result to another file
    assert not high(sh("patch -o /tmp/out.txt src/app.py < p.patch", work))


@pytest.mark.parametrize("body", [
    "diff --git a/src/a.py b/AGENTS.md\nsimilarity index 90%\nrename from src/a.py\nrename to AGENTS.md\n",
    "diff --git a/src/a.py b/policies/risk.yaml\ncopy from src/a.py\ncopy to policies/risk.yaml\n",
    "diff --git a/AGENTS.md b/AGENTS.md\ndeleted file mode 100644\n--- a/AGENTS.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n",
    "diff --git a/AGENTS.md b/AGENTS.md\nnew file mode 100644\n--- /dev/null\n+++ b/AGENTS.md\n@@ -0,0 +1 @@\n+x\n",
    "diff --git a/AGENTS.md b/AGENTS.md\nold mode 100644\nnew mode 100755\n",
    "Index: AGENTS.md\n===================================================================\n--- AGENTS.md\t(revision 1)\n+++ AGENTS.md\t(working copy)\n@@ -1 +1 @@\n-a\n+b\n",
    "*** AGENTS.md\t2020-01-01\n--- AGENTS.md\t2020-01-02\n***************\n*** 1 ****\n! a\n--- 1 ----\n! b\n",
    'diff --git "a/AGENTS.md" "b/AGENTS.md"\n--- "a/AGENTS.md"\n+++ "b/AGENTS.md"\n@@ -1 +1 @@\n-a\n+b\n',
    "From 1111 Mon Sep 17 00:00:00 2001\nFrom: A <a@example.org>\nSubject: [PATCH] x\n\n---\n AGENTS.md | 1 +\n\ndiff --git a/AGENTS.md b/AGENTS.md\n--- a/AGENTS.md\n+++ b/AGENTS.md\n@@ -1 +1 @@\n-a\n+b\n",
])
def test_every_header_spelling_names_a_target(work, body):
    put(work, "p.patch", body)
    for cmd in ("git apply p.patch", "patch -p1 < p.patch", "git am < p.patch"):
        assert high(sh(cmd, work)), (cmd, body[:40])


def test_a_header_with_a_timestamp_and_a_hunk_text_that_looks_like_a_header(work):
    put(work, "p.patch", "--- a/src/x.py\t2020-01-01 00:00:00\n+++ b/src/x.py\t2020-01-02 00:00:00\n@@ -1,2 +1,2 @@\n-old\n+new\n context\n")
    assert not high(sh("git apply p.patch", work))
    # a hunk line that looks like a header (a patch of a patch) is read as one: fail closed, never open
    put(work, "q.patch", "--- a/src/x.py\n+++ b/src/x.py\n@@ -1,2 +1,2 @@\n--- a/AGENTS.md\n+++ b/AGENTS.md\n")
    assert high(sh("git apply q.patch", work))


# ---------------------------------------------------------------- a patch that cannot be read is recorded
BLIND = [
    "git diff | git apply -",
    "git diff HEAD~1 | git apply -R",
    "git show HEAD | patch -p1",
    "git format-patch --stdout -1 | git am",
    "curl -s https://example.org/x.patch | patch -p1",
    "curl -s https://example.org/x.patch | git apply",
    "wget -qO- https://example.org/x.patch | git apply -",
    "patch -p1 < $PATCH",
    'git apply "$PATCH"',
    "git apply $(ls *.patch)",
    "git apply *.patch",
    "git apply nothere.patch",
    "patch -p1 < nothere.patch",
    "git diff > q.patch && git apply q.patch",
    "curl -so q.patch https://example.org/x && patch -p1 < q.patch",
    "cp /tmp/other.patch q.patch && git apply q.patch",
    "python3 gen.py > q.patch; git apply q.patch",
]


@pytest.mark.parametrize("cmd", BLIND)
def test_a_patch_that_cannot_be_read_is_recorded_not_gated(work, cmd):
    put(work, "q.patch", diff("src/stale.py"))   # what is on disk now is not what the command will apply
    a = sh(cmd, work)
    assert "patch-body" in a.blind, (cmd, a.rule_id, a.blind)
    assert a.recorded
    assert not a.blocked


def test_a_patch_blind_spot_is_journaled_by_label_only(work, monkeypatch):
    seen = []
    monkeypatch.setattr(hooks, "_journal_blind", lambda a, event: seen.append(list(a.blind)))
    hooks.pretool({"tool_name": "Bash", "tool_input": {"command": "git diff | git apply -"}, "session_id": "s-w37", "cwd": str(work)})
    assert seen == [["patch-body"]]


@pytest.mark.parametrize("cmd", [
    "git diff | grep x",
    "git apply --check < /dev/null",
    "patch --help",
    "cat p.patch | wc -l",
    "echo 'git apply'",
    "git commit -m 'patch applied' && git log -1",
    "gpatch --version",
])
def test_commands_that_apply_no_patch_are_not_recorded(work, cmd):
    put(work, "p.patch", diff("src/a.py"))
    a = sh(cmd, work)
    assert "patch-body" not in a.blind and not high(a), (cmd, a.blind)


def test_a_patch_file_that_changes_between_the_gate_and_the_run_is_not_trusted_when_the_command_rewrites_it(work):
    put(work, "p.patch", diff("src/a.py"))
    a = sh("git diff > p.patch && git apply p.patch", work)
    assert "patch-body" in a.blind


def test_a_huge_patch_is_recorded_and_not_read(work):
    put(work, "big.patch", diff("src/a.py") + ("+x\n" * 400_000))
    a = sh("git apply big.patch", work)
    assert "patch-body" in a.blind


# ---------------------------------------------------------------- a link made in the same command
def test_a_symlink_made_and_written_through_in_one_command_is_followed(work, tmp_path):
    claude = tmp_path / "home" / ".claude"
    link = tmp_path / "lnk"
    for cmd in (
        f"ln -s {claude} {link} && echo x > {link}/settings.json",
        f"ln -sf {claude} {link} && echo x >> {link}/settings.json",
        f"ln -sfn {claude} {link}; printf x > {link}/settings.json",
        f"ln -s ~/.claude {link} && cp evil.json {link}/settings.json",
        f"ln -s ~/.claude {link} && tee {link}/settings.json",
        f"ln -s $HOME/.claude {link} && echo x > {link}/settings.json",
        f"ln -s {claude} {link} && sed -i '' s/a/b/ {link}/settings.json",
        f"ln -s {claude}/hooks {link} && echo x > {link}/pre.sh",
        f"ln -s {claude}/settings.json {link} && echo x > {link}",
        f"cd {tmp_path} && ln -s {claude} c && echo x > c/settings.json",
        f"cd {tmp_path} && ln -s ~/.claude c && cp evil.json c/settings.json",
        f"cd {tmp_path} && ln -s ../home/.claude c && echo x > c/settings.json",
        f"ln -s {claude} {link} && cd {link} && echo x > settings.json",
        f"ln -s {claude} {link}\necho x > {link}/settings.json",
        f"(ln -s {claude} {link}; echo x > {link}/settings.json)",
    ):
        assert high(sh(cmd, work)), cmd


def test_a_chain_of_links_is_followed(work, tmp_path):
    a, b = tmp_path / "la", tmp_path / "lb"
    assert high(sh(f"ln -s ~/.claude {a} && ln -s {a}/hooks {b} && echo x > {b}/pre.sh", work))
    assert high(sh(f"cd {tmp_path} && ln -s ~/.claude la && ln -s la/hooks lb && echo x > lb/pre.sh", work))
    assert not high(sh(f"ln -s ~/projects/app {a} && ln -s {a}/src {b} && echo x > {b}/f.py", work))


def test_a_link_to_a_directory_and_into_one(work, tmp_path):
    links = tmp_path / "links"
    links.mkdir()
    cfg = tmp_path / "home" / ".config" / "jade"
    assert high(sh(f"cd {links} && ln -s {cfg} . && echo x > jade/local.yaml", work))
    assert high(sh(f"ln -s {cfg} {links}/ && echo x > {links}/jade/local.yaml", work))
    assert high(sh(f"ln -s {cfg} {links} && echo x > {links}/jade/local.yaml", work))
    assert high(sh(f"ln -s {cfg} {tmp_path}/lnk && ln -sf {tmp_path}/lnk {links}/second && echo x > {links}/second/local.yaml", work))


def test_a_hard_link_to_a_protected_file_is_followed(work, tmp_path):
    f = tmp_path / "home" / ".claude" / "settings.json"
    assert high(sh(f"ln {f} {tmp_path}/hl && echo x >> {tmp_path}/hl", work))
    assert not high(sh(f"ln {tmp_path}/a.txt {tmp_path}/hl && echo x >> {tmp_path}/hl", work))


def test_a_link_through_a_variable_is_recorded_not_guessed(work, tmp_path):
    assert not high(sh(f"ln -s $WHERE {tmp_path}/lnk && echo x > {tmp_path}/lnk/f", work))


def test_links_twins_stay_low(work, tmp_path):
    claude, link = tmp_path / "home" / ".claude", tmp_path / "lnk"
    other = tmp_path / "other"
    for cmd in (
        f"ln -s ~/projects/app {link} && echo y > {link}/file",
        f"ln -s {claude} {link} && ls {link}/",
        f"ln -s {claude} {link} && cat {link}/settings.json",
        f"ln -s {claude} {link} && grep -n hooks {link}/settings.json",
        f"echo x > {link}/settings.json",
        f"echo x > {link}/settings.json && ln -s {claude} {link}",
        f"ln -s {claude} {link} && echo x > {tmp_path}/lnk2/settings.json",
        f"ln -s {claude} {link} && echo x > {tmp_path}/lnk-notes/settings.json",
        f"ln -s {claude} {link} && echo x > {other}/settings.json",
        f"(cd {other} && ln -s {claude} c); echo x > c/f",
        f"cd {tmp_path} && ln -s ~/projects/x c && echo x > c/settings.json",
        "ln -s a b && echo x > c",
    ):
        a = sh(cmd, work)
        assert not high(a), (cmd, a.rule_id, a.level)


def test_a_write_before_the_link_exists_is_not_through_it(work, tmp_path):
    claude, link = tmp_path / "home" / ".claude", tmp_path / "lnk2"
    assert not high(sh(f"echo x > {link}/settings.json; ln -s {claude} {link}", work))


# ---------------------------------------------------------------- pyproject.toml and uv.lock of a JADE checkout (R-24)
@pytest.fixture
def checkout(tmp_path):
    c = tmp_path / "ck"
    put(c, "jade/__init__.py", "")
    put(c, "jade/hooks.py", "")
    put(c, "pyproject.toml", '[project]\nname = "jade-decision-governance"\n')
    put(c, "uv.lock", "")
    (c / "docs").mkdir()
    return c


@pytest.fixture
def other(tmp_path):
    o = tmp_path / "app"
    put(o, "pyproject.toml", '[project]\nname = "app"\n')
    put(o, "uv.lock", "")
    (o / "jade").mkdir()   # a directory called jade that is not the package
    return o


WRITES = [
    "echo x >> pyproject.toml",
    "echo x > pyproject.toml",
    "printf x >> uv.lock",
    "cp /tmp/p.toml pyproject.toml",
    "cat /tmp/p.toml > pyproject.toml",
    "tee pyproject.toml",
    "sed -i '' 's/a/b/' pyproject.toml",
    "sed -i.bak s/a/b/ uv.lock",
    "mv /tmp/p.toml pyproject.toml",
    "python3 - <<'EOF'\nopen('pyproject.toml','w').write('x')\nEOF",
    "touch pyproject.toml",
    "rm pyproject.toml",
    "perl -pi -e 's/a/b/' pyproject.toml",
    "git checkout HEAD~3 -- pyproject.toml",
    "git restore pyproject.toml",
    "git apply p.patch",
]


@pytest.mark.parametrize("cmd", WRITES)
def test_a_write_to_the_pyproject_of_a_jade_checkout_is_gated(checkout, cmd, tmp_path):
    put(checkout, "p.patch", diff("pyproject.toml"))
    a = sh(cmd, checkout)
    assert high(a), (cmd, a.rule_id, a.level)


@pytest.mark.parametrize("cmd", WRITES)
def test_the_same_write_in_another_project_stays_low(other, cmd):
    put(other, "p.patch", diff("pyproject.toml"))
    a = sh(cmd, other)
    assert not high(a), (cmd, a.rule_id, a.level)


def test_the_pyproject_of_a_checkout_by_absolute_and_relative_path(checkout, other, tmp_path):
    assert high(sh(f"echo x >> {checkout}/pyproject.toml", other))
    assert high(sh(f"cd {checkout} && echo x >> pyproject.toml", other))
    assert high(sh("echo x >> ../pyproject.toml", checkout / "docs"))
    assert high(sh("echo x >> ./pyproject.toml", checkout))
    assert high(sh(f"cp /tmp/a {checkout}/uv.lock", other))
    assert not high(sh(f"echo x >> {other}/pyproject.toml", checkout))
    assert not high(sh(f"cd {other} && echo x >> pyproject.toml", checkout))


def test_the_write_tools_are_gated_on_the_pyproject_of_a_checkout(checkout, other):
    for tool in ("Write", "Edit", "MultiEdit"):
        assert high(policy.assess(tool, {"file_path": str(checkout / "pyproject.toml"), "content": "x", "new_string": "x", "old_string": "a"}, str(checkout)))
        assert high(policy.assess(tool, {"file_path": str(checkout / "uv.lock"), "content": "x"}, str(checkout)))
        assert not high(policy.assess(tool, {"file_path": str(other / "pyproject.toml"), "content": "x"}, str(other)))
        assert not high(policy.assess(tool, {"file_path": str(checkout / "docs" / "pyproject.toml.md"), "content": "x"}, str(checkout)))
    assert high(policy.assess("Write", {"file_path": "pyproject.toml", "content": "x"}, str(checkout)))


@pytest.mark.parametrize("cmd", [
    "cat pyproject.toml", "git add pyproject.toml", "git diff pyproject.toml", "git commit -m 'bump pyproject.toml'", "grep -n name pyproject.toml",
    "uv run pytest -q", "uv sync", "uv run ruff check .", "uv lock --check", "uv add --dry-run requests", "uv version", "uv version --short", "uv tree", "echo pyproject.toml", "cp pyproject.toml /tmp/backup.toml",
    "cat > pyproject.toml.md <<'EOF'\nx\nEOF", "git show HEAD:pyproject.toml > /tmp/old.toml", "ls pyproject.toml uv.lock", "git apply --check p.patch",
    "uv run python -c \"import tomllib;print(tomllib.load(open('pyproject.toml','rb')))\"",
])
def test_reads_and_builds_of_a_checkout_are_not_gated(checkout, cmd):
    put(checkout, "p.patch", diff("pyproject.toml"))
    assert not high(sh(cmd, checkout)), cmd


@pytest.mark.parametrize("cmd", [
    "uv add requests", "uv add --dev pytest-foo", "uv remove pyyaml", "uv lock", "uv lock --upgrade", "uv version --bump patch", "uv version 9.9.9", "uv add -r reqs.txt",
    "cd {c} && uv add requests", "uv add --project {c} requests", "uv remove --directory {c} pyyaml",
])
def test_uv_commands_that_edit_the_project_files_are_gated_in_a_checkout(checkout, other, cmd):
    names_it = "{c}" in cmd
    cmd = cmd.format(c=checkout)
    run_in = other if names_it else checkout
    assert high(sh(cmd, run_in)), (cmd, run_in)
    if run_in == checkout:
        assert not high(sh(cmd, other)), cmd   # the same command in another project


def test_uv_add_and_lock_are_the_routine_in_other_projects(other):
    for cmd in ("uv add requests", "uv remove x", "uv lock", "uv lock --upgrade", "uv version --bump patch", "uv sync", "uv run pytest"):
        assert not high(sh(cmd, other)), cmd


def test_a_checkout_without_a_pyproject_or_a_missing_directory_does_not_raise(tmp_path):
    ghost = tmp_path / "nowhere"
    a = sh("echo x > pyproject.toml", ghost)
    assert not high(a)
    a = sh("echo x > pyproject.toml", "")
    assert not high(a)


# ---------------------------------------------------------------- the human terminal: the session leader (util.tty_session_refusal)
ME, SID = 4242, 4100


def session(leader_exe, leader_args="", leader_ppid=4000, me_ppid=None, extra=None):
    """A process table where this process (4242) belongs to the session led by pid 4100, which runs `leader_exe leader_args`."""
    t = {1: Proc(0, "/sbin/launchd"), 4000: Proc(1, "/Applications/Terminal.app/Contents/MacOS/Terminal"), SID: Proc(leader_ppid, leader_exe, leader_args),
         ME: Proc(SID if me_ppid is None else me_ppid, "/usr/bin/python3", "jade permit approve")}
    t.update(extra or {})
    return t


def refusal(table, ctty=True, sid=SID):
    return REAL_TTY_SESSION(table=table, pid=ME, sid=sid, ctty=ctty)


@pytest.mark.parametrize("exe,args", [
    ("-zsh", ""), ("-bash", ""), ("/bin/zsh", "-l"), ("/bin/zsh", "-l -i"), ("/bin/bash", "--login"), ("/bin/bash", "-i"), ("zsh", ""), ("/opt/homebrew/bin/fish", ""),
    ("/opt/homebrew/bin/fish", "--login"), ("-sh", ""), ("/bin/dash", "-i"), ("-ksh", ""), ("-tcsh", ""), ("/opt/homebrew/bin/nu", "--login"),
])
def test_a_session_led_by_an_interactive_shell_is_a_human_terminal(exe, args):
    assert refusal(session(exe, args)) is None


@pytest.mark.parametrize("exe,args", [
    ("/usr/bin/python3", "-c import pty; pty.spawn(['jade','permit','approve'])"),
    ("/usr/bin/python3", "-m jade.cli permit approve"),
    ("/Users/x/.local/bin/jade", "permit approve"),
    ("/usr/bin/script", "-q /dev/null jade permit approve"),
    ("/usr/bin/expect", "-c spawn jade"),
    ("/bin/zsh", "-c jade permit approve"),
    ("/bin/bash", "-lc jade permit approve"),
    ("/bin/sh", "-ic jade permit approve"),
    ("/bin/zsh", "-i -c x"),
    ("/bin/bash", "--command x"),
    ("/bin/zsh", "run.sh"),
    ("/bin/bash", "-l setup.sh"),
    ("/usr/bin/screen", "-dmS x"),
    ("/usr/bin/ssh", "host"),
    ("/usr/local/bin/node", "agent.js"),
    ("", ""),
])
def test_a_session_led_by_anything_else_is_not_a_human_terminal(exe, args):
    r = refusal(session(exe, args))
    assert r and "session" in r, (exe, args, r)


def test_the_reason_names_the_leader_so_the_human_can_see_why():
    r = refusal(session("/usr/bin/script", "-q /dev/null x"))
    assert "script" in r


def test_a_session_whose_leader_is_gone_is_refused():
    t = session("-zsh")
    del t[SID]
    assert refusal(t)


def test_stdin_that_is_not_the_controlling_terminal_of_the_session_is_refused():
    assert refusal(session("-zsh"), ctty=False)
    assert refusal(session("-zsh"), ctty=True) is None


def test_a_leader_whose_own_ancestry_is_an_agent_is_refused_even_when_this_process_is_detached():
    t = session("-zsh", leader_ppid=4001, me_ppid=1, extra={4001: Proc(1, "/Users/x/Library/Application Support/Claude/claude-code/2.1.0/claude")})
    r = refusal(t)
    assert r and "agent" in r


def test_the_real_session_id_is_read(monkeypatch):
    sid = os.getsid(0)
    if sid == os.getpid():
        pytest.skip("this test process leads its own session")
    t = {1: Proc(0, "/sbin/launchd"), sid: Proc(1, "-zsh"), os.getpid(): Proc(sid, "/usr/bin/python3")}
    assert REAL_TTY_SESSION(table=t, ctty=True) is None
    t[sid] = Proc(1, "/usr/bin/python3", "-c x")
    assert REAL_TTY_SESSION(table=t, ctty=True)


def test_an_unreadable_process_table_is_a_refusal(monkeypatch):
    def boom():
        raise OSError("no ps")

    monkeypatch.setattr(util, "read_process_table", boom)
    assert REAL_TTY_SESSION(pid=ME, sid=SID, ctty=True)


def test_human_tty_refusal_asks_the_session_check_after_the_ancestry_check(monkeypatch):
    monkeypatch.setattr(util, "agent_env_present", lambda: False)   # the run itself may sit under an agent
    monkeypatch.setattr(util, "agent_ancestor", lambda: None)
    monkeypatch.setattr(util, "tty_session_refusal", lambda *a, **k: "its session is led by script")
    assert util.human_tty_refusal(True) == "its session is led by script"
    assert not util.is_human_tty(True)
    monkeypatch.setattr(util, "tty_session_refusal", lambda *a, **k: None)
    assert util.human_tty_refusal(True) is None and util.is_human_tty(True)
    monkeypatch.setattr(util, "agent_ancestor", lambda: "it runs under an agent process (claude, pid 7)")
    assert "agent" in util.human_tty_refusal(True)
    # not a terminal at all: the earlier answer, and the session is not asked
    monkeypatch.setattr(util, "tty_session_refusal", lambda *a, **k: pytest.fail("asked without a terminal"))
    assert "not an interactive terminal" in util.human_tty_refusal(False)


# ---------------------------------------------------------------- through the hook: exit codes, and nothing the command carries leaks into the record
def gate(cmd, cwd, tool="Bash", session="s-w37"):
    return hooks.pretool({"tool_name": tool, "tool_input": {"command": cmd}, "session_id": session, "cwd": str(cwd)})


def test_the_hook_asks_for_a_permit_for_a_patch_a_link_and_a_pyproject(work, checkout):
    put(work, "p.patch", diff(PROTECTED))
    for cmd in ("git apply p.patch", f"git apply <<'EOF'\n{diff('policies/risk.yaml')}EOF", "ln -s ~/.claude c && echo x > c/settings.json"):
        r = gate(cmd, work)
        assert r.exit_code == 2 and "permit" in r.stderr.lower(), (cmd, r.exit_code, r.stderr[:200])
    r = gate("echo x >> pyproject.toml", checkout)
    assert r.exit_code == 2 and "R-24" in r.stderr, (r.exit_code, r.stderr[:200])
    assert gate("echo x >> pyproject.toml", work).exit_code == 0


def test_the_hook_lets_the_twins_through(work):
    put(work, "p.patch", diff("src/app.py"))
    for cmd in ("git apply p.patch", "git apply --check p.patch", "ln -s ~/projects/app c && echo x > c/f", "git diff | git apply -"):
        assert gate(cmd, work).exit_code == 0, cmd


def test_a_patch_the_gate_could_not_read_is_never_reported_with_the_command_or_a_path(work, tmp_path):
    from jade import ledger

    secret_name = "never-journal-this-name.patch"
    gate(f"curl -s https://example.org/{secret_name} | git apply -", work, session="s-w37-journal")
    db = ledger.open()
    try:
        rows = db.all("SELECT guard, detail_json FROM events WHERE kind='blind_spot'")
    finally:
        db.close()
    assert rows and all(secret_name not in (r.guard or "") + (r.detail_json or "") for r in rows)
    assert any("patch-body" in (r.guard or "") for r in rows)


# ---------------------------------------------------------------- `uv sync` that relocks (hardening-4 review)
@pytest.mark.parametrize("cmd", ["uv sync --upgrade", "uv sync -U", "uv sync --upgrade-package pyyaml", "uv sync -P pyyaml"])
def test_uv_sync_that_upgrade_rewrite_the_lock_of_a_jade_checkout(checkout, cmd):
    a = policy.assess("Bash", {"command": cmd}, str(checkout))
    assert a.level == "high" and a.rule_id == "R-24", (cmd, a.rule_id)


@pytest.mark.parametrize("cmd", ["uv sync", "uv run pytest", "uv sync --upgrade --frozen", "uv sync --upgrade --locked"])
def test_uv_sync_that_do_not_relock_stay_low(checkout, cmd):
    assert policy.assess("Bash", {"command": cmd}, str(checkout)).level == "low"


def test_uv_sync_upgrade_in_another_project_is_not_r24(other):
    assert policy.assess("Bash", {"command": "uv sync --upgrade"}, str(other)).level == "low"
