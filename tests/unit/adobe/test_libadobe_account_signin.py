"""T1.6: ``libadobeAccount.signIn()`` error messages, with upstream fix ``bccca40`` in full.

The sign-in request and the network are replaced by fakes; each test gives the reply.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from book_loader.adobe._vendor import libadobe, libadobeAccount
from tests.fixtures.builders.adobe_auth import build_auth_folder

# Module globals that libadobe keeps between calls.
_STATE = ("FILE_DEVICEKEY", "FILE_DEVICEXML", "FILE_ACTIVATIONXML", "devkey_bytes", "pkcs12")


def error_reply(data: str) -> bytes:
    return f'<error xmlns="http://ns.adobe.com/adept" data="{data}"/>'.encode("utf-8")


@pytest.fixture
def sign_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Calls ``signIn`` with an Adobe ID; the server answers with the given reply."""
    for name in _STATE:
        monkeypatch.setattr(libadobe, name, getattr(libadobe, name, None), raising=False)
    auth = build_auth_folder(tmp_path / ".adobe", method="AdobeID")
    libadobe.update_account_path(str(auth.path))
    sent: list[str] = []
    monkeypatch.setattr(libadobeAccount, "buildSignInRequest", lambda *args: "<signIn/>")

    def run(reply: bytes) -> tuple[bool, str]:
        def send(document, url):
            sent.append(url)
            return reply

        monkeypatch.setattr(libadobeAccount, "sendRequestDocu", send)
        result = libadobeAccount.signIn("AdobeID", "reader@example.com", "password")
        assert sent[-1] == "https://auth.example.com/adept/SignInDirect"
        return result

    return run


def test_password_reset_required_after_bytebooks_migration(sign_in):
    ok, message = sign_in(error_reply("E_ADEPT_RESET_PW_REQUIRED http://adeactivate.adobe.com"))
    assert ok is False
    assert message == (
        "Server requires a password reset due to ByteBooks migration. "
        "See https://dtsbytebooks.com/transition-help"
    )


def test_reply_that_is_not_xml(sign_in):
    assert sign_in(b"<html>Service unavailable") == (
        False,
        "Invalid response to login request (please open a bug report)",
    )


def test_wrong_username_or_password(sign_in):
    reply = error_reply("E_AUTH_FAILED http://adeactivate.adobe.com CUS05051")
    assert sign_in(reply) == (False, "Invalid username or password!")


def test_login_failed_mentions_two_factor_authentication(sign_in):
    reply = error_reply("E_AUTH_FAILED http://adeactivate.adobe.com LOGIN_FAILED")
    assert sign_in(reply) == (
        False,
        "E_AUTH_FAILED/LOGIN_FAILED. If you have 2FA enabled, please disable that and try again.",
    )


def test_unknown_error_includes_the_reply(sign_in):
    reply = error_reply("E_SOMETHING_NEW http://adeactivate.adobe.com")
    ok, message = sign_in(reply)
    assert ok is False
    assert message == "Unknown Adobe error:" + str(reply)


def test_unexpected_root_element(sign_in):
    reply = b'<surprise xmlns="http://ns.adobe.com/adept"/>'
    assert sign_in(reply) == (False, "Invalid main tag {http://ns.adobe.com/adept}surprise")
