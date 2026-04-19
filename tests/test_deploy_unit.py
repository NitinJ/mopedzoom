"""Tests for J28: systemd user unit template + install script."""

from __future__ import annotations

import configparser
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIT_FILE = REPO_ROOT / "deploy" / "mopedzoomd.service"
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


# ---------- systemd unit ------------------------------------------------------


def test_unit_file_exists():
    assert UNIT_FILE.is_file(), f"missing {UNIT_FILE}"


def test_unit_parses_as_ini():
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    # systemd unit files are INI-like; configparser handles them for our purposes.
    parser.read(UNIT_FILE)
    assert set(parser.sections()) >= {"Unit", "Service", "Install"}


def test_unit_has_required_fields():
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read(UNIT_FILE)

    assert parser["Unit"]["Description"]
    assert parser["Unit"]["After"] == "network.target"

    svc = parser["Service"]
    assert svc["Type"] == "simple"
    assert svc["WorkingDirectory"] == "%h/.mopedzoom"
    assert svc["ExecStart"].startswith("{{PLUGIN_PATH}}/")
    assert svc["ExecStart"].endswith("/mopedzoomd")
    assert svc["Restart"] == "on-failure"
    assert svc["RestartSec"] == "3"

    assert parser["Install"]["WantedBy"] == "default.target"


def test_unit_has_env_and_logging():
    text = UNIT_FILE.read_text()
    # Environment= entries appear more than once — configparser collapses them,
    # so assert against the raw text.
    assert "Environment=PYTHONUNBUFFERED=1" in text
    assert "Environment=MOPEDZOOM_STATE=%h/.mopedzoom" in text
    assert "StandardOutput=append:%h/.mopedzoom/logs/mopedzoomd.log" in text
    assert "StandardError=append:%h/.mopedzoom/logs/mopedzoomd.log" in text


def test_unit_template_placeholder_present():
    # The install script must substitute {{PLUGIN_PATH}}.
    assert "{{PLUGIN_PATH}}" in UNIT_FILE.read_text()


# ---------- install script ----------------------------------------------------


def test_install_script_exists_and_executable():
    assert INSTALL_SH.is_file(), f"missing {INSTALL_SH}"
    mode = INSTALL_SH.stat().st_mode
    assert mode & stat.S_IXUSR, "install.sh must be executable"


def test_install_script_has_shebang_and_strict_mode():
    text = INSTALL_SH.read_text()
    assert text.startswith("#!/usr/bin/env bash\n") or text.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in text


def test_install_script_has_dry_run_flag():
    text = INSTALL_SH.read_text()
    assert "--dry-run" in text


def _run_install(tmp_path: Path, extra_args: list[str]) -> subprocess.CompletedProcess[str]:
    # Pretend $HOME is tmp_path so we never touch the real user's systemd state,
    # and skip systemctl — tests must be pure (no touching user systemd state).
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["XDG_CONFIG_HOME"] = str(tmp_path / ".config")
    env["MOPEDZOOM_PLUGIN_PATH"] = str(REPO_ROOT)
    env["MOPEDZOOM_SKIP_SYSTEMCTL"] = "1"
    return subprocess.run(
        ["bash", str(INSTALL_SH), *extra_args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_install_dry_run_does_not_touch_filesystem(tmp_path):
    res = _run_install(tmp_path, ["--dry-run"])
    assert res.returncode == 0, res.stderr
    # Nothing should have been written under HOME.
    systemd_dir = tmp_path / ".config" / "systemd" / "user"
    assert not systemd_dir.exists(), f"dry-run wrote to {systemd_dir}"
    # The script should announce what it *would* do.
    assert "dry-run" in res.stdout.lower() or "would" in res.stdout.lower()


def test_install_real_run_writes_unit_file(tmp_path):
    res = _run_install(tmp_path, [])
    assert res.returncode == 0, f"stderr={res.stderr}\nstdout={res.stdout}"

    installed = tmp_path / ".config" / "systemd" / "user" / "mopedzoomd.service"
    assert installed.exists(), "unit file should be installed under XDG_CONFIG_HOME"

    text = installed.read_text()
    # The placeholder must be substituted.
    assert "{{PLUGIN_PATH}}" not in text
    assert str(REPO_ROOT) in text


def test_install_is_idempotent(tmp_path):
    first = _run_install(tmp_path, [])
    assert first.returncode == 0, first.stderr
    second = _run_install(tmp_path, [])
    assert second.returncode == 0, f"second run failed: {second.stderr}"

    installed = tmp_path / ".config" / "systemd" / "user" / "mopedzoomd.service"
    assert installed.exists()


def test_install_does_not_invoke_systemctl(tmp_path):
    # Put a poisoned `systemctl` on PATH; if the script actually runs it, the test
    # fails. This enforces the "tests must be pure" constraint.
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    sentinel = tmp_path / "systemctl_called"
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(f"#!/usr/bin/env bash\ntouch {sentinel}\nexit 0\n")
    systemctl.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["XDG_CONFIG_HOME"] = str(tmp_path / ".config")
    env["MOPEDZOOM_PLUGIN_PATH"] = str(REPO_ROOT)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["MOPEDZOOM_SKIP_SYSTEMCTL"] = "1"

    res = subprocess.run(
        ["bash", str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert not sentinel.exists(), (
        "install.sh invoked systemctl; tests must be pure. Honour MOPEDZOOM_SKIP_SYSTEMCTL=1."
    )


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_install_help(tmp_path, flag):
    res = _run_install(tmp_path, [flag])
    assert res.returncode == 0
    assert "dry-run" in res.stdout.lower()
