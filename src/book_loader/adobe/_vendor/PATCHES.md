# Vendored code: acsm-calibre-plugin

The five Python files here come from the ACSM Input plugin for Calibre. This file records
where they come from and every local change, so an upstream update can be diffed and the
changes reapplied. Update it, and `MANIFEST.sha256`, in the same commit as any change to a
vendored file.

`tests/unit/test_vendor_manifest.py` fails when a file here no longer matches
`MANIFEST.sha256`. After an intended change, run
`uv run python tests/tools/update_vendor_manifest.py`. The hashes ignore CRLF line endings,
and this file is not hashed.

## Upstream

| | |
| --- | --- |
| Repository | <https://github.com/Leseratte10/acsm-calibre-plugin>, folder `calibre-plugin/` |
| Pinned commit | `4eff3ee35ac760ccad063639401b303b7207aa9c` (2026-09-23, "Update .gitignore") |
| License | GPL-3.0-or-later (upstream `LICENSE` and README). Each file keeps its header, "Copyright (c) 2021-2023 Leseratte10". book-loader is GPL-3.0-or-later too |

How the pin was chosen (T1.2.1, 2026-09-29): each file as it was first imported into
book-loader (commit `f84918b`; `libpdf.py` at `d197e84`) was compared with every upstream
revision of that file. After the relative imports are undone, all five match upstream
revisions that exist together in commits `2f40289` (2024-09-17) to `fb288af` (2025-10-07).
No release tag contains them: the newest tag, `v0.0.16`, is from 2022. The only upstream
change to these files after `fb288af` was `bccca40` (2026-06-24) in `libadobeAccount.py`,
which `d197e84` had partly backported. T1.6 applied the rest (2026-09-29), so all five files
now match the upstream head of that day, `4eff3ee`, which became the pin.

There are no upstream changes to these files after the pin.

## Comparing with upstream

```sh
git clone https://github.com/Leseratte10/acsm-calibre-plugin
git -C acsm-calibre-plugin show 4eff3ee:calibre-plugin/libadobe.py \
  | diff -u --strip-trailing-cr - src/book_loader/adobe/_vendor/libadobe.py
```

Upstream's `calibre-plugin/__init__.py` isn't vendored. Its `download()` method is the
upstream counterpart of the download and license code added to `libadobeFulfill.py`.

## Changes that apply to several files

- **Relative imports.** Calibre loads the plugin files as top-level modules, so upstream writes
  `from libadobe import ...`. Here they form a package, so these become
  `from .libadobe import ...`, including the imports inside functions.
- **Imports that leave `_vendor/`.** Both are relative. They work because `adobe/_vendor/` is
  two package levels below `book_loader`, as `core/adobe/` was. If a target moves, only its
  import line changes.
  - `libadobe.py` and `libadobeFulfill.py` import `redact_url`, `redact_text` and
    `redact_header` from `...infra.redact` (from `...utils.redact` until T2.12 moved the
    module, 2026-09-29)
  - `libadobe.py` imports `keys`, `dump_certificate` and `dump_private_key` from `..pkcs12`,
    in place of `oscrypto` (see [libadobe.py](#libadobepy))
- **Whitespace.** Trailing spaces are removed on some lines next to local changes, for example
  `try: ` → `try:`. The diff shows these, but they change nothing.

## customRSA.py

Unchanged.

## libpdf.py

Unchanged. Added in `d197e84`, which writes the license into downloaded PDFs; see
`apply_license()` below.

## libadobeAccount.py

- Relative imports: the six `from libadobe import` lines at the top and the one in
  `encryptLoginCredentials()`. Nothing else differs from upstream.
- History: `d197e84` backported part of upstream `bccca40` into `signIn()`, and T1.6 made
  `signIn()` identical to upstream.

## libadobe.py

- Relative import: `from .customRSA import CustomRSA`.
- New imports: `time`, `http.client` and the redaction functions (see above).
- **`oscrypto` replaced by a shim** (T1.5; decided in T1.3, REFACTOR_PLAN decision 21 and
  §16). The two lines `from oscrypto import keys` and
  `from oscrypto.asymmetric import dump_certificate, dump_private_key` now import the same
  names from `..pkcs12` (`book_loader/adobe/pkcs12.py`, project code). Nothing else changed.
  - Why: the last `oscrypto` release, 1.3.0 from March 2022, fails to detect OpenSSL 3.x on
    some Linux systems and raises "Error detecting the version of libcrypto". `libadobe`
    imported `oscrypto.asymmetric` at module level, so there every Adobe command could fail,
    including `auth create`. No Linux system is available during the refactor (REFACTOR_PLAN
    §2.1), so this couldn't be checked (T0.5.3: "not checked"). The shim loads no native
    OpenSSL, so it can't happen, whatever the answer. The Linux check is deferred item D3.
  - Also found while testing it: on Windows, `oscrypto` derives PKCS#12 keys with its own
    pure-Python code, which gets a 3DES key wrong for about one salt in 465, and can then not
    decrypt the account's private key. The shim follows RFC 7292 and matches OpenSSL.
  - What `libadobe` uses, only to read the PKCS#12 bundle in `activation.xml` (signing itself
    uses `pycryptodome` through `customRSA`): `keys.parse_pkcs12(data, password)` in
    `get_cert_from_pkcs12()` and `sign_node()`, `dump_certificate(cert, encoding="der")` in
    `get_cert_from_pkcs12()`, and `dump_private_key(key, None, "der")` in `sign_node()`.
  - `oscrypto` is now a dev dependency only. `tests/unit/adobe/test_pkcs12.py` checks that the
    shim's results are byte-identical to it, and a `live` test does the same for this
    machine's real authorization.
  - When updating from upstream, replace its two `oscrypto` import lines again.
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
