# Changelog

All notable changes to this project are recorded in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Python 3.11 or newer is now required. Installing on Python 3.10 is refused.
- The package description and keywords now mention Kobo.

### Developer
- Development tools (pytest, pytest-cov, black, ruff, pyright) moved from the `dev` extra to
  uv's `dev` dependency group. `uv sync` installs them; `pip install ".[dev]"` no longer works.

## [0.1.0]

- First release: Adobe ACSM fulfillment and DRM removal for EPUB and PDF, optional EPUB to PDF
  conversion, and Kobo Desktop KEPUB DRM removal on macOS.
