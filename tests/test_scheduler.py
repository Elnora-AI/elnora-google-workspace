"""Cross-platform scheduler command-building tests.

The actual launchd/schtasks/crontab execution is OS-specific and verified live on
each platform, but the command each platform *builds* is pure logic and locked in
here so a refactor can't silently break Windows or Linux scheduling.
"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import scheduler


class _Ok:
    returncode = 0
    stdout = ""
    stderr = ""


def test_windows_builds_schtasks_and_env_wrapper(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_KB_CONFIG", str(tmp_path / "kb.md"))
    monkeypatch.setattr(scheduler.gw_config, "config_dir", lambda: tmp_path)
    calls = []
    with mock.patch.object(scheduler.sys, "platform", "win32"), \
         mock.patch.object(scheduler.subprocess, "run", lambda cmd, **k: (calls.append(cmd), _Ok())[1]):
        scheduler.install("gmail", 3)

    schtasks = calls[-1]
    assert schtasks[0] == "schtasks"
    assert "/Create" in schtasks and "/F" in schtasks
    assert schtasks[schtasks.index("/TN") + 1] == "gw-gmail-crm-sync"
    assert schtasks[schtasks.index("/MO") + 1] == "3"

    wrapper = tmp_path / "schedule" / "gw-gmail-crm-sync.cmd"
    assert wrapper.exists()
    body = wrapper.read_text(encoding="utf-8")
    assert "GW_KB_CONFIG" in body           # env re-exported for the detached task
    assert "gmail sync-crm" in body


def test_linux_appends_marked_cron_line_preserving_existing(monkeypatch):
    monkeypatch.delenv("GW_KB_CONFIG", raising=False)
    monkeypatch.setattr(scheduler, "_env_passthrough", lambda: {})
    written = {}

    def fake_run(cmd, **kw):
        r = _Ok()
        if cmd == ["crontab", "-l"]:
            r = type("R", (), {"returncode": 0, "stdout": "0 5 * * * echo hi\n", "stderr": ""})()
        if cmd == ["crontab", "-"]:
            written["content"] = kw.get("input", "")
        return r

    with mock.patch.object(scheduler.sys, "platform", "linux"), \
         mock.patch.object(scheduler.subprocess, "run", fake_run):
        scheduler.install("calendar", 2)

    out = written["content"]
    assert "0 5 * * * echo hi" in out                       # existing line preserved
    assert "calendar sync-crm # gw-calendar-crm-sync" in out  # new marked line
    assert "0 */2 * * *" in out                              # every-2-hours schedule


def test_linux_uninstall_removes_only_marked_line(monkeypatch):
    written = {}

    def fake_run(cmd, **kw):
        if cmd == ["crontab", "-l"]:
            return type("R", (), {
                "returncode": 0,
                "stdout": "0 5 * * * echo hi\n0 */2 * * * x calendar sync-crm # gw-calendar-crm-sync\n",
                "stderr": "",
            })()
        if cmd == ["crontab", "-"]:
            written["content"] = kw.get("input", "")
        return _Ok()

    with mock.patch.object(scheduler.sys, "platform", "linux"), \
         mock.patch.object(scheduler.subprocess, "run", fake_run):
        scheduler.uninstall("calendar")

    out = written["content"]
    assert "echo hi" in out
    assert "gw-calendar-crm-sync" not in out


def test_env_passthrough_pins_resolved_kb_config(monkeypatch, tmp_path):
    """When GW_KB_CONFIG isn't set, install pins the currently-resolved config."""
    monkeypatch.delenv("GW_KB_CONFIG", raising=False)
    found = tmp_path / ".claude" / "knowledge-base.local.md"
    with mock.patch.object(scheduler.gw_config, "find_kb_config", return_value=found):
        env = scheduler._env_passthrough()
    assert env["GW_KB_CONFIG"] == str(found)
