# Changelog

All notable changes to this project are recorded in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Python 3.11 or newer is now required. Installing on Python 3.10 is refused.
- The package description and keywords now mention Kobo.
- `oscrypto` is no longer needed. book-loader reads the key and certificate in the Adobe
  authorization with its own PKCS#12 reader, written in pure Python. This avoids
  `oscrypto`'s "Error detecting the version of libcrypto" failure on some Linux systems with
  OpenSSL 3. The new reader's tests pass on Windows, macOS and Linux (Ubuntu with OpenSSL 3,
  where `oscrypto` fails that way), but it hasn't yet been used with a real Adobe account on
  Linux.
- When Adobe's reply to an Adobe ID sign-in can't be read, the error now says
  "Invalid response to login request (please open a bug report)", as the Calibre plugin does.

### Fixed
- On Windows, about one Adobe authorization in 465 could not sign requests, failing with
  "NTSTATUS error 0xC000003E". `oscrypto` derived the wrong key to decrypt the account's private
  key; the new PKCS#12 reader derives it as the standard (RFC 7292) and OpenSSL do.

### Developer
- Development tools (pytest, pytest-cov, black, ruff, pyright) moved from the `dev` extra to
  uv's `dev` dependency group. `uv sync` installs them; `pip install ".[dev]"` no longer works.
- The local check also type-checks with pyright (`[tool.pyright]` in `pyproject.toml`): the new
  packages and the tests, not the code that the refactor replaces or the vendored files.
  `lxml-stubs` joins the dev group.
- Vendored Adobe and DeDRM files are excluded from ruff and black. Project code was reformatted
  with black and had unused imports and placeholder-free f-strings fixed by ruff; behaviour is
  unchanged.
- A test suite (`uv run pytest`) and a local check (`uv run python tests/tools/check.py`) that
  runs ruff, black, pytest and coverage under Python 3.11 and 3.14.
- GitHub Actions runs the same check on Windows, macOS and Linux for every push to `main` and
  every pull request. The network tests stay a manual workflow.

## [0.1.0]

- First release: Adobe ACSM fulfillment and DRM removal for EPUB and PDF, optional EPUB to PDF
  conversion, and Kobo Desktop KEPUB DRM removal on macOS.
