"""Help parity (T0.3): no command, option or argument of 0.1.0 may disappear.

The golden file is written by ``tests/tools/dump_help.py``. The test compares option
names, not help text, so rich-click's formatting in Phase 6 doesn't break it.
"""

from __future__ import annotations

import click
import pytest

from tests.tools.dump_help import PROGRAM, collect, find_removals, load_cli, load_golden

GOLDEN = load_golden()


def _names(path: str) -> set[str]:
    return {name for option in GOLDEN[path]["options"] for name in option["names"]}


def test_no_command_option_or_argument_was_lost():
    problems = find_removals(GOLDEN, collect(load_cli()))
    assert problems == [], "The CLI lost:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("path", sorted(GOLDEN))
def test_help_renders(cli_runner, path):
    args = path.split()[1:] + ["--help"]
    result = cli_runner.invoke(load_cli(), args)
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.stdout


@pytest.mark.parametrize(
    ("path", "names"),
    [
        (f"{PROGRAM} process", {"--optimize", "--no-optimize"}),
        (f"{PROGRAM} process", {"-v", "--verbose"}),
        (f"{PROGRAM} process", {"--auth-dir"}),
        (f"{PROGRAM} auth reset", {"--yes"}),
    ],
)
def test_golden_file_pins_options_the_plan_names(path, names):
    """T0.3.3: options that are easy to lose in the rewrite are in the contract."""
    assert names <= _names(path)


class TestFindRemovals:
    """The comparison itself, on small made-up commands."""

    @staticmethod
    def _specs(*params: click.Parameter, sub: click.Command | None = None) -> dict:
        root = click.Group("tool", params=list(params))
        if sub is not None:
            root.add_command(sub)
        return collect(root, "tool")

    def test_identical_is_clean(self):
        old = self._specs(click.Option(["-o", "--output"]))
        assert find_removals(old, old) == []

    def test_additions_are_allowed(self):
        old = self._specs(
            click.Option(["-o", "--output"]),
            click.Option(["--engine"], type=click.Choice(["a"])),
            click.Argument(["src"]),
        )
        new = self._specs(
            click.Option(["-o", "--output", "--out"]),
            click.Option(["--engine"], type=click.Choice(["a", "b"])),
            click.Option(["--new"], is_flag=True),
            click.Argument(["src"]),
            click.Argument(["extra"], required=False),
            sub=click.Command("added"),
        )
        assert find_removals(old, new) == []

    def test_removed_option(self):
        old = self._specs(click.Option(["--to-pdf"], is_flag=True))
        assert find_removals(old, self._specs()) == ["tool: option --to-pdf removed or split"]

    def test_split_alias(self):
        old = self._specs(click.Option(["-o", "--output"]))
        new = self._specs(click.Option(["-o"]), click.Option(["--output"]))
        assert find_removals(old, new) == ["tool: option --output/-o removed or split"]

    def test_secondary_flag_name_counts(self):
        old = self._specs(click.Option(["--optimize/--no-optimize"]))
        new = self._specs(click.Option(["--optimize"], is_flag=True))
        assert find_removals(old, new) == ["tool: option --no-optimize/--optimize removed or split"]

    def test_flag_that_takes_a_value(self):
        old = self._specs(click.Option(["--keep"], is_flag=True))
        new = self._specs(click.Option(["--keep"]))
        assert find_removals(old, new) == ["tool: option --keep no longer is a flag"]

    def test_count_option_is_still_a_flag(self):
        old = self._specs(click.Option(["-v", "--verbose"], is_flag=True))
        new = self._specs(click.Option(["-v", "--verbose"], count=True))
        assert find_removals(old, new) == []

    def test_lost_choice(self):
        old = self._specs(click.Option(["--engine"], type=click.Choice(["python", "calibre"])))
        new = self._specs(click.Option(["--engine"], type=click.Choice(["python"])))
        assert find_removals(old, new) == ["tool: option --engine lost choices calibre"]

    def test_removed_command(self):
        old = self._specs(sub=click.Command("gone"))
        assert find_removals(old, self._specs()) == ["tool gone: command removed"]

    def test_argument_changes(self):
        old = self._specs(click.Argument(["src"]), click.Argument(["dst"], required=False))
        new = self._specs(
            click.Argument(["src"], nargs=2),
            click.Argument(["dst"]),
            click.Argument(["more"]),
        )
        assert find_removals(old, new) == [
            "tool: argument src changed nargs",
            "tool: argument dst became required",
            "tool: new required argument more",
        ]

    def test_removed_argument(self):
        old = self._specs(click.Argument(["src"]))
        assert find_removals(old, self._specs()) == ["tool: argument src removed"]
