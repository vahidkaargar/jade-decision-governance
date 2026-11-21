"""W-95: opening a second ledger connection must not release the first one's locks.

POSIX advisory locks belong to the process, not to the file descriptor: when a process closes ANY descriptor of a file, the kernel drops every lock the
process holds on that file, including the ones a SQLite connection took through its own descriptor (https://www.sqlite.org/howtocorrupt.html, 2.2.1).
`Ledger.__init__` used to create the file with `os.close(os.open(path, O_RDWR | O_CREAT))`, so every second connection in a process silently dropped the
locks of the first. Another process could then run WAL recovery on the file, or delete `-wal` and `-shm`, under the first connection, which kept reading its
stale shared-memory map ("database disk image is malformed"; CI flake on Linux, Python 3.13, in the parallel-hook tests)."""
import os
import subprocess
import sys

import pytest

from jade import ledger

fcntl = pytest.importorskip("fcntl")

PROBE = (
    "import fcntl, sys\n"
    "f = open(sys.argv[1], 'r+b')\n"
    "try:\n"
    "    fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "    print('free')\n"
    "except OSError:\n"
    "    print('held')\n"
)


def held_by_someone(path) -> bool:
    """True when another process cannot take an exclusive lock on the whole file, that is, some process holds a (shared) lock on it."""
    out = subprocess.run([sys.executable, "-c", PROBE, str(path)], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    return out == "held"


def test_the_probe_sees_the_lock_of_an_open_connection(tmp_path):
    path = tmp_path / "h" / "jade.sqlite"
    a = ledger.open(path)
    assert held_by_someone(path)       # the premise of the test below: a WAL connection holds a shared lock on the file for as long as it is open
    a.close()
    assert not held_by_someone(path)


def test_a_second_connection_in_the_same_process_keeps_the_first_ones_lock(tmp_path):
    path = tmp_path / "h" / "jade.sqlite"
    a = ledger.open(path)
    assert held_by_someone(path)
    b = ledger.open(path)              # used to close a descriptor of the file, and with it every lock this process held on it
    try:
        assert held_by_someone(path)
    finally:
        b.close()
        assert held_by_someone(path)   # `a` is still open
        a.close()


def test_a_new_ledger_file_is_still_created_with_mode_600(tmp_path):
    path = tmp_path / "h" / "jade.sqlite"
    ledger.open(path).close()
    assert path.stat().st_mode & 0o777 == 0o600


def mode_when_sqlite_opens(monkeypatch, path, target) -> int | None:
    """The mode of `target` at the moment SQLite is asked to open `path`, before `_lock_down` chmods anything; None when it does not exist yet.
    The umask is 022 for the call, so a file SQLite creates itself would be 0644."""
    seen = []
    real = ledger.sqlite3.connect

    def spy(*a, **kw):
        seen.append(os.stat(target).st_mode & 0o777 if os.path.exists(target) else None)
        return real(*a, **kw)

    monkeypatch.setattr(ledger.sqlite3, "connect", spy)
    old = os.umask(0o022)
    try:
        ledger.open(path).close()
    finally:
        os.umask(old)
    return seen[0]


def test_a_new_file_is_0600_before_sqlite_opens_it(tmp_path, monkeypatch):
    path = tmp_path / "h" / "jade.sqlite"
    assert mode_when_sqlite_opens(monkeypatch, path, path) == 0o600


def test_a_dangling_symlink_at_the_ledger_path_gets_a_0600_target_before_sqlite_opens_it(tmp_path, monkeypatch):
    # O_EXCL refuses a path that is a symlink, even a dangling one; SQLite would then create the target itself with the umask mode (0644 here).
    path = tmp_path / "h" / "jade.sqlite"
    target = tmp_path / "real" / "ledger.sqlite"
    target.parent.mkdir()
    path.parent.mkdir()
    path.symlink_to(target)
    assert mode_when_sqlite_opens(monkeypatch, path, target) == 0o600
    assert path.is_symlink() and target.stat().st_mode & 0o777 == 0o600


def test_a_second_connection_through_a_symlink_keeps_the_first_ones_lock(tmp_path):
    target = tmp_path / "real" / "ledger.sqlite"
    path = tmp_path / "h" / "jade.sqlite"
    target.parent.mkdir()
    path.parent.mkdir()
    path.symlink_to(target)
    a = ledger.open(path)
    assert held_by_someone(target)
    b = ledger.open(path)              # resolving the link must not open a descriptor of the existing target
    try:
        assert held_by_someone(target)
    finally:
        b.close()
        assert held_by_someone(target)
        a.close()


LONG = 45   # more links than either kernel follows in one path lookup (macOS 32, Linux 40); SQLite resolves the links itself and still opens the file


def link_chain(d, n, end="ledger.sqlite"):
    """`n` relative symlinks c{n-1}.sqlite -> ... -> c0.sqlite -> `end` in `d`; returns the first link."""
    prev = end
    for i in range(n):
        (d / f"c{i}.sqlite").symlink_to(prev)
        prev = f"c{i}.sqlite"
    return d / prev


def modes(d, name="ledger.sqlite"):
    return {s: (d / f"{name}{s}").stat().st_mode & 0o777 for s in ("", "-wal", "-shm") if (d / f"{name}{s}").exists()}


def test_a_dangling_link_chain_longer_than_the_kernel_follows_still_gets_a_0600_target_before_sqlite_opens_it(tmp_path, monkeypatch):
    # One stat of the whole chain fails with ELOOP; SQLite still opens it, and used to create the target with the umask mode (0644 here) for good.
    path = link_chain(tmp_path, LONG)
    target = tmp_path / "ledger.sqlite"
    assert mode_when_sqlite_opens(monkeypatch, path, target) == 0o600
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("n", [1, LONG])
def test_an_existing_0644_ledger_behind_links_is_tightened_with_its_wal_and_shm(tmp_path, n):
    # SQLite puts -wal and -shm next to the file it really opened, not next to the link; JADE used to chmod `<link>-wal` and miss them.
    target = tmp_path / "ledger.sqlite"
    ledger.open(target).close()
    os.chmod(target, 0o644)
    path = link_chain(tmp_path, n)
    old = os.umask(0o022)
    try:
        db = ledger.open(path)
    finally:
        os.umask(old)
    try:
        got = modes(tmp_path)
        assert "-wal" in got and "-shm" in got
        assert got == {"": 0o600, "-wal": 0o600, "-shm": 0o600}
    finally:
        db.close()


def test_a_second_connection_through_a_long_link_chain_keeps_the_first_ones_lock(tmp_path):
    path = link_chain(tmp_path, LONG)
    target = tmp_path / "ledger.sqlite"
    a = ledger.open(path)
    assert held_by_someone(target)
    b = ledger.open(path)
    try:
        assert held_by_someone(target)
    finally:
        b.close()
        assert held_by_someone(target)
        a.close()


def test_a_symlink_loop_at_the_ledger_path_ends_and_creates_nothing(tmp_path):
    (tmp_path / "a.sqlite").symlink_to("b.sqlite")
    (tmp_path / "b.sqlite").symlink_to("a.sqlite")
    with pytest.raises(ledger.sqlite3.OperationalError):
        ledger.open(tmp_path / "a.sqlite")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.sqlite", "b.sqlite"]
