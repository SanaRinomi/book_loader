"""T2.6: ``infra/names.py``, plain-mode parity and portable library names."""

from __future__ import annotations

import hashlib
import json
import random
import sys
import unicodedata
from pathlib import Path, PurePath

import pytest

from book_loader.domain.errors import ConfigError
from book_loader.domain.models import BookMetadata
from book_loader.infra.names import (
    WINDOWS_MAX_PATH,
    KoboNameRequest,
    assign_kobo_names,
    disambiguate,
    kobo_plain_name,
    library_component,
    render_template,
    short_volume_id,
    windows_long_paths_enabled,
)

GOLDEN = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "golden" / "safe_filename.json").read_text("utf-8")
)

UUID_A = "0a1b2c3d-1111-2222-3333-444455556666"
UUID_B = "9f8e7d6c-1111-2222-3333-444455556666"

# --- An independent statement of "valid on Windows, macOS and Linux" -------------------

FORBIDDEN = set('<>:"/\\|?*\x7f') | {chr(i) for i in range(32)}
RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{port}{n}" for port in ("COM", "LPT") for n in "123456789\u00b9\u00b2\u00b3"
}


def assert_portable(name: str) -> None:
    assert name and name not in (".", ".."), repr(name)
    assert not set(name) & FORBIDDEN, repr(name)
    assert name[0] not in " ." and name[-1] not in " .", repr(name)
    assert name.split(".")[0].rstrip(" ").upper() not in RESERVED, repr(name)
    assert len(name) <= 100, (len(name), name)
    assert len(name.encode("utf-8")) <= 255, repr(name)
    assert unicodedata.is_normalized("NFC", name), repr(name)


# --- Plain mode ------------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: repr(c["title"][:30]))
def test_kobo_plain_name_matches_0_1_0(case):
    assert kobo_plain_name(case["title"]) == case["filename"]


class TestShortVolumeId:
    def test_uuid_prefix(self):
        assert short_volume_id(UUID_A) == "0a1b2c3d"
        assert short_volume_id(UUID_A.upper()) == "0a1b2c3d"

    def test_other_ids_are_hashed(self):
        expected = hashlib.sha256(b"vol-alpha").hexdigest()[:8]
        assert short_volume_id("vol-alpha") == expected
        assert short_volume_id("vol-alpha-1") != short_volume_id("vol-alpha-2")

    def test_eight_characters(self):
        for volume_id in (UUID_A, "x", "vol-" * 20):
            assert len(short_volume_id(volume_id)) == 8


class TestDisambiguate:
    def test_author(self):
        assert disambiguate("Dune.epub", "Frank Herbert") == "Dune - Frank Herbert.epub"

    def test_author_and_volume_id(self):
        assert disambiguate("Dune.epub", "Frank Herbert", UUID_A) == (
            "Dune - Frank Herbert [0a1b2c3d].epub"
        )

    def test_no_author(self):
        assert disambiguate("Dune.epub", None) == "Dune.epub"
        assert disambiguate("Dune.epub", "  ", UUID_A) == "Dune [0a1b2c3d].epub"

    def test_author_follows_the_plain_character_rule(self):
        assert disambiguate("Book.epub", "O'Brien, Tim") == "Book - O_Brien_ Tim.epub"

    def test_full_id(self):
        assert disambiguate("Dune.epub", "A", "vol:1", full_id=True) == "Dune - A [vol_1].epub"

    def test_name_without_suffix(self):
        assert disambiguate("Dune", "A", UUID_A) == "Dune - A [0a1b2c3d]"


class TestAssignKoboNames:
    def test_unique_names_are_kept(self):
        books = {
            1: KoboNameRequest("Dune", "Frank Herbert", UUID_A),
            2: KoboNameRequest("Emma", "Jane Austen", UUID_B),
        }
        assert assign_kobo_names(books) == {1: "Dune.epub", 2: "Emma.epub"}

    def test_same_title_different_authors(self):
        books = {
            1: KoboNameRequest("Collected Poems", "A. Poet", UUID_A),
            2: KoboNameRequest("Collected Poems", "B. Poet", UUID_B),
        }
        assert assign_kobo_names(books) == {
            1: "Collected Poems - A_ Poet.epub",
            2: "Collected Poems - B_ Poet.epub",
        }

    def test_same_title_and_author(self):
        books = {
            "a": KoboNameRequest("Dune", "Frank Herbert", UUID_A),
            "b": KoboNameRequest("Dune", "Frank Herbert", UUID_B),
            "c": KoboNameRequest("Emma", "Jane Austen", "vol-emma"),
        }
        assert assign_kobo_names(books) == {
            "a": "Dune - Frank Herbert [0a1b2c3d].epub",
            "b": "Dune - Frank Herbert [9f8e7d6c].epub",
            "c": "Emma.epub",
        }

    def test_no_author(self):
        books = {1: KoboNameRequest("Dune", None, UUID_A), 2: KoboNameRequest("Dune", None, UUID_B)}
        assert assign_kobo_names(books) == {1: "Dune [0a1b2c3d].epub", 2: "Dune [9f8e7d6c].epub"}

    def test_names_that_differ_only_in_case_collide(self):
        books = {1: KoboNameRequest("Dune", "A", UUID_A), 2: KoboNameRequest("DUNE", "B", UUID_B)}
        assert assign_kobo_names(books) == {1: "Dune - A.epub", 2: "DUNE - B.epub"}

    def test_titles_that_collide_after_the_character_rule(self):
        books = {1: KoboNameRequest("A/B", "X", UUID_A), 2: KoboNameRequest("A?B", "Y", UUID_B)}
        assert assign_kobo_names(books) == {1: "A_B - X.epub", 2: "A_B - Y.epub"}

    def test_same_short_id_falls_back_to_the_full_id(self):
        first = "0a1b2c3d-0000-0000-0000-000000000001"
        second = "0a1b2c3d-0000-0000-0000-000000000002"
        books = {1: KoboNameRequest("Dune", "A", first), 2: KoboNameRequest("Dune", "A", second)}
        names = assign_kobo_names(books)
        assert names == {1: f"Dune - A [{first.replace('-', '_')}].epub", 2: names[2]}
        assert names[1] != names[2]

    def test_the_same_names_on_every_run_in_any_order(self):
        books = {
            n: KoboNameRequest(title, author, f"{n:08x}-0000-0000-0000-000000000000")
            for n, (title, author) in enumerate(
                [("Dune", "A"), ("Dune", "B"), ("Dune", "B"), ("Emma", None), ("emma", None)]
            )
        }
        expected = assign_kobo_names(books)
        assert len(set(expected.values())) == len(books)
        for seed in range(20):
            keys = list(books)
            random.Random(seed).shuffle(keys)
            assert assign_kobo_names({key: books[key] for key in keys}) == expected


# --- library_component -----------------------------------------------------------------


class TestLibraryComponent:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Dune", "Dune"),
            ('Title: Sub/Part\\2 <b> "q" | ? *', "Title_ Sub_Part_2 _b_ _q_ _ _ _"),
            ("Tabs\tand\nnewlines", "Tabs_and_newlines"),
            ("trailing dots...", "trailing dots"),
            ("  spaced  ", "spaced"),
            (".hidden", "hidden"),
            ("...", "_"),
            ("", "_"),
            ("R.U.R.", "R.U.R"),
            ("Caf\u0065\u0301", "Caf\u00e9"),  # NFD becomes NFC
            ("bad\ud800surrogate", "bad_surrogate"),
        ],
    )
    def test_rules(self, text, expected):
        assert library_component(text) == expected

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("CON", "CON_"),
            ("con", "con_"),
            ("Nul.txt", "Nul_.txt"),
            ("aux.tar.gz", "aux_.tar.gz"),
            ("COM1", "COM1_"),
            ("lpt9", "lpt9_"),
            ("COM\u00b9", "COM\u00b9_"),
            ("CONIN$", "CONIN$_"),
            ("CON .txt", "CON_.txt"),
        ],
    )
    def test_reserved_names(self, text, expected):
        assert library_component(text) == expected

    @pytest.mark.parametrize("text", ["CONSOLE", "COM0", "COM10", "LPT", "NULL", "icon"])
    def test_names_that_are_not_reserved(self, text):
        assert library_component(text) == text

    def test_reserved_with_a_suffix(self):
        assert library_component("CON", ".epub") == "CON_.epub"
        assert library_component("Dune", ".epub") == "Dune.epub"

    def test_fallback(self):
        assert library_component("...", ".epub", fallback="Untitled") == "Untitled.epub"

    def test_capped_at_100_characters(self):
        assert library_component("x" * 300) == "x" * 100

    def test_suffix_is_kept_within_the_cap(self):
        name = library_component("x" * 300, ".epub")
        assert name == "x" * 95 + ".epub"

    def test_capped_at_255_bytes(self):
        # 100 CJK characters are 300 UTF-8 bytes.
        name = library_component("\u6f22" * 100)
        assert name == "\u6f22" * 85
        assert len(name.encode("utf-8")) == 255

    def test_cut_leaves_no_trailing_dot_or_space(self):
        assert library_component("x" * 98 + ". tail") == "x" * 98

    def test_combining_marks_stay_with_their_letter(self):
        # "e" + three combining marks has no precomposed form, so it stays 4 code points.
        cluster = "e\u0301\u0302\u0303"
        name = library_component("x" * 98 + cluster)
        assert name == "x" * 98

    def test_zwj_sequences_are_not_split(self):
        technologist = "\U0001f469\u200d\U0001f4bb"  # woman + ZWJ + laptop
        name = library_component("x" * 98 + technologist)
        assert name == "x" * 98

    def test_flags_are_not_split(self):
        flag = "\U0001f1eb\U0001f1f7"
        assert library_component("x" * 99 + flag) == "x" * 99
        assert library_component("x" * 98 + flag) == "x" * 98 + flag

    def test_skin_tone_stays_with_its_emoji(self):
        thumbs = "\U0001f44d\U0001f3fd"
        assert library_component("x" * 99 + thumbs) == "x" * 99

    def test_custom_limit(self):
        assert library_component("abcdefgh", ".epub", max_chars=10) == "abcde.epub"


TOKENS = [
    "a",
    "Z",
    "7",
    " ",
    ".",
    "_",
    "-",
    "\t",
    "\n",
    "\x00",
    "\x1f",
    "\x7f",
    *'<>:"/\\|?*',
    "CON",
    "com1",
    "LPT\u00b9",
    "CONOUT$",
    "e\u0301",
    "\u00e9",
    "\u6f22\u5b57",
    "\U0001f469\u200d\U0001f4bb",
    "\U0001f1eb\U0001f1f7",
    "\U0001f44d\U0001f3fd",
    "\ufe0f",
    "\u3000",
    "\u00a0",
    "\ud800",
    "\u1100\u1161",  # conjoining Hangul jamo, composed by NFC
]


def random_texts(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choices(TOKENS, k=rng.randint(0, 140))) for _ in range(count)]


@pytest.mark.parametrize("suffix", ["", ".epub"])
def test_property_every_output_is_portable_idempotent_and_nfc(suffix):
    for text in random_texts(3000, seed=len(suffix)):
        name = library_component(text, suffix)
        assert_portable(name)
        assert name.endswith(suffix)
        assert library_component(name) == name, repr(text)


# --- render_template -------------------------------------------------------------------


def book(**fields) -> BookMetadata:
    return BookMetadata(**({"title": "Dune", "authors": ("Frank Herbert",)} | fields))


class TestRenderTemplate:
    def test_default_template(self):
        path = render_template("{author}/{title}", book(), suffix=".epub")
        assert path == PurePath("Frank Herbert", "Dune.epub")

    def test_every_field(self):
        metadata = book(year=1965, series="Dune Chronicles", authors=("F. Herbert", "B. Herbert"))
        path = render_template("{author}/{series}/{year} - {title}", metadata)
        assert path == PurePath("F. Herbert", "Dune Chronicles", "1965 - Dune")

    def test_unknown_author(self):
        assert render_template("{author}/{title}", book(authors=())) == PurePath(
            "Unknown Author", "Dune"
        )

    def test_missing_year_drops_its_brackets(self):
        assert render_template("{title} ({year})", book()) == PurePath("Dune")
        assert render_template("{title} [{year}]", book()) == PurePath("Dune")
        assert render_template("{title} ({year})", book(year=1965)) == PurePath("Dune (1965)")

    def test_real_brackets_in_a_title_are_kept(self):
        assert render_template("{title}", book(title="Notes ()")) == PurePath("Notes ()")

    def test_missing_series_drops_its_separator(self):
        assert render_template("{series} - {title}", book()) == PurePath("Dune")

    def test_missing_series_folder_is_dropped(self):
        assert render_template("{author}/{series}/{title}", book()) == PurePath(
            "Frank Herbert", "Dune"
        )

    def test_a_slash_in_a_value_never_makes_a_folder(self):
        path = render_template("{author}/{title}", book(title="Either/Or", authors=("A\\B",)))
        assert path == PurePath("A_B", "Either_Or")

    def test_backslash_separates_folders_in_the_template(self):
        assert render_template("{author}\\{title}", book()) == PurePath("Frank Herbert", "Dune")

    def test_always_relative_and_inside(self):
        assert render_template("/{title}", book()) == PurePath("Dune")
        assert render_template("../{title}", book()) == PurePath("Dune")
        assert render_template("{author}/../{title}", book()) == PurePath("Frank Herbert", "Dune")

    def test_every_part_is_portable(self):
        metadata = book(title="CON", authors=("AUX.",), series="Series: One?")
        path = render_template("{author}/{series}/{title}", metadata, suffix=".epub")
        # "AUX." loses its trailing dot, which leaves the reserved name AUX.
        assert path == PurePath("AUX_", "Series_ One_", "CON_.epub")
        for part in path.parts:
            assert_portable(part)

    def test_template_without_fields(self):
        assert render_template("Books/All", book(), suffix=".epub") == PurePath("Books", "All.epub")

    def test_empty_template(self):
        assert render_template("", book(), suffix=".epub") == PurePath("Untitled.epub")

    def test_text_is_nfc(self):
        path = render_template("{title}", book(title="Cafe\u0301"))
        assert str(path) == "Caf\u00e9"

    @pytest.mark.parametrize(
        "template",
        ["{isbn}", "{0}", "{}", "{title!r}", "{title:>10}", "{title.upper}", "{author", "}"],
    )
    def test_invalid_templates(self, template):
        with pytest.raises(ConfigError) as info:
            render_template(template, book())
        assert "[output] naming" in info.value.message

    def test_unknown_field_hint_lists_the_fields(self):
        with pytest.raises(ConfigError) as info:
            render_template("{isbn}", book())
        assert info.value.hint == "Use {author}, {series}, {title}, {year}."


class TestWindowsPathLimit:
    def base(self, length: int) -> Path:
        # A path of exactly ``length`` characters, like C:\Users\...\Library.
        root = Path("C:/") if sys.platform == "win32" else Path("/")
        return root / ("b" * (length - len(str(root))))

    def test_no_limit_without_max_path(self):
        metadata = book(title="t" * 100, authors=("a" * 100,))
        path = render_template("{author}/{title}", metadata, suffix=".epub", base=self.base(200))
        assert path == PurePath("a" * 100, "t" * 95 + ".epub")

    def test_short_paths_are_unchanged(self):
        path = render_template(
            "{author}/{title}", book(), suffix=".epub", base=self.base(50), max_path=259
        )
        assert path == PurePath("Frank Herbert", "Dune.epub")

    def test_long_paths_are_shortened_longest_part_first(self):
        metadata = book(title="t" * 100, authors=("a" * 30,))
        base = self.base(150)
        path = render_template(
            "{author}/{title}", metadata, suffix=".epub", base=base, max_path=WINDOWS_MAX_PATH
        )
        assert len(str(base / path)) <= WINDOWS_MAX_PATH
        author, title = path.parts
        assert author == "a" * 30
        assert title.endswith(".epub") and title.startswith("t")
        for part in path.parts:
            assert_portable(part)

    def test_every_part_can_shrink(self):
        metadata = book(title="t" * 100, authors=("a" * 100,))
        base = self.base(120)
        path = render_template(
            "{author}/{title}", metadata, suffix=".epub", base=base, max_path=WINDOWS_MAX_PATH
        )
        assert len(str(base / path)) <= WINDOWS_MAX_PATH
        assert all(len(part) >= 16 for part in path.parts)

    def test_rejected_when_it_cannot_fit(self):
        with pytest.raises(ConfigError, match="over Windows' limit of 260") as info:
            render_template(
                "{author}/{title}",
                book(title="t" * 100, authors=("a" * 100,)),
                suffix=".epub",
                base=self.base(240),
                max_path=WINDOWS_MAX_PATH,
            )
        assert "long path support" in (info.value.hint or "")


class TestLongPathSupport:
    @pytest.mark.windows_only
    def test_reads_the_registry(self):
        assert isinstance(windows_long_paths_enabled(), bool)

    @pytest.mark.windows_only
    def test_a_missing_value_means_off(self, monkeypatch):
        import winreg

        def missing(key, name):
            raise FileNotFoundError(name)

        monkeypatch.setattr(winreg, "QueryValueEx", missing)
        assert windows_long_paths_enabled() is False

    def test_always_on_off_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert windows_long_paths_enabled() is True
