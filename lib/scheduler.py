"""Cross-platform scheduler for the optional ``gw <service> sync-crm`` jobs.

One entry point — ``install(service, interval_hours)`` / ``uninstall(service)`` —
that registers a recurring job on the host's native scheduler:

- **macOS**: a launchd LaunchAgent (loaded immediately).
- **Windows**: a Task Scheduler task (``schtasks``) pointing at a generated
  wrapper ``.cmd`` that re-exports the connector env vars first.
- **Linux**: a line in the user crontab, tagged with a marker comment.

A scheduler does not inherit your interactive shell env, so the knowledge-base
location and connector settings (``GW_*``) are captured at install time and
carried into the detached job. If the native scheduler can't be driven
(command missing, non-zero exit), we fall back to printing the exact command so
the user is never left without a path forward, and never has privileges taken
on their behalf silently.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click

import gw_config

# GW_* env vars a detached job needs to find the vault + connector config.
_ENV_KEYS = ("GW_KB_CONFIG", "GW_CONFIG_DIR", "GW_INTERNAL_DOMAINS",
             "GW_TRANSCRIPT_DIRS", "GW_SLACK_USER_ID", "GW_SLACK_CLI_BIN",
             "GW_EXA_LIB")


def _entrypoint() -> tuple[str, Path]:
    """Return (python_executable, path to cli/gw.py)."""
    plugin_root = Path(__file__).resolve().parent.parent
    return sys.executable, plugin_root / "cli" / "gw.py"


def _env_passthrough() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_KEYS if os.environ.get(k)}
    # A detached job runs from a bare working directory, so it can't walk up to a
    # project-local .claude/knowledge-base.local.md. Resolve the config the user
    # has right now and pin it, so the scheduled job syncs the same vault.
    if "GW_KB_CONFIG" not in env:
        found = gw_config.find_kb_config()
        if found is not None:
            env["GW_KB_CONFIG"] = str(found)
    return env


def _label(service: str) -> str:
    return f"gw-{service}-crm-sync"


# ---------------------------------------------------------------------------
# macOS — launchd
# ---------------------------------------------------------------------------

def _install_macos(service: str, interval_hours: int, python: str, gw_py: Path,
                   env: dict[str, str]) -> bool:
    import plistlib
    label = f"com.gw.{service}-crm-sync"
    log_dir = Path.home() / "Library" / "Logs" / "gw"
    log_dir.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": label,
        "ProgramArguments": [python, str(gw_py), service, "sync-crm"],
        "StartInterval": interval_hours * 3600,
        "StandardOutPath": str(log_dir / f"{service}-crm-sync.out.log"),
        "StandardErrorPath": str(log_dir / f"{service}-crm-sync.err.log"),
        "RunAtLoad": False,
    }
    if env:
        plist["EnvironmentVariables"] = env
    dest = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "unload", str(dest)], capture_output=True, text=True)
    result = subprocess.run(["launchctl", "load", str(dest)], capture_output=True, text=True)
    if result.returncode != 0:
        click.echo(click.style(f"launchctl load failed: {result.stderr.strip()}", fg="red"))
        return False
    click.echo(click.style(f"Installed launchd job {label} (every {interval_hours}h).", fg="green"))
    click.echo(f"  Plist: {dest}")
    click.echo(f"  Logs:  {log_dir}")
    return True


def _uninstall_macos(service: str) -> None:
    label = f"com.gw.{service}-crm-sync"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist_path.exists():
        click.echo(click.style("launchd job not installed.", fg="yellow"))
        return
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, text=True)
    plist_path.unlink(missing_ok=True)
    click.echo(click.style(f"Uninstalled launchd job {label}.", fg="green"))


# ---------------------------------------------------------------------------
# Windows — Task Scheduler (schtasks) + env wrapper
# ---------------------------------------------------------------------------

def _wrapper_dir() -> Path:
    d = gw_config.config_dir() / "schedule"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_windows_wrapper(service: str, python: str, gw_py: Path,
                           env: dict[str, str]) -> Path:
    """Write a .cmd that re-exports env vars then runs the gw sync-crm job."""
    lines = ["@echo off"]
    for k, v in env.items():
        lines.append(f'set "{k}={v}"')
    lines.append(f'"{python}" "{gw_py}" {service} sync-crm')
    path = _wrapper_dir() / f"{_label(service)}.cmd"
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def _install_windows(service: str, interval_hours: int, python: str, gw_py: Path,
                     env: dict[str, str]) -> bool:
    wrapper = _write_windows_wrapper(service, python, gw_py, env)
    task = _label(service)
    cmd = ["schtasks", "/Create", "/TN", task, "/SC", "HOURLY",
           "/MO", str(interval_hours), "/TR", str(wrapper), "/F"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        click.echo(click.style(f"schtasks failed: {result.stderr.strip() or result.stdout.strip()}", fg="red"))
        return False
    click.echo(click.style(f"Installed Task Scheduler task {task} (every {interval_hours}h).", fg="green"))
    click.echo(f"  Wrapper: {wrapper}")
    return True


def _uninstall_windows(service: str) -> None:
    task = _label(service)
    result = subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"],
                            capture_output=True, text=True)
    wrapper = _wrapper_dir() / f"{task}.cmd"
    wrapper.unlink(missing_ok=True)
    if result.returncode != 0:
        click.echo(click.style(f"Task not found or already removed: {task}", fg="yellow"))
        return
    click.echo(click.style(f"Uninstalled Task Scheduler task {task}.", fg="green"))


# ---------------------------------------------------------------------------
# Linux — user crontab
# ---------------------------------------------------------------------------

def _cron_marker(service: str) -> str:
    return f"# {_label(service)}"


def _read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    # rc != 0 with "no crontab" is normal for a user with no crontab yet.
    if result.returncode != 0 and "no crontab" not in (result.stderr + result.stdout).lower():
        raise RuntimeError(result.stderr.strip() or "crontab -l failed")
    return [ln for ln in result.stdout.splitlines()]


def _write_crontab(lines: list[str]) -> None:
    content = "\n".join(lines).rstrip("\n") + "\n"
    result = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "crontab - failed")


def _install_linux(service: str, interval_hours: int, python: str, gw_py: Path,
                   env: dict[str, str]) -> bool:
    marker = _cron_marker(service)
    env_prefix = " ".join(f'{k}="{v}"' for k, v in env.items())
    job = f'"{python}" "{gw_py}" {service} sync-crm'
    schedule = f"0 */{interval_hours} * * *"
    line = f"{schedule} {env_prefix + ' ' if env_prefix else ''}{job} {marker}"
    try:
        existing = [ln for ln in _read_crontab() if marker not in ln]
        _write_crontab(existing + [line])
    except (RuntimeError, FileNotFoundError) as exc:
        click.echo(click.style(f"Could not update crontab automatically ({exc}). Add this line with `crontab -e`:", fg="yellow"))
        click.echo(f"  {line}")
        return False
    click.echo(click.style(f"Installed cron job {_label(service)} (every {interval_hours}h).", fg="green"))
    return True


def _uninstall_linux(service: str) -> None:
    marker = _cron_marker(service)
    try:
        existing = _read_crontab()
        filtered = [ln for ln in existing if marker not in ln]
        if len(filtered) == len(existing):
            click.echo(click.style("cron job not installed.", fg="yellow"))
            return
        _write_crontab(filtered)
    except (RuntimeError, FileNotFoundError) as exc:
        click.echo(click.style(f"Could not update crontab automatically ({exc}). Remove the line marked `{marker}` with `crontab -e`.", fg="yellow"))
        return
    click.echo(click.style(f"Uninstalled cron job {_label(service)}.", fg="green"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install(service: str, interval_hours: int) -> None:
    """Register a recurring ``gw <service> sync-crm`` job on the native scheduler."""
    interval_hours = max(1, interval_hours)
    python, gw_py = _entrypoint()
    env = _env_passthrough()
    if sys.platform == "darwin":
        _install_macos(service, interval_hours, python, gw_py, env)
    elif sys.platform == "win32":
        _install_windows(service, interval_hours, python, gw_py, env)
    else:
        _install_linux(service, interval_hours, python, gw_py, env)


def uninstall(service: str) -> None:
    """Remove the recurring job for ``service`` from the native scheduler."""
    if sys.platform == "darwin":
        _uninstall_macos(service)
    elif sys.platform == "win32":
        _uninstall_windows(service)
    else:
        _uninstall_linux(service)
