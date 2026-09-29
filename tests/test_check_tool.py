"""Tests for the local check script (T0.5.4)."""

from __future__ import annotations

from tests.tools.check import ROOT, environment_for, plan, pyright_configured


def names(steps):
    return [(s.name, s.python) for s in steps]


def test_default_order():
    steps = plan(["3.11", "3.14"], pyright=False, pytest_args=[])
    assert names(steps) == [
        ("sync", "3.11"),
        ("ruff", "3.11"),
        ("black", "3.11"),
        ("pytest", "3.11"),
        ("sync", "3.14"),
        ("pytest", "3.14"),
    ]


def test_pyright_runs_for_every_version_once_configured():
    steps = plan(["3.11", "3.14"], pyright=True, pytest_args=[])
    assert [s.python for s in steps if s.name == "pyright"] == ["3.11", "3.14"]


def test_sync_uses_the_lock_file_and_commands_skip_syncing():
    sync, *rest = plan(["3.14"], pyright=False, pytest_args=[])
    assert sync.command == ("uv", "sync", "--locked", "--python", "3.14")
    assert all(s.command[:5] == ("uv", "run", "--no-sync", "--python", "3.14") for s in rest)


def test_pytest_gets_coverage_and_extra_arguments():
    pytest_step = plan(["3.11"], pyright=False, pytest_args=["-k", "kobo"])[-1]
    assert pytest_step.command[-4:] == (
        "--cov=book_loader",
        "--cov-report=term:skip-covered",
        "-k",
        "kobo",
    )


def test_each_version_gets_its_own_environment():
    assert environment_for("3.11")["UV_PROJECT_ENVIRONMENT"] == str(ROOT / ".venv-py3.11")


def test_pyright_detection(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.black]\n", encoding="utf-8")
    assert not pyright_configured(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.pyright]\ninclude = []\n", encoding="utf-8")
    assert pyright_configured(tmp_path)
