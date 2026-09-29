"""Save the options and arguments of every book-loader command (T0.3).

The result, ``tests/fixtures/golden/help_options.json``, is the contract that
``tests/cli/test_help_parity.py`` checks: no command, option, alias or choice may
disappear, and no argument may change shape. New ones are allowed.

    uv run python -m tests.tools.dump_help             # update the golden file
    uv run python -m tests.tools.dump_help --stdout    # print, write nothing

Updating refuses to drop anything already in the golden file, unless
``--allow-removals`` is given for a removal that was decided in REFACTOR_PLAN.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import click

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "help_options.json"
PROGRAM = "book-loader"

CommandSpec = dict[str, Any]


def load_cli() -> click.Command:
    """The command behind the installed ``book-loader`` entry point."""
    (entry_point,) = entry_points(group="console_scripts", name=PROGRAM)
    return entry_point.load()


def _describe_option(option: click.Option) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "names": sorted(option.opts + option.secondary_opts),
        # A flag that starts taking a value would swallow the next word of a script.
        "takes_value": not (option.is_flag or option.count),
    }
    if isinstance(option.type, click.Choice):
        spec["choices"] = sorted(str(choice) for choice in option.type.choices)
    return spec


def _describe_argument(argument: click.Argument) -> dict[str, Any]:
    return {"name": argument.name, "nargs": argument.nargs, "required": argument.required}


def describe(command: click.Command) -> CommandSpec:
    options = [_describe_option(p) for p in command.params if isinstance(p, click.Option)]
    arguments = [_describe_argument(p) for p in command.params if isinstance(p, click.Argument)]
    return {
        "options": sorted(options, key=lambda o: o["names"]),
        "arguments": arguments,
    }


def collect(root: click.Command, path: str = PROGRAM) -> dict[str, CommandSpec]:
    """Every command and group under ``root``, keyed by its full command line."""
    specs = {path: describe(root)}
    if isinstance(root, click.Group):
        for name, sub in sorted(root.commands.items()):
            specs.update(collect(sub, f"{path} {name}"))
    return specs


def find_removals(golden: dict[str, CommandSpec], current: dict[str, CommandSpec]) -> list[str]:
    """What ``current`` lost compared with ``golden``. Additions are allowed."""
    problems: list[str] = []
    for path, old in golden.items():
        new = current.get(path)
        if new is None:
            problems.append(f"{path}: command removed")
            continue
        problems += _option_removals(path, old["options"], new["options"])
        problems += _argument_changes(path, old["arguments"], new["arguments"])
    return problems


def _option_removals(path: str, old_options: list[dict], new_options: list[dict]) -> list[str]:
    problems = []
    for old in old_options:
        label = "/".join(old["names"])
        # Every old name must still belong to one option; extra aliases are fine.
        new = next((o for o in new_options if set(old["names"]) <= set(o["names"])), None)
        if new is None:
            problems.append(f"{path}: option {label} removed or split")
            continue
        if new["takes_value"] != old["takes_value"]:
            was = "takes a value" if old["takes_value"] else "is a flag"
            problems.append(f"{path}: option {label} no longer {was}")
        if "choices" in old and "choices" in new:
            lost = sorted(set(old["choices"]) - set(new["choices"]))
            if lost:
                problems.append(f"{path}: option {label} lost choices {', '.join(lost)}")
    return problems


def _argument_changes(path: str, old_args: list[dict], new_args: list[dict]) -> list[str]:
    problems = []
    for position, old in enumerate(old_args):
        if position >= len(new_args):
            problems.append(f"{path}: argument {old['name']} removed")
            continue
        new = new_args[position]
        if new["nargs"] != old["nargs"]:
            problems.append(f"{path}: argument {old['name']} changed nargs")
        if new["required"] and not old["required"]:
            problems.append(f"{path}: argument {old['name']} became required")
    for new in new_args[len(old_args) :]:
        if new["required"]:
            problems.append(f"{path}: new required argument {new['name']}")
    return problems


def load_golden(path: Path = GOLDEN) -> dict[str, CommandSpec]:
    return json.loads(path.read_text(encoding="utf-8"))


def to_json(specs: dict[str, CommandSpec]) -> str:
    return json.dumps(specs, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    parser.add_argument("--output", type=Path, default=GOLDEN, help="file to write")
    parser.add_argument(
        "--allow-removals",
        action="store_true",
        help="write even if commands or options were removed (a decided change only)",
    )
    args = parser.parse_args(argv)

    current = collect(load_cli())
    if args.stdout:
        sys.stdout.write(to_json(current))
        return 0

    if args.output.exists():
        problems = find_removals(load_golden(args.output), current)
        if problems and not args.allow_removals:
            print("Not written; the CLI lost:", *problems, sep="\n  ", file=sys.stderr)
            return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as f:
        f.write(to_json(current))
    print(f"Wrote {len(current)} commands to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
