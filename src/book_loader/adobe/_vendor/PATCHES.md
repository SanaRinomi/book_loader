# Vendored code: acsm-calibre-plugin

The five Python files here come from the ACSM Input plugin for Calibre. This file records
where they come from and every local change, so an upstream update can be diffed and the
changes reapplied. Update it, and `MANIFEST.sha256`, in the same commit as any change to a
vendored file.

## Upstream

| | |
| --- | --- |
| Repository | <https://github.com/Leseratte10/acsm-calibre-plugin>, folder `calibre-plugin/` |
| Pinned commit | `fb288afb3a83156f0e534eb1e0ec1cbc45a3e675` (2025-10-07, "Fix CI") |
| License | GPL-3.0-or-later (upstream `LICENSE` and README). Each file keeps its header, "Copyright (c) 2021-2023 Leseratte10". book-loader is GPL-3.0-or-later too |

How the pin was chosen (T1.2.1, 2026-09-29): each file as it was first imported into
book-loader (commit `f84918b`; `libpdf.py` at `d197e84`) was compared with every upstream
revision of that file. After the relative imports are undone, all five match upstream
revisions that exist together in commits `2f40289` (2024-09-17) to `fb288af` (2025-10-07).
The pin is the newest of these. No release tag contains them: the newest tag, `v0.0.16`, is
from 2022.

Upstream changes after the pin that are **not** here (checked up to `4eff3ee`, 2026-09-23):

- `bccca40` (2026-06-24), `libadobeAccount.signIn()`: a clearer error for accounts that need a
  password reset after the ByteBooks migration. Two of its three hunks are already backported
  (see [libadobeAccount.py](#libadobeaccountpy)). The missing hunk changes the message
  "Invalid response to login request" to "... (please open a bug report)" and removes a
  blank line. T1.6 in REFACTOR_ACTION_PLAN.md applies it.

## Comparing with upstream

```sh
git clone https://github.com/Leseratte10/acsm-calibre-plugin
git -C acsm-calibre-plugin show fb288af:calibre-plugin/libadobe.py \
  | diff -u --strip-trailing-cr - src/book_loader/adobe/_vendor/libadobe.py
```

Upstream's `calibre-plugin/__init__.py` isn't vendored. Its `download()` method is the
upstream counterpart of the download and license code added to `libadobeFulfill.py`.

## Changes that apply to several files

- **Relative imports.** Calibre loads the plugin files as top-level modules, so upstream writes
  `from libadobe import ...`. Here they form a package, so these become
  `from .libadobe import ...`, including the imports inside functions.
- **The only import that leaves `_vendor/`.** `libadobe.py` and `libadobeFulfill.py` import
  `redact_url`, `redact_text` and `redact_header` from `...utils.redact`. The relative path
  works because `adobe/_vendor/` is two package levels below `book_loader`, as `core/adobe/`
  was. If `utils.redact` moves, only this import line changes.
- **Whitespace.** Trailing spaces are removed on some lines next to local changes, for example
  `try: ` → `try:`. The diff shows these, but they change nothing.

## Planned change: replace `oscrypto` with a shim

Decided on 2026-09-29 (T1.3; REFACTOR_PLAN decision 21 and §16). T1.5 makes the change.
When it lands, move this section into [libadobe.py](#libadobepy), and add `..pkcs12` to the
imports that leave `_vendor/`.

- **Why.** `libadobe.py` imports `oscrypto`, whose last release, 1.3.0 from March 2022, has a
  known bug on some Linux systems: it fails to detect OpenSSL 3.x versions and raises "Error
  detecting the version of libcrypto". `libadobe` imports `oscrypto.asymmetric` at module
  level, so there every Adobe command could fail, including `auth create`. Windows and macOS
  use their own crypto libraries and aren't affected.
- **Why a shim rather than a workaround or a pinned `oscrypto` commit.** No Linux system is
  available during the refactor (REFACTOR_PLAN §2.1), so whether the bug affects book-loader
  can't be checked (T0.5.3 recorded "not checked"). A shim in pure Python loads no native
  OpenSSL, so the bug can't occur, whatever the answer. It is checked on Linux after the
  refactor (deferred item D3).
- **What `libadobe` uses.** The following three names, only to read the PKCS#12 bundle in
  `activation.xml`. Signing itself already uses `pycryptodome` through `customRSA`.
  - `keys.parse_pkcs12(data, password)`, in `get_cert_from_pkcs12()` and `sign_node()`
  - `dump_certificate(cert, encoding="der")`, in `get_cert_from_pkcs12()`
  - `dump_private_key(key, None, "der")`, in `sign_node()`
- **The vendored change.** Only the two import lines,
  `from oscrypto import keys` and `from oscrypto.asymmetric import dump_certificate, dump_private_key`,
  which will import the same names from `..pkcs12` (`book_loader/adobe/pkcs12.py`, project
  code). `oscrypto` then moves to the dev dependencies, where tests check that the shim's
  results are byte-identical to it.

## customRSA.py

Unchanged.

## libpdf.py

Unchanged. Added in `d197e84`, which writes the license into downloaded PDFs; see
`apply_license()` below.

## libadobeAccount.py

- Relative imports: the six `from libadobe import` lines at the top and the one in
  `encryptLoginCredentials()`.
- `signIn()`: partial backport of upstream `bccca40` (added in `d197e84`). It adds the
  `E_ADEPT_RESET_PW_REQUIRED` branch ("Server requires a password reset due to ByteBooks
  migration ...") and changes `"Unknown Adobe error:" + credentials` to
  `+ str(credentials)`, which fixes a `TypeError`. The rest of `bccca40` is not applied yet
  (see above). The backported `else:` line also lost upstream's trailing space.

## libadobe.py

- Relative import: `from .customRSA import CustomRSA`.
- New imports: `time`, `http.client` and the redaction functions (see above).
- `createDeviceKeyFile()`: after writing the device key, `os.chmod(FILE_DEVICEKEY, 0o600)`,
  so only the owner can read it (`ab502ae`). This includes a redundant local `import os`.
- New section "Verbose logging" (`d197e84`): the global `VERBOSE`, `set_verbose()`,
  `is_verbose()`, `vprint()` (prints `[verbose] ...` only when verbose), and
  `vprint_headers()` (prints headers through `redact_header`).
- New section "Status reporting" (`d197e84`): the global `_status_callback`,
  `set_status_callback()` and `report(event, **data)`. book-loader mutes this code's plain
  `print()` output unless verbose, and shows progress from these events instead (receiver:
  `utils/console.py`). Events: `fulfill`, `fulfilled`, `notify_start`, `notify`,
  `notify_result`, `download`, `redirect`, `progress`, `retry`, `start`, `info`, `warning`.
- New class `_LoggingRedirectHandler`: logs each redirect, with the URL redacted, and reports
  `redirect`.
- `sendHTTPRequest_DL2FILE(URL, outputfile, responseHeaders=None)`: the body is rewritten
  (`d197e84`). The function keeps upstream's TLS 1.2 context with certificate checks turned off.
  Upstream calls `urlopen` once, follows a `Location` header by recursion (and forgets to pass
  `outputfile`, which `ab502ae` fixed first), and writes the file. The local version:
  - uses an opener with `HTTPCookieProcessor`, so cookies are kept across redirects as ADE
    and libgourou do, plus `_LoggingRedirectHandler`, and a 60-second timeout
  - retries HTTP 429 and 503 up to 5 attempts in total. The wait starts at 5 s and
    doubles, and a numeric `Retry-After` header overrides it, capped at 300 s
  - on an `HTTPError`, saves the response body as `err.body` and sets `err.is_bot_block` when
    Google's "unusual traffic" / `google.com/sorry` page answers. That block is never retried,
    because retrying only extends it. `download_book()` in `libadobeFulfill.py` reads both
  - retries connection errors (DNS, refused, reset, TLS, timeout), waiting `attempt`
    seconds
  - when a transfer breaks after some data arrived, sends a `Range` request to resume
    (appending on HTTP 206), up to 20 times without counting as an attempt. HTTP 416 on a
    resume starts again from byte 0. It raises `IOError("Download incomplete: ...")` when
    it runs out of attempts
  - copies the response headers into `responseHeaders` when given
  - reports `download`, `progress` (`done`, `total`) and `retry`, and logs requests,
    responses and error bodies through `vprint`, all redacted
  - as upstream, raises other HTTP errors as `HTTPError`. It returns 200 on success, and
    otherwise the status code of any response that isn't 200 or 206
- `sendPOSTHTTPRequest()`: the "malformed URL" message prints the URL through `redact_url`.
  When verbose, it logs the POST URL, the response status and, on an `HTTPError`, the response
  headers.

## libadobeFulfill.py

- Relative imports, plus new imports of `is_verbose`, `vprint`, `report` from `.libadobe` and
  the redaction functions.
- `fulfill()`, `tryReturnBook()`, `performFulfillmentNotification()`: start from
  `verbose_logging = is_verbose()` instead of `False`. The Calibre preferences that upstream
  reads next don't exist outside Calibre, so `-v` now controls this output.
- Redacted logging: every `print()` of request or response XML, of the loan return and
  notification server URLs, and of server error messages goes through `redact_text` or
  `redact_url`.
- `fulfill()`: two checks taken from libgourou, run after parsing the ACSM. If the file is an
  `<error>` document from the distributor, it returns
  `(False, "The ACSM file is an error message from the distributor: ...")`. If
  `<expiration>` is in the past, it reports a `warning` and carries on.
- `report()` calls: in `fulfill()` for `fulfill` (with the URL), `fulfilled`, `notify_start`,
  and a `warning` when notification fails. In `performFulfillmentNotification()` for `info`
  (no `<notify>` tag), `notify` (per server) and `notify_result` (`ok=True/False`).
- New functions at the end of the file. The first import (`f84918b`) added a `download()`,
  and `d197e84` split it into parse, download and apply steps, so that a download blocked
  after fulfillment can be finished by hand from a saved license:
  - `parse_fulfillment(replyData)` returns a JSON-serializable dict: `download_url` (`None`
    when the reply has no `<src>`), `rights_xml`, `book_name` (the title, keeping only letters,
    digits, spaces, `-` and `_`, or `"Book"`), `resource` and `format`. It raises
    `RuntimeError` when the reply has no `resourceItemInfo` or license token.
  - `download(replyData, output_dir)`: a wrapper, `download_book(parse_fulfillment(...))`.
  - `download_book(info, output_dir)` downloads to `<book_name>.tmp` in `output_dir`, then calls
    `apply_license()`. It raises `RuntimeError` instead of returning `None`. With no URL it
    explains that ACSMs with download type 'auth' are unsupported. On HTTP 429 it saves the
    redacted body as `download_error_429.html` and gives a separate message for Google's IP
    block.
  - `apply_license(book_file, info, output_dir, content_type="", source="The file is")` works out
    the file type: `PK` → EPUB, `%PDF` (after leading whitespace) → PDF. An HTML page (by content
    type or leading tag) is saved as `download_error.html` and raised as an error, and
    `application/pdf` in the content type or the ACSM metadata → PDF. EPUB: moves the file to
    `<book_name>.epub` and adds `META-INF/rights.xml` unless it is already there. PDF: calls
    `libpdf.patch_drm_into_pdf()` with the license and `resource`, to add `ADEPT_LICENSE` to the
    `EBX_HANDLER`. If that fails, the unpatched file is kept and its path is given in the
    error. `book_file` is consumed.
  - `_save_error_body(output_dir, name, body)` writes a redacted copy of an error response and
    returns its path, or `None`.
  - Some comments in these functions are in Chinese, from the first import.
