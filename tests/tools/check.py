"""The local check: the gate every task must pass (T0.5.4, REFACTOR_PLAN §2.1).

    uv run python tests/tools/check.py                    # Python 3.11, then 3.14
    uv run python tests/tools/check.py --python 3.14      # one version (what CI runs)
    uv run python tests/tools/check.py -- -k kobo         # extra pytest arguments

For each Python version it syncs a separate environment (``.venv-py<version>``) from the
lock file, then runs ruff, black ``--check``, pyright (once a pyright config exists,
from T2.14) and pytest with coverage. Ruff and black don't depend on the Python version,
so they run only for the first one. It stops at the first failing step.

Standard library only, so CI can run it before anything is installed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PYTHONS = ("3.11", "3.14")


@dataclass(frozen=True)
class Step:
    name: str
    python: str
    command: tuple[str, ...]


def pyright_configured(root: Path = ROOT) -> bool:
    if (root / "pyrightconfig.json").exists():
        return True
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return "pyright" in pyproject.get("tool", {})


def plan(pythons: list[str], pyright: bool, pytest_args: list[str]) -> list[Step]:
    steps = []
    for index, python in enumerate(pythons):

        def run(*command: str, python: str = python) -> tuple[str, ...]:
            return ("uv", "run", "--no-sync", "--python", python, *command)

        steps.append(Step("sync", python, ("uv", "sync", "--locked", "--python", python)))
        if index == 0:
            steps.append(Step("ruff", python, run("ruff", "check", "src", "tests")))
            steps.append(Step("black", python, run("black", "--check", "src", "tests")))
        if pyright:
            steps.append(Step("pyright", python, run("pyright")))
        steps.append(
            Step(
                "pytest",
                python,
                run(
                    "pytest",
                    "--cov=book_loader",
                    "--cov-report=term:skip-covered",
                    *pytest_args,
                ),
            )
        )
    return steps


def environment_for(python: str) -> dict[str, str]:
    env = dict(os.environ)
    env["UV_PROJECT_ENVIRONMENT"] = str(ROOT / f".venv-py{python}")
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    parser.add_argument(
        "--python",
        nargs="+",
        default=list(DEFAULT_PYTHONS),
        help=f"Python versions to check, in order (default: {' '.join(DEFAULT_PYTHONS)})",
    )
    parser.add_argument("pytest_args", nargs="*", help="extra pytest arguments, after --")
    args = parser.parse_args(argv)

    steps = plan(args.python, pyright_configured(), args.pytest_args)
    started = time.monotonic()
    for number, step in enumerate(steps, start=1):
        print(f"\n=== [{number}/{len(steps)}] {step.name} (Python {step.python})", flush=True)
        print("$ " + " ".join(step.command), flush=True)
        code = subprocess.run(step.command, cwd=ROOT, env=environment_for(step.python)).returncode
        if code != 0:
            print(f"\nCHECK FAILED at {step.name} (Python {step.python}), exit code {code}")
            return 1
    print(f"\nCHECK PASSED: {len(steps)} steps in {time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
