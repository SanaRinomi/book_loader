"""T2.5: ``infra/settings.py``, the layered lookup and the auth folder order."""

from __future__ import annotations

from pathlib import Path

import pytest

from book_loader.domain.conflicts import ConflictPolicy
from book_loader.domain.errors import ConfigError
from book_loader.infra.paths import OS, GlobalPaths, Host, legacy_windows_auth_dir
from book_loader.infra.settings import AUTH_DIR_ENV, AuthLocation, Settings, Source, _describe

KEY = "conflicts.policy"
ENV = "BOOK_LOADER_CONFLICTS"


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "user"  # tmp_home already owns tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def paths(home) -> GlobalPaths:
    return GlobalPaths.resolve(Host(OS.LINUX, {}, home))


@pytest.fixture
def library(tmp_path) -> Path:
    root = tmp_path / "Library"
    (root / ".book-loader").mkdir(parents=True)
    return root


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def policy_toml(value: str) -> str:
    return f'[conflicts]\npolicy = "{value}"\n'


def make(paths, library=None, flags=None, env=None, **kwargs) -> Settings:
    if library is not None:
        kwargs.setdefault("local_file", library / ".book-loader" / "local.toml")
        kwargs.setdefault("library_file", library / "library.toml")
    return Settings(paths, env=env or {}, flags=flags, **kwargs)


def lookup(settings: Settings):
    return settings.lookup(KEY, ConflictPolicy.ASK, flag="policy", env=ENV, convert=ConflictPolicy)


class TestPrecedence:
    @pytest.fixture
    def every_layer(self, paths, library):
        write(paths.config_file, policy_toml("overwrite"))
        write(library / "library.toml", policy_toml("skip"))
        write(library / ".book-loader" / "local.toml", policy_toml("rename"))

    def test_flag_wins(self, paths, library, every_layer):
        settings = make(paths, library, flags={"policy": "flag-value"}, env={ENV: "skip"})
        assert lookup(settings).value == "flag-value"
        assert lookup(settings).source is Source.FLAG
        assert lookup(settings).origin == "--policy"

    def test_environment_wins_over_files(self, paths, library, every_layer):
        setting = lookup(make(paths, library, env={ENV: "overwrite"}))
        assert (setting.value, setting.source, setting.origin) == (
            ConflictPolicy.OVERWRITE,
            Source.ENV,
            ENV,
        )

    def test_local_toml_wins_over_library_toml(self, paths, library, every_layer):
        setting = lookup(make(paths, library))
        assert setting.value is ConflictPolicy.RENAME
        assert setting.source is Source.LOCAL
        assert setting.origin == f"{library / '.book-loader' / 'local.toml'}: {KEY}"

    def test_library_toml_wins_over_global(self, paths, library, every_layer):
        (library / ".book-loader" / "local.toml").unlink()
        setting = lookup(make(paths, library))
        assert (setting.value, setting.source) == (ConflictPolicy.SKIP, Source.LIBRARY)
        assert setting.origin == f"{library / 'library.toml'}: {KEY}"

    def test_global_config(self, paths, library, every_layer):
        setting = lookup(make(paths))  # plain mode: no library layers
        assert (setting.value, setting.source) == (ConflictPolicy.OVERWRITE, Source.GLOBAL)
        assert setting.origin == f"{paths.config_file}: {KEY}"

    def test_default(self, paths):
        setting = lookup(make(paths))
        assert (setting.value, setting.source, setting.origin) == (
            ConflictPolicy.ASK,
            Source.DEFAULT,
            None,
        )

    def test_library_files_that_do_not_exist_are_empty(self, paths, library):
        write(paths.config_file, policy_toml("skip"))
        assert lookup(make(paths, library)).source is Source.GLOBAL


class TestFlagsAndEnvironment:
    def test_a_flag_that_was_not_given_is_skipped(self, paths):
        assert lookup(make(paths, flags={"policy": None})).source is Source.DEFAULT

    def test_a_false_flag_is_a_value(self, paths):
        setting = make(paths, flags={"to_pdf": False}).lookup("output.to_pdf", True, flag="to_pdf")
        assert (setting.value, setting.source) == (False, Source.FLAG)

    def test_flags_are_not_converted(self, paths):
        setting = make(paths, flags={"policy": ConflictPolicy.SKIP}).lookup(
            KEY, ConflictPolicy.ASK, flag="policy", convert=lambda raw: 1 / 0
        )
        assert setting.value is ConflictPolicy.SKIP

    def test_flag_names_use_dashes(self, paths):
        setting = make(paths, flags={"keep_epub": True}).lookup("x", False, flag="keep_epub")
        assert setting.origin == "--keep-epub"

    def test_an_empty_variable_is_unset(self, paths):
        assert lookup(make(paths, env={ENV: ""})).source is Source.DEFAULT

    def test_a_setting_without_flag_or_variable_ignores_them(self, paths):
        settings = make(paths, flags={"policy": "x"}, env={ENV: "skip"})
        assert settings.lookup(KEY, ConflictPolicy.ASK).source is Source.DEFAULT


class TestTomlValues:
    def test_nested_keys_and_types(self, paths):
        write(paths.config_file, "[logs]\nkeep_days = 0\n[kobo]\nextra_macs = ['AA:BB']\n")
        settings = make(paths)
        assert settings.lookup("logs.keep_days", 30).value == 0
        assert settings.lookup("kobo.extra_macs", []).value == ["AA:BB"]
        assert settings.lookup("kobo.missing", "d").source is Source.DEFAULT
        assert settings.lookup("other.missing", "d").source is Source.DEFAULT

    def test_top_level_key(self, paths):
        write(paths.config_file, 'default_library = "D:/Books"\n')
        assert make(paths).lookup("default_library", None).value == "D:/Books"

    def test_key_under_a_value_that_is_not_a_table(self, paths):
        write(paths.config_file, 'conflicts = "skip"\n')
        with pytest.raises(ConfigError, match="'conflicts' must be a table") as info:
            lookup(make(paths))
        assert info.value.path == paths.config_file

    def test_the_file_is_read_once(self, paths):
        write(paths.config_file, policy_toml("skip"))
        settings = make(paths)
        assert lookup(settings).value is ConflictPolicy.SKIP
        write(paths.config_file, policy_toml("overwrite"))
        assert lookup(settings).value is ConflictPolicy.SKIP


class TestErrors:
    def test_invalid_toml_names_the_file_and_line(self, paths):
        write(paths.config_file, '[conflicts]\npolicy = "skip"\nbroken =\n')
        with pytest.raises(ConfigError) as info:
            lookup(make(paths))
        error = info.value
        assert error.path == paths.config_file
        assert error.line == 3
        assert error.message == f"{paths.config_file}, line 3: Invalid value"
        assert error.hint

    def test_line_from_the_message_on_python_3_11(self):
        # 3.11's TOMLDecodeError has no .msg or .lineno; 3.14's does.
        old_style = Exception("Invalid value (at line 4, column 2)")
        assert _describe(old_style) == ("Invalid value", 4)  # type: ignore[arg-type]

    def test_invalid_library_toml_names_that_file(self, paths, library):
        write(library / "library.toml", "[output\n")
        with pytest.raises(ConfigError) as info:
            lookup(make(paths, library))
        assert info.value.path == library / "library.toml"
        assert info.value.line == 1

    def test_a_valid_layer_above_hides_a_broken_one_below(self, paths, library):
        write(paths.config_file, "broken =\n")
        write(library / "library.toml", policy_toml("skip"))
        assert lookup(make(paths, library)).value is ConflictPolicy.SKIP

    def test_not_utf8(self, paths):
        paths.config_file.parent.mkdir(parents=True)
        paths.config_file.write_bytes(b'policy = "\xff"\n')
        with pytest.raises(ConfigError, match="UTF-8") as info:
            lookup(make(paths))
        assert info.value.path == paths.config_file

    def test_unreadable(self, paths):
        paths.config_file.mkdir(parents=True)  # a folder where the file should be
        with pytest.raises(ConfigError, match="Can't read the file") as info:
            lookup(make(paths))
        assert info.value.path == paths.config_file

    def test_invalid_value_in_a_file(self, paths):
        write(paths.config_file, policy_toml("sometimes"))
        with pytest.raises(ConfigError) as info:
            lookup(make(paths))
        assert info.value.path == paths.config_file
        assert f"Invalid value for {KEY} ('sometimes')" in info.value.message

    def test_invalid_value_in_the_environment(self, paths):
        with pytest.raises(ConfigError) as info:
            lookup(make(paths, env={ENV: "sometimes"}))
        assert info.value.path is None
        assert info.value.message.startswith(f"Invalid value for {ENV} ('sometimes')")

    def test_type_errors_are_config_errors(self, paths):
        write(paths.config_file, "[logs]\nkeep_days = [1]\n")
        with pytest.raises(ConfigError, match="logs.keep_days"):
            make(paths).lookup("logs.keep_days", 30, convert=int)

    def test_the_default_is_not_converted(self, paths):
        assert make(paths).lookup("logs.keep_days", "30", convert=int).value == "30"


class TestAuthLocation:
    def test_order(self, paths, tmp_path):
        flag, env, lib = tmp_path / "flag", tmp_path / "env", tmp_path / "lib"
        settings = make(
            paths, flags={"auth_dir": flag}, env={AUTH_DIR_ENV: str(env)}, library_auth_dir=lib
        )
        assert settings.auth_location() == AuthLocation(flag, Source.FLAG, "--auth-dir")

        settings = make(paths, env={AUTH_DIR_ENV: str(env)}, library_auth_dir=lib)
        assert settings.auth_location() == AuthLocation(env, Source.ENV, AUTH_DIR_ENV)

        settings = make(paths, library_auth_dir=lib)
        assert settings.auth_location() == AuthLocation(lib, Source.LIBRARY, "[auth]")

        settings = make(paths)
        assert settings.auth_location() == AuthLocation(paths.auth_dir, Source.DEFAULT)

    def test_flag_given_as_a_string(self, paths):
        location = make(paths, flags={"auth_dir": "D:/auth"}).auth_location()
        assert location.path == Path("D:/auth")

    def test_empty_variable_is_unset(self, paths):
        location = make(paths, env={AUTH_DIR_ENV: ""}).auth_location()
        assert location.source is Source.DEFAULT

    def test_old_windows_folder_is_flagged(self, home):
        legacy_windows_auth_dir(home).mkdir(parents=True)
        host = Host(OS.WINDOWS, {"LOCALAPPDATA": str(home / "AppData" / "Local")}, home)
        location = Settings.for_host(host).auth_location()
        assert location.path == legacy_windows_auth_dir(home)
        assert location.legacy

    def test_old_windows_folder_is_not_used_when_a_folder_is_given(self, home, tmp_path):
        legacy_windows_auth_dir(home).mkdir(parents=True)
        env = {"LOCALAPPDATA": str(home / "L"), AUTH_DIR_ENV: str(tmp_path / "mine")}
        location = Settings.for_host(Host(OS.WINDOWS, env, home)).auth_location()
        assert location.path == tmp_path / "mine"
        assert not location.legacy


def test_for_host(home):
    host = Host(OS.LINUX, {ENV: "skip"}, home)
    settings = Settings.for_host(host, flags={"auth_dir": None})
    assert settings.paths == GlobalPaths.resolve(host)
    assert lookup(settings).value is ConflictPolicy.SKIP
    assert settings.auth_location().path == home / ".config" / "book-loader" / ".adobe"


def test_settings_never_write_to_disk(tmp_path, paths, library):
    write(library / "library.toml", policy_toml("skip"))
    write(paths.config_file, "[logs]\nkeep_days = 7\n")
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}

    settings = make(paths, library)
    lookup(settings)
    settings.lookup("logs.keep_days", 30)
    settings.lookup("missing.key", 1)
    settings.auth_location()
    make(paths).auth_location()  # the global auth folder doesn't exist yet

    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == before
    assert not paths.auth_dir.exists()
