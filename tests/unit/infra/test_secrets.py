"""T2.9: ``infra/secrets.py``, secret sources in order and the masked ``Secret`` type."""

from __future__ import annotations

import io
import logging
import pickle
from pathlib import Path

import pytest

from book_loader.domain.errors import OperationCancelled, SecretUnavailableError
from book_loader.infra.secrets import ResolvedSecret, Secret, SecretKind, resolve_secret
from tests.fakes.prompter import FakePrompter

PASSWORD = SecretKind.ADOBE_PASSWORD
NEW_PASSPHRASE = SecretKind.NEW_BACKUP_PASSPHRASE
PASSPHRASE = SecretKind.BACKUP_PASSPHRASE
PASSWORD_ENV = "BOOK_LOADER_ADOBE_PASSWORD"
PASSPHRASE_ENV = "BOOK_LOADER_BACKUP_PASSPHRASE"


class Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def secret_file(tmp_path) -> Path:
    path = tmp_path / "secret.txt"
    path.write_text("from-file\nsecond line is ignored\n", encoding="utf-8")
    return path


def resolve(kind=PASSWORD, **kwargs) -> ResolvedSecret:
    kwargs.setdefault("env", {})
    return resolve_secret(kind, **kwargs)


def value(result: ResolvedSecret) -> str:
    return result.secret.reveal()


# --- Order -----------------------------------------------------------------------------


class TestPasswordOrder:
    """--password-file, --password-stdin, the variable, --password, then a prompt."""

    def everything(self, secret_file, **overrides):
        kwargs = dict(
            file=secret_file,
            use_stdin=True,
            stdin=Pipe("from-stdin\n"),
            env={PASSWORD_ENV: "from-env"},
            legacy_value="from-flag",
            prompter=FakePrompter(secret=["from-prompt"]),
            interactive=True,
        )
        return resolve(PASSWORD, **(kwargs | overrides))

    def test_file_first(self, secret_file):
        result = self.everything(secret_file)
        assert (value(result), result.source, result.warnings) == (
            "from-file",
            "--password-file",
            (),
        )

    def test_then_stdin(self, secret_file):
        result = self.everything(secret_file, file=None)
        assert (value(result), result.source) == ("from-stdin", "--password-stdin")

    def test_then_the_variable(self, secret_file):
        result = self.everything(secret_file, file=None, use_stdin=False)
        assert (value(result), result.source) == ("from-env", PASSWORD_ENV)

    def test_then_the_old_option_with_a_warning(self, secret_file):
        result = self.everything(secret_file, file=None, use_stdin=False, env={})
        assert (value(result), result.source) == ("from-flag", "--password")
        assert len(result.warnings) == 1
        warning = result.warnings[0]
        assert "shell history" in warning and "--password-file" in warning
        assert "from-flag" not in warning

    def test_then_a_prompt(self, secret_file):
        prompter = FakePrompter(secret=["from-prompt"])
        result = self.everything(
            secret_file, file=None, use_stdin=False, env={}, legacy_value=None, prompter=prompter
        )
        assert (value(result), result.source) == ("from-prompt", "prompt")
        assert prompter.asked("secret") == [("Adobe ID password", False)]

    def test_an_empty_variable_is_unset(self):
        prompter = FakePrompter(secret=["typed"])
        result = resolve(env={PASSWORD_ENV: ""}, prompter=prompter, interactive=True)
        assert result.source == "prompt"


class TestPassphraseOrder:
    """--passphrase-file, --passphrase-stdin, the variable, then a prompt."""

    @pytest.mark.parametrize("kind", [NEW_PASSPHRASE, PASSPHRASE])
    def test_order(self, kind, secret_file):
        stdin = Pipe("from-stdin")
        env = {PASSPHRASE_ENV: "from-env"}
        prompter = FakePrompter(secret=["from-prompt"])
        common = dict(env=env, stdin=stdin, prompter=prompter, interactive=True)
        assert resolve(kind, file=secret_file, use_stdin=True, **common).source == (
            "--passphrase-file"
        )
        assert resolve(kind, use_stdin=True, **common).source == "--passphrase-stdin"
        assert resolve(kind, **common).source == PASSPHRASE_ENV
        assert resolve(kind, **(common | {"env": {}})).source == "prompt"

    def test_a_new_passphrase_is_asked_twice(self):
        prompter = FakePrompter(secret=["pass"])
        resolve(NEW_PASSPHRASE, prompter=prompter, interactive=True)
        assert prompter.asked("secret") == [("Backup passphrase", True)]

    def test_an_existing_passphrase_is_asked_once(self):
        prompter = FakePrompter(secret=["pass"])
        resolve(PASSPHRASE, prompter=prompter, interactive=True)
        assert prompter.asked("secret") == [("Backup passphrase", False)]

    def test_passphrases_have_no_old_option(self):
        with pytest.raises(ValueError, match="no legacy"):
            resolve(PASSPHRASE, legacy_value="x")

    def test_the_password_variable_is_not_a_passphrase(self):
        with pytest.raises(SecretUnavailableError):
            resolve(PASSPHRASE, env={PASSWORD_ENV: "x"})


# --- No source -------------------------------------------------------------------------


class TestNothingAvailable:
    def test_no_prompt_when_not_interactive(self):
        prompter = FakePrompter()  # fails the test if asked
        with pytest.raises(SecretUnavailableError) as info:
            resolve(PASSWORD, prompter=prompter, interactive=False)
        assert prompter.calls == []
        error = info.value
        assert "Adobe ID password is needed" in error.message
        hint = error.hint or ""
        assert "--password-file" in hint and "--password-stdin" in hint
        assert PASSWORD_ENV in hint
        assert "--no-encrypt" not in hint

    def test_interactive_without_a_prompter(self):
        with pytest.raises(SecretUnavailableError):
            resolve(PASSWORD, interactive=True)

    def test_a_new_passphrase_mentions_no_encrypt(self):
        with pytest.raises(SecretUnavailableError) as info:
            resolve(NEW_PASSPHRASE)
        assert "--no-encrypt" in (info.value.hint or "")
        assert "--passphrase-file" in (info.value.hint or "")

    def test_opening_a_backup_does_not_mention_no_encrypt(self):
        with pytest.raises(SecretUnavailableError) as info:
            resolve(PASSPHRASE)
        assert "--no-encrypt" not in (info.value.hint or "")

    def test_nothing_typed_at_the_prompt(self):
        with pytest.raises(SecretUnavailableError, match="No Adobe ID password was entered"):
            resolve(PASSWORD, prompter=FakePrompter(secret=[""]), interactive=True)

    def test_cancelling_the_prompt(self):
        prompter = FakePrompter(secret=[OperationCancelled()])
        with pytest.raises(OperationCancelled):
            resolve(PASSWORD, prompter=prompter, interactive=True)

    def test_empty_old_option(self):
        with pytest.raises(SecretUnavailableError, match="empty"):
            resolve(PASSWORD, legacy_value="")


# --- Files -----------------------------------------------------------------------------


class TestFile:
    @pytest.mark.parametrize(
        "content, expected",
        [
            (b"pass\n", "pass"),
            (b"pass", "pass"),
            (b"pass\r\nmore\r\n", "pass"),
            (b"\xef\xbb\xbfpass\r\n", "pass"),  # Notepad's byte order mark
            (b"  spaced pass  \n", "  spaced pass  "),
            ("päss\n".encode(), "päss"),
        ],
    )
    def test_first_line(self, tmp_path, content, expected):
        path = tmp_path / "secret"
        path.write_bytes(content)
        assert value(resolve(file=path)) == expected

    @pytest.mark.parametrize("content", [b"", b"\n", b"\r\nsecond\n"])
    def test_empty_first_line(self, tmp_path, content):
        path = tmp_path / "secret"
        path.write_bytes(content)
        with pytest.raises(SecretUnavailableError, match="first line"):
            resolve(file=path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(SecretUnavailableError, match="Can't read --password-file"):
            resolve(file=tmp_path / "missing")

    def test_not_utf8(self, tmp_path):
        path = tmp_path / "secret"
        path.write_bytes(b"\xff\xfe\x00bad\n")
        with pytest.raises(SecretUnavailableError, match="isn't UTF-8"):
            resolve(file=path)

    def test_error_messages_never_contain_the_file_contents(self, tmp_path):
        path = tmp_path / "secret"
        path.write_bytes(b"\r\nhunter2\n")
        with pytest.raises(SecretUnavailableError) as info:
            resolve(file=path)
        assert "hunter2" not in str(info.value) and "hunter2" not in (info.value.hint or "")


# --- stdin -----------------------------------------------------------------------------


class TestStdin:
    @pytest.mark.parametrize(
        "text, expected",
        [("pass\n", "pass"), ("pass", "pass"), ("pass\r\n", "pass"), ("pass\n\n  \n", "pass")],
    )
    def test_one_line(self, text, expected):
        assert value(resolve(use_stdin=True, stdin=Pipe(text))) == expected

    def test_other_input_on_stdin_is_an_error(self):
        with pytest.raises(SecretUnavailableError, match="only the Adobe ID password") as info:
            resolve(use_stdin=True, stdin=Pipe("hunter2\nbook.acsm\n"))
        assert "hunter2" not in info.value.message

    def test_combined_with_another_stdin_reader(self):
        with pytest.raises(SecretUnavailableError) as info:
            resolve(
                NEW_PASSPHRASE,
                use_stdin=True,
                stdin=Pipe("pass\n"),
                stdin_taken_by="--password-stdin",
            )
        assert "--passphrase-stdin can't be used together with --password-stdin" in (
            info.value.message
        )

    def test_empty(self):
        with pytest.raises(SecretUnavailableError, match="had no Adobe ID password"):
            resolve(use_stdin=True, stdin=Pipe(""))

    def test_a_terminal_is_refused(self):
        with pytest.raises(SecretUnavailableError, match="standard input is a terminal"):
            resolve(use_stdin=True, stdin=Terminal("typed\n"))

    def test_real_stdin_by_default(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", Pipe("piped\n"))
        assert value(resolve(use_stdin=True)) == "piped"


# --- Secret ----------------------------------------------------------------------------


class TestSecret:
    def test_str_and_repr_are_masked(self):
        secret = Secret("hunter2")
        assert str(secret) == "********"
        assert repr(secret) == "Secret('********')"
        assert "hunter2" not in f"{secret} {secret!r} {secret:>20}"
        assert "hunter2" not in repr([secret, {"password": secret}])

    def test_reveal(self):
        assert Secret("hunter2").reveal() == "hunter2"

    def test_masked_in_exceptions_and_logs(self, caplog):
        secret = Secret("hunter2")
        error = RuntimeError(f"login failed for {secret}")
        assert "hunter2" not in str(error)
        with caplog.at_level(logging.DEBUG):
            logging.getLogger("test").debug("using %s and %r", secret, secret)
        assert "hunter2" not in caplog.text

    def test_resolved_secret_repr_is_masked(self, secret_file):
        assert "from-file" not in repr(resolve(file=secret_file))

    def test_equality(self):
        assert Secret("a") == Secret("a")
        assert Secret("a") != Secret("b")
        assert Secret("a") != "a"

    def test_not_hashable_or_picklable(self):
        with pytest.raises(TypeError):
            hash(Secret("a"))
        with pytest.raises(TypeError, match="pickled"):
            pickle.dumps(Secret("a"))

    def test_no_attribute_holds_it_visibly(self):
        secret = Secret("hunter2")
        assert not hasattr(secret, "__dict__")

    def test_truthiness(self):
        assert Secret("a")
        assert not Secret("")
