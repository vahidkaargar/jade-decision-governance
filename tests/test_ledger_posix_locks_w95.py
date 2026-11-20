"""W-95: opening a second ledger connection must not release the first one's locks.

POSIX advisory locks belong to the process, not to the file descriptor: when a process closes ANY descriptor of a file, the kernel drops every lock the
process holds on that file, including the ones a SQLite connection took through its own descriptor (https://www.sqlite.org/howtocorrupt.html, 2.2.1).
`Ledger.__init__` used to create the file with `os.close(os.open(path, O_RDWR | O_CREAT))`, so every second connection in a process silently dropped the
locks of the first. Another process could then run WAL recovery on the file, or delete `-wal` and `-shm`, under the first connection, which kept reading its
stale shared-memory map ("database disk image is malformed"; CI flake on Linux, Python 3.13, in the parallel-hook tests)."""
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
