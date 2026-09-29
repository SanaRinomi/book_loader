"""T2.3.1: ``domain/conflicts.py``, the conflict policy and resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from book_loader.domain.conflicts import (
    ConflictAction,
    ConflictPolicy,
    ConflictResolver,
    Resolution,
)
from book_loader.domain.errors import ConfigError, OperationCancelled
from tests.fakes.prompter import FakePrompter

A = Path("out") / "A.epub"
B = Path("out") / "B.epub"
C = Path("out") / "C.epub"
NEW = Path("out") / "New.epub"
EXISTING = {A, B, C}


def resolver(
    policy=ConflictPolicy.ASK, batch=True, **answers
) -> tuple[ConflictResolver, FakePrompter]:
    prompter = FakePrompter(**answers)
    return ConflictResolver(policy, prompter, batch, exists=EXISTING.__contains__), prompter


def run(resolver: ConflictResolver, paths) -> list[Resolution]:
    return [resolver.resolve(path, path.stem) for path in paths]


def test_conflict_actions():
    assert {action.value for action in ConflictAction} == {
        "overwrite",
        "skip",
        "rename",
        "overwrite_all",
        "skip_all",
        "cancel",
    }


class TestFromFlags:
    @pytest.mark.parametrize(
        "overwrite, skip_existing, expected",
        [
            (False, False, ConflictPolicy.ASK),
            (True, False, ConflictPolicy.OVERWRITE),
            (False, True, ConflictPolicy.SKIP),
        ],
    )
    def test_flags(self, overwrite, skip_existing, expected):
        assert ConflictPolicy.from_flags(overwrite, skip_existing) is expected

    def test_both_flags_are_invalid(self):
        # The same message as 0.1.0's kobo dedrm.
        with pytest.raises(ConfigError) as info:
            ConflictPolicy.from_flags(True, True)
        assert info.value.message == "--overwrite and --skip-existing cannot be used together"
        assert info.value.hint

    @pytest.mark.parametrize("default", list(ConflictPolicy))
    def test_default_applies_without_flags(self, default):
        assert ConflictPolicy.from_flags(False, False, default) is default

    def test_flags_win_over_the_default(self):
        assert (
            ConflictPolicy.from_flags(True, False, ConflictPolicy.RENAME)
            is ConflictPolicy.OVERWRITE
        )
        assert ConflictPolicy.from_flags(False, True, ConflictPolicy.RENAME) is ConflictPolicy.SKIP

    def test_values_match_library_toml(self):
        assert [policy.value for policy in ConflictPolicy] == ["ask", "overwrite", "skip", "rename"]


def test_only_skip_does_not_write():
    assert [r for r in Resolution if not r.writes] == [Resolution.SKIP]


class TestNoConflict:
    @pytest.mark.parametrize("policy", list(ConflictPolicy))
    @pytest.mark.parametrize("batch", [False, True])
    def test_a_new_path_is_written_without_asking(self, policy, batch):
        r, prompter = resolver(policy, batch)
        assert r.resolve(NEW, "New") is Resolution.WRITE
        assert prompter.calls == []

    @pytest.mark.parametrize("answer", [ConflictAction.SKIP_ALL, ConflictAction.OVERWRITE_ALL])
    def test_a_remembered_choice_never_applies_to_a_new_path(self, answer):
        # 0.1.0 skipped every book with --skip-existing (REFACTOR_PLAN §3).
        r, prompter = resolver(resolve_conflict=[answer])
        assert run(r, [A, NEW]) == [
            Resolution.SKIP if answer is ConflictAction.SKIP_ALL else Resolution.OVERWRITE,
            Resolution.WRITE,
        ]

    def test_skip_policy_writes_new_paths(self):
        assert run(resolver(ConflictPolicy.SKIP)[0], [A, NEW, B]) == [
            Resolution.SKIP,
            Resolution.WRITE,
            Resolution.SKIP,
        ]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    "policy, expected",
    [
        (ConflictPolicy.OVERWRITE, Resolution.OVERWRITE),
        (ConflictPolicy.SKIP, Resolution.SKIP),
        (ConflictPolicy.RENAME, Resolution.RENAME),
    ],
)
def test_fixed_policies_never_ask(policy, expected, batch):
    r, prompter = resolver(policy, batch)
    assert run(r, [A, B, C]) == [expected] * 3
    assert prompter.calls == []


class TestAsk:
    @pytest.mark.parametrize(
        "answer, expected",
        [
            (ConflictAction.OVERWRITE, Resolution.OVERWRITE),
            (ConflictAction.SKIP, Resolution.SKIP),
            (ConflictAction.RENAME, Resolution.RENAME),
        ],
    )
    def test_single_book(self, answer, expected):
        r, prompter = resolver(batch=False, resolve_conflict=[answer])
        assert r.resolve(A, "Title A") is expected
        assert prompter.asked("resolve_conflict") == [(A, "Title A", False)]

    @pytest.mark.parametrize(
        "answers, expected, asked",
        [
            # One answer per book.
            (
                [ConflictAction.OVERWRITE, ConflictAction.SKIP, ConflictAction.RENAME],
                [Resolution.OVERWRITE, Resolution.SKIP, Resolution.RENAME],
                3,
            ),
            # An "all" answer is remembered for the books that follow.
            ([ConflictAction.OVERWRITE_ALL], [Resolution.OVERWRITE] * 3, 1),
            ([ConflictAction.SKIP_ALL], [Resolution.SKIP] * 3, 1),
            (
                [ConflictAction.SKIP, ConflictAction.OVERWRITE_ALL],
                [Resolution.SKIP, Resolution.OVERWRITE, Resolution.OVERWRITE],
                2,
            ),
            (
                [ConflictAction.RENAME, ConflictAction.SKIP_ALL],
                [Resolution.RENAME, Resolution.SKIP, Resolution.SKIP],
                2,
            ),
        ],
    )
    def test_batch(self, answers, expected, asked):
        r, prompter = resolver(resolve_conflict=answers)
        assert run(r, [A, B, C]) == expected
        calls = prompter.asked("resolve_conflict")
        assert calls == [(path, path.stem, True) for path in [A, B, C][:asked]]

    def test_single_answers_are_not_remembered(self):
        r, prompter = resolver(resolve_conflict=[ConflictAction.SKIP, ConflictAction.SKIP])
        run(r, [A, B])
        assert r.remembered is None

    def test_remembered_choice_is_kept_across_new_paths(self):
        r, prompter = resolver(resolve_conflict=[ConflictAction.SKIP_ALL])
        assert run(r, [A, NEW, B]) == [Resolution.SKIP, Resolution.WRITE, Resolution.SKIP]
        assert r.remembered is Resolution.SKIP

    @pytest.mark.parametrize("batch", [False, True])
    def test_cancel_raises(self, batch):
        r, prompter = resolver(batch=batch, resolve_conflict=[ConflictAction.CANCEL])
        with pytest.raises(OperationCancelled):
            r.resolve(A, "A")

    def test_cancel_from_the_prompter_passes_through(self):
        # Ctrl+C or Esc in the menu.
        r, prompter = resolver(resolve_conflict=[OperationCancelled()])
        with pytest.raises(OperationCancelled):
            r.resolve(A, "A")

    def test_cancel_after_earlier_books(self):
        r, prompter = resolver(resolve_conflict=[ConflictAction.OVERWRITE, ConflictAction.CANCEL])
        assert r.resolve(A, "A") is Resolution.OVERWRITE
        with pytest.raises(OperationCancelled):
            r.resolve(B, "B")


def test_checks_the_real_file_system_by_default(tmp_path):
    existing = tmp_path / "Book.epub"
    existing.write_bytes(b"")
    r = ConflictResolver(ConflictPolicy.SKIP, FakePrompter(), batch=False)
    assert r.resolve(existing, "Book") is Resolution.SKIP
    assert r.resolve(tmp_path / "Other.epub", "Other") is Resolution.WRITE
