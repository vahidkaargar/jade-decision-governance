"""V6-11 (DX-P2, FN-12): retention is scheduled where launchd does not exist, and `jade check` says when nothing runs `jade maintain`.

Retention, expiry sweeps, the drift canary and the miner run only inside `jade maintain`. The scheduler helper was launchd only, so on Linux nothing purged
unless the user knew to schedule it. `jade maintain-schedule` prints a cron line or systemd units (it installs nothing). `jade maintain` leaves a last-run
stamp in the ledger; the `maintain job` row reads it, so the row is honest under any scheduler: a stamp younger than three days is ok, anything else warns.
"""

from __future__ import annotations

import configparser
from datetime import timedelta

import pytest

from jade import cli, ledger, ops
from jade.util import now, ts


@pytest.fixture
def jade_path(monkeypatch, tmp_path):
    p = tmp_path / "bin dir" / "jade"      # a space: the generated text must quote it
    monkeypatch.setenv("JADE_BIN", str(p))
    return p


def row():
    return {n: (ok, d) for n, ok, d in ops.check(live=False)}["maintain job"]


def stamp(value) -> None:
    db = ledger.open()
    db.meta_set("last_maintain_ts", value)
    db.close()


def test_the_cron_line_runs_maintain_daily_with_the_path_quoted(jade_path, capsys):
    assert cli.main(["maintain-schedule"]) == 0
    line = capsys.readouterr().out
    assert line.count("\n") == 1 and line.startswith("15 9 * * * ")
    assert f"'{jade_path}' maintain" in line and line.rstrip().endswith("2>&1") and "maintain.log" in line


def test_a_percent_sign_in_a_path_cannot_end_the_cron_line(monkeypatch):
    monkeypatch.setenv("JADE_BIN", "/opt/we%ird/jade")
    assert "we\\%ird" in ops.maintain_schedule("cron") and "%i" not in ops.maintain_schedule("cron").replace("\\%", "")


def test_the_systemd_text_has_a_service_and_a_timer_that_parse(jade_path, capsys):
    assert cli.main(["maintain-schedule", "--format", "systemd"]) == 0
    text = capsys.readouterr().out
    service, timer = text.split("# ~/.config/systemd/user/jade-maintain.timer\n")
    assert service.startswith("# ~/.config/systemd/user/jade-maintain.service\n")
    svc, tim = configparser.ConfigParser(strict=False, interpolation=None), configparser.ConfigParser(strict=False, interpolation=None)
    svc.read_string(service.split("\n", 1)[1])
    tim.read_string(timer)
    assert svc["Service"]["Type"] == "oneshot" and svc["Service"]["ExecStart"] == f'"{jade_path}" maintain'
    assert tim["Timer"]["OnCalendar"] == "*-*-* 09:15:00" and tim["Timer"]["Persistent"] == "true" and tim["Install"]["WantedBy"] == "timers.target"


def test_an_unknown_format_is_refused():
    with pytest.raises(SystemExit):
        cli.main(["maintain-schedule", "--format", "windows"])
    with pytest.raises(ValueError):
        ops.maintain_schedule("windows")


def test_the_daily_job_leaves_a_stamp_the_check_reads(monkeypatch):
    monkeypatch.setattr(ops, "check", lambda live=True, **k: [])
    assert ledger.open().meta_get("last_maintain_ts") is None
    assert cli.main(["maintain", "--offline"]) == 0
    assert ledger.open().meta_get("last_maintain_ts")


def test_a_run_that_stopped_part_way_leaves_no_new_stamp(monkeypatch):
    stamp("2000-01-01T00:00:00Z")
    monkeypatch.setattr(ops, "purge", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        cli.main(["maintain", "--offline"])
    assert ledger.open().meta_get("last_maintain_ts") == "2000-01-01T00:00:00Z"


# ---- the row, in each of its states ------------------------------------------------------------------------------------
def test_no_job_and_no_stamp_warns_and_names_the_command_that_prints_a_schedule():
    ok, detail = row()
    assert ok is None and detail.startswith("not installed") and "do not run" in detail
    assert "never left a last-run stamp" in detail and "jade maintain-schedule" in detail


def test_a_fresh_stamp_without_a_launchagent_is_ok():
    stamp(ts(now() - timedelta(hours=20)))
    ok, detail = row()
    assert ok is True and "last ran" in detail and "another scheduler" in detail


def test_an_old_stamp_without_a_launchagent_warns_with_its_date():
    old = ts(now() - timedelta(days=ops.MAINTAIN_STALE_DAYS + 2))
    stamp(old)
    ok, detail = row()
    assert ok is None and f"It last ran {old}" in detail and "jade maintain-schedule" in detail


def test_a_stamp_that_is_not_a_time_counts_as_no_stamp():
    stamp("yesterday")
    ok, detail = row()
    assert ok is None and "never left a last-run stamp" in detail


def test_a_loaded_launchagent_is_judged_as_before_whatever_the_stamp_says(monkeypatch, tmp_path):
    exe = tmp_path / "jade"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(ops, "maintain_jobs", lambda: [(tmp_path / "x.plist", "com.example.jade-maintain", [str(exe), "maintain"])])
    monkeypatch.setattr(ops, "_launchctl_loaded", lambda label: False)
    assert row()[0] is False
    stamp(ts(now()))
    assert row()[0] is False, "a plist launchd has not loaded is a FAIL even if something else ran the job once"
