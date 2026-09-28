# book-loader

> Snapshot of the original project structure, as of commit `d197e84` (2026-09-28). It describes the code as it was then; later changes are not reflected here.

book-loader is a Python command-line tool that turns ebooks you have bought into DRM-free EPUB or PDF files for personal backup. It handles two sources:

- **Adobe ACSM** (any platform): it registers its own Adobe device, uses an `.acsm` ticket to download the encrypted EPUB or PDF from the store's Adobe Content Server, then strips Adobe ADEPT DRM with that device's private key. It can also convert the EPUB to PDF.
- **Kobo Desktop** (macOS only): it reads the Kobo Desktop library database, derives each book's key from the Mac's network address and the Kobo user ID, and decrypts KEPUB files into standard EPUBs.

Version 0.1.0, Python 3.10+, GPL-3.0-or-later. It is meant only for books you legally own; the README sets out the legal terms.

## Usage

Install with `pip install -e .` (or `uv sync` for development). Installing puts a single `book-loader` command on the PATH; it comes from `book_loader.cli:cli` in `pyproject.toml`. Everything is driven from that command:

| Command | What it does |
| --- | --- |
| `book-loader process BOOK.acsm` | Fulfill an ACSM ticket and write a DRM-free EPUB or PDF. Options: `-o DIR`, `--to-pdf`, `--convert-engine python\|calibre`, `--auth-dir DIR`, `--downloaded-file FILE`, `--keep-encrypted`, `-v` |
| `book-loader auth create` | Create an Adobe authorization: `--anonymous` (default) or `--adobe-id --email EMAIL` |
| `book-loader auth info` | Show the authorization directory, status and type |
| `book-loader auth reset` | Delete the authorization, after an automatic backup |
| `book-loader auth backup` / `restore` | Save the authorization to a `.tar.gz` archive, or restore one (default folder `~/adobe-ade-auth-bk/`) |
| `book-loader kobo [--source DIR] list` | List the books in the Kobo Desktop library and their DRM status |
| `book-loader kobo [--source DIR] dedrm` | Decrypt Kobo books: pick them from a menu, or pass `--all`. Options: `-o DIR`, `--overwrite`, `--skip-existing` |
| `book-loader convert BOOK.epub` | Convert any EPUB to PDF (`-o FILE`, `--convert-engine`) |
| `book-loader info` | Show the version, authorization status and whether Calibre is installed |

The Adobe authorization lives in `~/.config/book-loader/.adobe/`. You can change that with `--auth-dir` or the `BOOK_LOADER_AUTH_DIR` environment variable. PDF conversion with the default `python` engine needs WeasyPrint's native libraries (Pango, cairo); no other command does.

### User Flows

There are seven user flows. Every Adobe flow goes through `BookLoader.process_acsm` in `core/workflow.py`, which reports progress as 3 steps, or 4 with `--to-pdf`.

#### Process an ACSM book

1. User runs `book-loader process book.acsm -o ~/Books/`.
2. `cli.process` builds a `Config` and a `BookLoader`, then calls `process_acsm`.
3. Step 1, Checking for Auth: `AdobeAccount.is_authorized()` looks for an authorization. If there is none, it creates an anonymous one on the spot.
4. Step 2, Downloading Encrypted File: `ACSMFulfiller.fulfill()` sends the ACSM to Adobe's fulfillment server. It then downloads the encrypted book into `<output>/.temp/` and embeds the license (`META-INF/rights.xml` for EPUB, `ADEPT_LICENSE` for PDF).
5. Step 3, Removing DRM: `DRMRemover.remove_drm()` decrypts the book with the device's RSA private key. The output goes to the output folder, and the encrypted copy and `.temp/` are deleted.
6. Step 4 (only with `--to-pdf`), Converting to PDF: `ConversionEngine` turns the EPUB into a PDF and deletes the EPUB. If conversion fails, it keeps the EPUB and prints a warning.
7. The CLI prints the path of the output file.

#### Recover from a blocked download (interactive)

1. Fulfillment succeeds, but the download fails. For example, Google Play Books answers with a CAPTCHA (HTTP 429).
2. `ACSMFulfiller` saves the parsed fulfillment to `<auth_dir>/pending/<acsm hash>.json`, tagged with a fingerprint of the key. It also writes a `<title> - download link.html` page, then raises `ManualDownloadRequired`.
3. The terminal is interactive, so `_ManualDownloadPrompt` shows the link and offers to open it in the browser.
4. User downloads the encrypted book in the browser, completing the check if asked.
5. User enters a path or file name at the prompt, or presses Enter to pick the newest EPUB or PDF in `~/Downloads`.
6. `fulfill_from_file()` adds the saved license to a copy of that file. The flow then continues at Removing DRM. If the file is wrong (for example, the check page), the prompt asks again.

#### Finish a blocked download later

1. The user quits at the prompt, or the terminal was not interactive. The CLI prints the exact command to finish with and exits with code 1.
2. User opens the saved link page in a browser and downloads the book.
3. User runs `book-loader process book.acsm --downloaded-file ~/Downloads/book.epub`.
4. `fulfill_from_file()` loads the saved license if its key fingerprint matches. Otherwise it fulfills the ACSM again.
5. The book is licensed, decrypted and written out as in the main flow. The pending JSON and the link page are deleted.

#### Authorize with an Adobe ID

1. User runs `book-loader auth create --adobe-id --email you@example.com`. The command prompts for the password unless `--password` is given.
2. If an authorization already exists, the command stops and asks the user to run `auth reset` first.
3. `AdobeAccount.authorize_adobe_id()` makes a device key, then runs four steps: create the device, create the user, sign in, activate the device.
4. If any step fails, the partial `activation.xml`, `device.xml` and `devicesalt` are deleted, so no half-made authorization is left behind.
5. `book-loader auth info` shows the type and the Adobe ID.

#### Back up and restore an authorization

1. User runs `book-loader auth backup`. It asks for a folder; the default is `~/adobe-ade-auth-bk/`.
2. The auth directory is written to `auth_<type>_<timestamp>.tar.gz`.
3. On another machine, or after a reset, user runs `book-loader auth restore`.
4. The command lists the backups, newest first. The user picks one by number, or passes `--file` to skip the list.
5. The current auth directory is replaced by the archive's contents, and the restored type is shown.

#### Decrypt Kobo books (macOS)

1. User runs `book-loader kobo list` to see every book in Kobo Desktop's library, marked Protected or DRM-free.
2. User runs `book-loader kobo dedrm -o ~/Books/` and picks books from a checkbox menu. `--all` takes every book instead.
3. `KoboLibrary` copies `Kobo.sqlite` to a temp file with WAL turned off, then reads the books and each file's encrypted page key.
4. `KoboLibrary.userkeys` builds candidate keys from every MAC address, the 4 fixed hash keys and every Kobo user ID.
5. For each book whose output file already exists, the command applies the conflict rule. `--overwrite` or `--skip-existing` decide without asking; otherwise the user is prompted.
6. `KoboDecryptor.decrypt_book()` tries each key until the decrypted files look valid, then writes `<title>.epub`.
7. The command prints a summary of successes, skips and failures. It exits with code 1 if any book failed.

#### Convert an EPUB to PDF

1. User runs `book-loader convert book.epub [-o out.pdf] [--convert-engine calibre]`.
2. `ConversionEngine` sends the book to WeasyPrint (`python`) or Calibre's `ebook-convert` (`calibre`).
3. The PDF is written next to the EPUB, or to `-o`.

## Contents

All code lives in the `book_loader` package under `src/`, in three layers. `cli.py` handles the terminal. `core/` does the work, with one folder per job: Adobe, DRM, Kobo and conversion. `utils/` holds shared helpers. Eleven modules are vendored and kept diffable against upstream. Five come from [acsm-calibre-plugin](https://github.com/Leseratte10/acsm-calibre-plugin) (`libadobe*`, `libpdf`, `customRSA`). Six come from the DeDRM tools (`ineptepub`, `ineptpdf`, `adobekey` and three helpers). Both groups are marked *vendored* below, and only the functions this project calls are listed for them. Paths are relative to `src/book_loader/`.

```
book_loader/                  repo root
├── pyproject.toml            package metadata, deps, `book-loader` entry point
├── main.py                   placeholder "Hello" script, not used by the CLI
├── README.md / README.zh-TW.md / TESTING.md / CLAUDE.md
└── src/book_loader/
    ├── cli.py                Click commands
    ├── core/
    │   ├── workflow.py       BookLoader: the ACSM pipeline
    │   ├── adobe/            authorization + fulfillment (+ vendored libadobe*)
    │   ├── drm/              DRMRemover + vendored DeDRM decryptors
    │   ├── kobo/             Kobo Desktop library + KEPUB decryptor
    │   └── conversion/       EPUB → PDF engines
    └── utils/                config, errors, console output, log redaction
```

### File: cli.py

`cli.py` defines every `book-loader` command with Click. It only parses options, prompts the user and prints results; the work is done in `core/`. Errors of type `BookLoaderError` are printed in red and the command exits with code 1.

#### Function: cli

The root Click group and the `book-loader` entry point. It also adds `--version`.

- Receives: nothing (Click parses the command line)
- Returns: nothing
- Depends: get_version

#### Function: process

The `process` command. It runs the full ACSM pipeline and prints the output path. It builds a `_ManualDownloadPrompt` only when stdin is a terminal, so a blocked download never hangs a script.

- Receives: `acsm_file`, `output`, `auth_dir`, `optimize`, `to_pdf`, `convert_engine`, `keep_encrypted`, `verbose`, `downloaded_file`
- Returns: nothing; exits 1 on failure, and on a stopped manual download prints the command to finish later
- Depends: core.workflow.BookLoader.process_acsm, utils.config.Config, utils.console.StepReporter, _ManualDownloadPrompt, _step_error

#### Function: _ManualDownloadPrompt.\_\_call\_\_

Called by the workflow when the automatic download is blocked. The first time, it shows the download link and offers to open it with `webbrowser`. Click's launcher is avoided because on Windows it splits the URL at `&`. It then loops, asking for the downloaded file.

- Receives: `blocked` (ManualDownloadRequired), `last_error` (the error from the previous file, or None)
- Returns: `Path` of the chosen file, or `None` if the user types `q`
- Depends: _show_link, _find, _newest_download (the newest EPUB or PDF in `~/Downloads` modified since the link was shown, minus 60 s)

#### Function: _step_error

Closes the status line and prints `[ERROR n/total] message`, so the user sees which step failed.

- Receives: `reporter` (StepReporter), `message`
- Returns: nothing
- Depends: StepReporter.fail

#### Function: create_auth

The `auth create` command. It refuses if an authorization already exists. For an Adobe ID it prompts for the email and password when they are not given as options.

- Receives: `auth_type` (`anonymous` or `adobe_id`), `email`, `password`
- Returns: nothing
- Depends: AdobeAccount.authorize_anonymous, AdobeAccount.authorize_adobe_id

#### Function: auth_info / info

`auth info` prints the authorization directory, status, type and Adobe ID email. `info` prints the version and authorization status, plus whether Calibre's `ebook-convert` is on the PATH.

- Receives: nothing
- Returns: nothing
- Depends: AdobeAccount.is_authorized, get_auth_type, get_adobe_id_email, CalibreConverter.is_available

#### Function: reset_auth

The `auth reset` command. It asks for confirmation and backs up the current authorization to `~/adobe-ade-auth-bk/`. Then it deletes the authorization files and any saved pending licenses.

- Receives: nothing (confirmation prompt)
- Returns: nothing
- Depends: backup_auth, AdobeAccount.reset

#### Function: backup_auth_cmd / restore_auth_cmd

The `auth backup` and `auth restore` commands. Backup names the archive `auth_<type>_<timestamp>.tar.gz`. Restore warns before overwriting an existing authorization and lists the backups to choose from, unless `--file` is given.

- Receives: `output` (backup); `backup_file`, `backup_dir` (restore)
- Returns: nothing
- Depends: backup_auth, list_backups, restore_auth

#### Function: backup_auth / list_backups / restore_auth

Plain helpers behind the backup commands. `backup_auth` tars the auth directory. `list_backups` returns the `*.tar.gz` files in a folder, newest first. `restore_auth` deletes the auth directory and extracts the archive in its place.

- Receives: `auth_dir` and `backup_path` / `backup_dir` / `backup_file`
- Returns: the backup `Path` (backup_auth), a `list[Path]` (list_backups), nothing (restore_auth)
- Depends: tarfile, shutil

#### Function: convert

The `convert` command. It converts an EPUB to PDF, writing next to the EPUB unless `-o` is given.

- Receives: `epub_file`, `output`, `convert_engine`
- Returns: nothing
- Depends: core.conversion.ConversionEngine

#### Function: kobo / kobo_list

`kobo` is a group that stores `--source` for its subcommands. `kobo list` prints a table of title, author and DRM status.

- Receives: `source` (group); Click context (list)
- Returns: nothing
- Depends: core.kobo.KoboLibrary

#### Function: kobo_dedrm

The `kobo dedrm` command. It selects books (all, or from a `questionary` checkbox menu), settles output-file conflicts, decrypts each book and prints a summary. `--overwrite` and `--skip-existing` cannot be used together.

- Receives: `all_books`, `output`, `overwrite`, `skip_existing`, Click context
- Returns: nothing; exits 1 if any book failed
- Depends: KoboLibrary, KoboDecryptor.decrypt_book, safe_filename, _resolve_file_conflict

#### Function: _resolve_file_conflict

Decides what to do when an output file already exists. A remembered "all" choice wins. A single book gets a yes/no prompt. A batch gets a menu: overwrite, skip, overwrite all, skip all, or cancel.

- Receives: `output_path`, `book_title`, `batch_mode`, `remembered_choice`
- Returns: a `ConflictAction`; raises `click.Abort` on cancel
- Depends: questionary, click.confirm

#### Function: get_version

Reads the installed package version, falling back to `0.1.0` when running from source.

- Receives: nothing
- Returns: version string
- Depends: importlib.metadata.version

### File: core/workflow.py

`workflow.py` holds `BookLoader`, which runs the Adobe pipeline: authorize, fulfill and download, remove DRM, and optionally convert. It owns one `AdobeAccount`, one `ACSMFulfiller` and one `DRMRemover`.

#### Function: BookLoader.process_acsm

Runs the whole pipeline and cleans up after it. It creates an anonymous authorization if none exists. Encrypted files go in `<output>/.temp/` and are deleted unless `--keep-encrypted` is set. A failed PDF conversion keeps the EPUB rather than failing the run. `--optimize` is accepted but only prints "not implemented".

- Receives: `acsm_path`, `output_dir`, `optimize`, `to_pdf`, `convert_engine`, `keep_encrypted`, `verbose`, `downloaded_file`, `reporter`, `manual_download` (callback)
- Returns: `Path` of the final EPUB or PDF
- Depends: AdobeAccount, ACSMFulfiller.fulfill / fulfill_from_file, DRMRemover.remove_drm, ConversionEngine, StepReporter, quiet

#### Function: BookLoader._finish_by_hand

The retry loop for a blocked download. It asks the `manual_download` callback for a file and tries it. On `ACSMFulfillmentError`, such as a CAPTCHA page saved instead of the book, it passes the error back and asks again.

- Receives: `acsm_path`, `blocked`, `manual_download`, `temp_dir`, `verbose`, `r`
- Returns: `Path` of the licensed encrypted book; re-raises `blocked` if the user gives up
- Depends: _use_downloaded_file, ACSMFulfiller.fulfill_from_file

#### Function: BookLoader.step_count

- Receives: `to_pdf`
- Returns: `4` with PDF conversion, else `3`
- Depends: nothing

### Folder: core/adobe

`core/adobe` talks to Adobe's servers. It creates the device authorization and turns an ACSM ticket into a licensed, still-encrypted book. `account.py` and `fulfill.py` are this project's own wrappers. The `lib*` files and `customRSA.py` are vendored and do the protocol work. `__init__.py` exports `AdobeAccount` and `ACSMFulfiller`.

### File: core/adobe/account.py

`account.py` holds `AdobeAccount`, which owns the authorization directory (`activation.xml`, `device.xml`, `devicesalt`, or ADE's `activation.dat`). `is_authorized()` accepts an `activation.dat`-only folder. However, `get_device_key()` and fulfillment both read `activation.xml`, so such a folder cannot fulfill or decrypt yet.

#### Function: AdobeAccount.is_authorized

- Receives: nothing
- Returns: `True` if the three standard files or `activation.dat` exist
- Depends: pathlib

#### Function: AdobeAccount.authorize_anonymous / authorize_adobe_id

Registers a new Adobe device. It creates a device key, then runs four steps: create the device, create the user, sign in, activate. If a step fails, the partial files are deleted, so `is_authorized()` never accepts a broken authorization.

- Receives: nothing (anonymous), or `email`, `password`
- Returns: nothing; raises `AuthorizationError` naming the step that failed
- Depends: libadobe.update_account_path, libadobe.createDeviceKeyFile, libadobeAccount.createDeviceFile / createUser / signIn / activateDevice

#### Function: AdobeAccount.get_auth_type / get_adobe_id_email

Read the `<adept:username method="...">` element of `activation.xml`.

- Receives: nothing
- Returns: `"anonymous"`, `"AdobeID"`, `"none"` or `"unknown"`; the email, or `""`
- Depends: lxml.etree

#### Function: AdobeAccount.get_device_key

Extracts the RSA private key that decrypts every book licensed to this device.

- Receives: nothing
- Returns: the key as DER `bytes` (base64-decoded `<adept:privateLicenseKey>`)
- Depends: lxml.etree, base64

#### Function: AdobeAccount.reset

Deletes all authorization files and the `pending/` folder. Saved licenses only work with the old key, so they go too.

- Receives: nothing
- Returns: nothing
- Depends: shutil.rmtree

### File: core/adobe/fulfill.py

`fulfill.py` holds `ACSMFulfiller`. It fulfills ACSM files and handles the recovery path when the download is blocked. Saved fulfillments live in `<auth_dir>/pending/` (mode 0700/0600), named by the first 16 hex characters of the ACSM's SHA-256.

#### Function: ACSMFulfiller.fulfill

Fulfills the ACSM, downloads the encrypted book and adds its license. If the download fails but a download URL exists, it saves the fulfillment and a link page instead of losing the transaction. Each ACSM can be fulfilled only once per authorization.

- Receives: `acsm_path`, `output_dir`, `verbose`, `status` (progress callback)
- Returns: `Path` of the encrypted `.epub` or `.pdf`
- Depends: _fulfill_acsm, libadobeFulfill.download_book, _save_pending, redact_text; raises `ManualDownloadRequired` or `ACSMFulfillmentError`

#### Function: ACSMFulfiller.fulfill_from_file

Licenses a book the user downloaded by hand. It uses the saved fulfillment when its key fingerprint matches the current authorization, and otherwise fulfills again. It works on a copy, so the user's file is never consumed.

- Receives: `acsm_path`, `downloaded_file`, `output_dir`, `verbose`, `status`
- Returns: `Path` of the licensed encrypted book
- Depends: _load_pending, _fulfill_acsm, libadobeFulfill.apply_license, _clear_pending

#### Function: ACSMFulfiller._fulfill_acsm

- Receives: `acsm_path`
- Returns: `dict` with `download_url`, `rights_xml`, `book_name`, `resource` and `format`
- Depends: libadobeFulfill.fulfill (`do_notify=True`), libadobeFulfill.parse_fulfillment

#### Function: ACSMFulfiller._save_pending / _load_pending / _clear_pending

Store, load and delete the saved fulfillment JSON and the `<title> - download link.html` page. `_load_pending` ignores a file saved under a different key.

- Receives: `acsm_path` (+ `output_dir`, `info` to save)
- Returns: link page `Path` (save); `info` dict or `None` (load); nothing (clear)
- Depends: hashlib, json, html

#### Function: ACSMFulfiller._adobe_session

A context manager that points `libadobe` at this account. It routes the library's events to the status callback and hides its `print` output unless verbose.

- Receives: `verbose`, `status`
- Returns: context manager
- Depends: libadobe.update_account_path / set_verbose / set_status_callback, utils.console.quiet

### File: core/adobe/libadobe.py

*Vendored.* Shared Adobe plumbing: paths to the auth files, the device key, the serial and fingerprint, signed and hashed XML nodes, and HTTP requests. This copy adds verbose logging with redaction and a `report(event, **data)` hook for progress events.

#### Function: sendHTTPRequest_DL2FILE

Downloads a URL to a file, reporting progress and redirects. It flags Google's HTTP 429 bot block on the error.

- Receives: `URL`, `outputfile`, `responseHeaders` (filled in)
- Returns: HTTP status code
- Depends: urllib, report, vprint

### File: core/adobe/libadobeAccount.py

*Vendored.* The Adobe activation protocol: `createDeviceFile`, `createUser`, `signIn` (anonymous or Adobe ID), `activateDevice`, plus Adobe ID conversion and key export helpers.

### File: core/adobe/libadobeFulfill.py

*Vendored, extended.* The Adobe Content Server fulfillment protocol, with loan records and notifications. Upstream's download step is split into `parse_fulfillment`, `download_book` and `apply_license`, so a fulfillment can be saved and finished by hand.

#### Function: fulfill

- Receives: `acsm_file`, `do_notify`
- Returns: `(True, reply XML)` or `(False, error message)`
- Depends: activation.xml's pkcs12, buildFulfillRequest, operatorAuth, performFulfillmentNotification

#### Function: apply_license

Sniffs the file type from its bytes and the Content-Type. It rejects HTML (a login or CAPTCHA page) with a clear error and saves that page, redacted. EPUBs get `META-INF/rights.xml`; PDFs are patched through `libpdf`.

- Receives: `book_file` (consumed), `info`, `output_dir`, `content_type`, `source`
- Returns: path of the licensed file named after the book
- Depends: libpdf.patch_drm_into_pdf, zipfile

### File: core/adobe/libpdf.py

*Vendored.* It appends an incremental PDF update that adds `ADEPT_LICENSE` and `EBX_BOOKID` to the `EBX_HANDLER` encrypt dictionary. Without this, `ineptpdf` has no key to decrypt with.

#### Function: patch_drm_into_pdf

- Receives: `filename_in`, `adept_license_string`, `filename_out`, `ebx_bookid`
- Returns: `None` on success, `False` on failure
- Depends: BackwardReader, update_ebx_with_keys, zlib/base64

### File: core/adobe/customRSA.py

*Vendored.* A small RSA helper (`CustomRSA.encrypt_for_adobe_signature`) that signs Adobe requests with PKCS#1 v1.5 padding, so the full `rsa` package's API is not needed here.

### Folder: core/drm

`core/drm` strips Adobe ADEPT DRM from licensed books. `remover.py` is this project's own code. The rest is vendored from the DeDRM tools (GPL-3.0, i♥cabbages, Apprentice Harper, noDRM et al.). `__init__.py` exports `DRMRemover`.

### File: core/drm/remover.py

`remover.py` holds `DRMRemover`, a single entry point that picks the decryptor by file extension.

#### Function: DRMRemover.remove_drm

- Receives: `encrypted_path`, `output_path`, `user_key` (DER RSA private key from `AdobeAccount.get_device_key`)
- Returns: nothing; writes `output_path`. Raises `DRMRemovalError` for an unknown extension or a failed decryption
- Depends: _decrypt_epub, _decrypt_pdf

#### Function: DRMRemover._decrypt_epub / _decrypt_pdf

Call the vendored `decryptBook`. Result code `0` means decrypted and `1` means already DRM-free, in which case the file is copied as is. Any other code is an error.

- Receives: `encrypted_path`, `output_path`, `user_key`
- Returns: nothing
- Depends: ineptepub.decryptBook, ineptpdf.decryptBook

### File: core/drm/ineptepub.py

*Vendored.* Decrypts ADEPT EPUBs.

#### Function: decryptBook

Decrypts the book key from `META-INF/rights.xml` with the user's RSA key. It then AES-CBC-decrypts every file listed in `encryption.xml` and writes a clean ZIP.

- Receives: `userkey`, `inpath`, `outpath`
- Returns: `0` success, `1` DRM-free, `2` failure
- Depends: Decryptor, removeHardening, zeroedzipinfo.ZeroedZipInfo, pycryptodome

### File: core/drm/ineptpdf.py

*Vendored.* A full PDF parser and serializer (about 2,500 lines) that rewrites an ADEPT PDF without its encryption.

#### Function: decryptBook

- Receives: `userkey`, `inpath`, `outpath`, `inept=True`
- Returns: `0` success, `2` failure
- Depends: PDFSerializer, PDFDocument, PDFParser

### File: core/drm/adobekey.py

*Vendored, not called by book-loader.* The DeDRM tool that extracts keys from an installed Adobe Digital Editions (Windows registry, or `activation.dat` on macOS).

### File: core/drm/utilities.py, argv_utils.py, zeroedzipinfo.py

*Vendored helpers.* `SafeUnbuffered` is a stdout wrapper that survives unencodable characters. `unicode_argv` reads Windows command-line arguments as Unicode. `ZeroedZipInfo` keeps a ZIP entry's `external_attr` at 0, working around [CPython issue 87713](https://github.com/python/cpython/issues/87713) so decrypted EPUBs match the original.

### Folder: core/kobo

`core/kobo` removes Kobo's KDRM from books synced by Kobo Desktop Edition on macOS. It needs no Adobe authorization. `__init__.py` exports `KoboLibrary`, `KoboBook` and `KoboDecryptor`.

### File: core/kobo/library.py

`library.py` reads the Kobo Desktop library. `KoboBook` is a dataclass holding `volumeid`, `title`, `author`, `filename` (under `kepub/`), `has_drm`, and `encrypted_files` (element ID → encrypted page key).

#### Function: KoboLibrary.\_\_init\_\_

Checks that the Kobo folder and `Kobo.sqlite` exist. It then copies the database to a temp file with bytes 18–19 set to `0x01 0x01`, which turns off WAL mode, so the live database is never locked or changed.

- Receives: `kobodir` (optional; default `~/Library/Application Support/Kobo/Kobo Desktop Edition`)
- Returns: a `KoboLibrary`; raises `KoboLibraryNotFoundError`
- Depends: sqlite3, tempfile

#### Function: KoboLibrary.books

A cached property. Books with rows in `content_keys` are DRM-protected; any other file in `kepub/` with a `content` row is DRM-free. The outer query uses `.fetchall()` and the inner queries use a second cursor. Reusing one cursor used to stop the loop after the first book (fixed in `a1d2b53`).

- Receives: nothing
- Returns: `list[KoboBook]` sorted by title
- Depends: sqlite3 cursors, base64

#### Function: KoboLibrary.userkeys

A cached property that builds every candidate key, since which MAC address and user ID Kobo used is not recorded. For each MAC, hash key and user ID it computes `deviceid = SHA256(hash_key + MAC)` and `userkey = SHA256(deviceid + UserID)`, and keeps the last 16 bytes.

- Receives: nothing
- Returns: `list[bytes]` (4 hash keys × MACs × user IDs)
- Depends: _get_mac_addrs (`/sbin/ifconfig -a`, macOS only), _get_user_ids, _compute_userkeys, KOBO_HASH_KEYS

#### Function: KoboLibrary.close

- Receives: nothing
- Returns: nothing; closes the connection and deletes the temp database
- Depends: sqlite3, os

### File: core/kobo/decryptor.py

`decryptor.py` turns a KEPUB into a standard EPUB.

#### Function: KoboDecryptor.decrypt_book

For DRM-free books it copies the file. Otherwise it tries each user key in turn. Layer 1: AES-ECB with the user key decrypts each file's page key. Layer 2: AES-ECB with the page key decrypts the content, then PKCS#7 padding is removed. A wrong key is caught by `_check_decrypted_content` (XHTML must start with printable ASCII; JPEG must start with `FF D8 FF`), and the partial output is deleted before the next key is tried.

- Receives: `book` (KoboBook), `userkeys`, `output_dir`
- Returns: `Path` of `<safe title>.epub`; raises `KoboDecryptionError` if no key works
- Depends: pycryptodome AES, zipfile, _unpad, _check_decrypted_content, safe_filename

#### Function: safe_filename

Replaces every character that is not a letter, digit, `_` or whitespace with `_`, and adds `.epub`. The CLI uses it to find output conflicts before decrypting.

- Receives: `title`
- Returns: file name string
- Depends: re

### Folder: core/conversion

`core/conversion` turns a DRM-free EPUB into a PDF with one of two engines: WeasyPrint in-process (`python`, the default) or Calibre's `ebook-convert` (`calibre`).

### File: core/conversion/\_\_init\_\_.py

Defines `ConversionEngine`, the one interface the CLI and workflow use.

#### Function: ConversionEngine.\_\_init\_\_ / convert_epub_to_pdf

The constructor picks the converter by name. `convert_epub_to_pdf` hands the paths to it.

- Receives: `engine` (`"python"` or `"calibre"`); then `epub_path`, `pdf_path`
- Returns: nothing; raises `ValueError` for an unknown engine
- Depends: EpubToPdfConverter, CalibreConverter

### File: core/conversion/epub_to_pdf.py

Holds `EpubToPdfConverter`, a pure-Python converter. WeasyPrint is imported inside `convert()` because it loads Pango and cairo when imported. A top-level import would break every other command on machines without those libraries.

#### Function: EpubToPdfConverter.convert

Reads the EPUB with ebooklib and joins every document's `<body>` into one HTML page, with a fixed A4 stylesheet and a title and author header. It writes the images to a temp folder and renders the page with WeasyPrint.

- Receives: `epub_path`, `pdf_path`
- Returns: nothing
- Depends: ebooklib, weasyprint.HTML, _extract_content, _clean_html, _extract_images

### File: core/conversion/calibre_wrapper.py

Holds `CalibreConverter`, a wrapper around Calibre's command-line converter.

#### Function: CalibreConverter.is_available / convert

`is_available` checks the PATH for `ebook-convert`. `convert` runs `ebook-convert <epub> <pdf>` with Calibre's default settings.

- Receives: nothing; then `epub_path`, `pdf_path`
- Returns: `bool`; then nothing. Raises `FileNotFoundError` if Calibre is missing, `CalledProcessError` if it fails
- Depends: shutil.which, subprocess

### File: core/conversion/epub_to_pdf_improved.py

Holds `ImprovedEpubToPdfConverter`, which is not wired in. It follows the EPUB spine order, keeps the book's own CSS, copies all resources and adds page numbers. Nothing imports it; `ConversionEngine` still uses `EpubToPdfConverter`.

### File: core/conversion/epub_optimizer.py

Empty. It is a placeholder for the `--optimize` option, which is not implemented yet.

### Folder: utils

`utils` holds the helpers shared across the package: where the auth files live, the error types, terminal status output, and log redaction.

### File: utils/config.py

Holds `Config`, which resolves the authorization directory.

#### Function: Config._get_auth_dir

Chooses, in order: the `--auth-dir` option, the `BOOK_LOADER_AUTH_DIR` environment variable, or `~/.config/book-loader/.adobe/`. The default folder is created and set to mode 0700 on every run, because it holds a private key.

- Receives: `custom_dir` (optional)
- Returns: `Path`
- Depends: os.getenv, pathlib

### File: utils/errors.py

The exception hierarchy. Everything derives from `BookLoaderError`, so the CLI can catch one base type:

```
BookLoaderError
├── AuthorizationError
├── ACSMFulfillmentError
│   └── ManualDownloadRequired   (reason, url, link_file, acsm_path)
├── DRMRemovalError
├── CalibreNotFoundError
├── WorkflowError
├── KoboLibraryNotFoundError
└── KoboDecryptionError
```

`ManualDownloadRequired` carries the download link. Its message tells the user how to finish with `--downloaded-file`.

### File: utils/console.py

Compact progress output. In normal mode each step is one line, extended as work goes on, for example `[2/3] Downloading: books.google.com... 45%`. In verbose mode every message gets its own line, because redacted protocol logs print in between.

#### Function: StepReporter.step / add / done / fail / note

The line API. `step` opens `[n/total] text...`, `add` extends the open line, `done` closes it with a result, `fail` closes it with `ERROR!`, and `note` prints a line of its own.

- Receives: step number and text
- Returns: nothing
- Depends: sys.stdout

#### Function: StepReporter.on_event

Turns `libadobe.report()` events into status text. The events are fulfill, notify, download, redirect, progress, retry, info and warning. Normal mode shows only the host name; verbose mode shows the redacted URL.

- Receives: `event`, `**data` (`url`, `message`, `done`, `total`, `ok`)
- Returns: nothing
- Depends: progress, redact_url

#### Function: StepReporter.progress

Redraws a percentage and MB count after the open line with `\r`. It works only in a real terminal, and only while the line fits the terminal width.

- Receives: `done`, `total` (bytes)
- Returns: nothing
- Depends: shutil.get_terminal_size

#### Function: quiet

A context manager that swallows `print()` output from vendored code unless verbose mode is on.

- Receives: `enabled`
- Returns: context manager
- Depends: contextlib.redirect_stdout

### File: utils/redact.py

Keeps logs useful for debugging network problems while hiding what identifies the user or the book. It keeps the scheme, host, generic path words, query parameter names, file extensions and header names. It masks tokens, signatures, IDs, titles, IPs, emails and cookies as `<hidden:N>`, `<uuid>`, `<email>` or `<ip>`.

#### Function: redact_url

- Receives: `url`
- Returns: the URL with its host, safe path words and safe query values kept, and everything else masked
- Depends: urlsplit, _redact_segment, SAFE_PATH_SEGMENTS, SAFE_QUERY_KEYS

#### Function: redact_text

Masks sensitive XML element text, UUIDs, emails, IPs, long numbers and long tokens in free text. URLs are redacted first and set aside, so the later passes don't re-mask what `redact_url` kept.

- Receives: `text`
- Returns: redacted string
- Depends: redact_url, regular expressions

#### Function: redact_header

- Receives: `name`, `value`
- Returns: fully masked for cookie and authorization headers, URL-redacted for `Location` and `Referer`, text-redacted otherwise
- Depends: redact_url, redact_text
