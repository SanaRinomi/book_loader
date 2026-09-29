# book-loader refactor action plan

> Companion to [REFACTOR_PLAN.md](REFACTOR_PLAN.md), which holds the design. This file turns that design into ordered tasks and sub-tasks, and lists the tests each one needs. References like "§7" point to sections of REFACTOR_PLAN.md; task IDs like **T3.2** point within this file.
>
> Written 2026-09-28 against commit `3f70d22`. Tick the boxes as work lands.
>
> Revised 2026-09-29 for the working constraints in REFACTOR_PLAN §2.1: only Windows is available, and there is no remote CI. Steps that need macOS, Linux or CI are marked **Deferred** with an item ID (D1, D2, …). Those items are listed in [Deferred until after the refactor](#deferred-until-after-the-refactor) at the end, as things that may need debugging or fixing once the refactor is done.

## How to use this plan

### Strategy: build alongside, then switch
- **The old code keeps running until Phase 6.** New packages (`domain/`, `infra/`, `adobe/`, `drm/`, `kobo/`, `epub/`, `conversion/`, `app/`, `cli/`) are built next to the existing `core/`, `utils/` and `cli.py`. The `book-loader` command keeps using the old code until Phase 6 switches the entry point and deletes it. So every phase leaves a working CLI, as REFACTOR_PLAN §15 requires.
- **Only moves reach into old code early.** Phase 1 moves the vendored files, and Phase 2 moves `redact.py`. Each time, a small re-export stays at the old path so old code keeps importing it.
- **Phase 0 tests pin current behaviour.** Where a test targets old internals, the later task that replaces the module ports the test, reusing the same fixtures and expected values. The expected values never change without a decision in REFACTOR_PLAN.md.

### Branches and PRs
- One branch and PR per phase: `refactor/p0-safety-net`, `refactor/p1-vendor`, … Large phases can split into one PR per task group, but every PR must pass the local check (T0.5.4).
- Commit messages say which tasks they complete (`T4.2.3`).

### Definition of done (applies to every task)
- [ ] The code is written in the target location (REFACTOR_PLAN §4) and follows the layering rule: nothing below `cli/` imports `click`, `rich`, `questionary` or `prompt_toolkit`.
- [ ] The tests listed for the task are written and pass locally: `uv run pytest`.
- [ ] `uv run ruff check src tests`, `uv run black --check src tests`, and (from Phase 2) `uv run pyright` pass.
- [ ] The local check (T0.5.4) passes on Windows with Python 3.11 and 3.14. CI on macOS and Linux is deferred (D1).
- [ ] Code that behaves differently per OS takes the platform, environment and command output as inputs, so its macOS and Linux behaviour is tested with fakes on Windows. Anything that still can't be tested on Windows is added to the deferred list.
- [ ] The help parity test (T0.3) still passes.
- [ ] User-visible changes have a `CHANGELOG.md` entry under "Unreleased".
- [ ] No vendored file changed except in tasks that say so, and then the vendor manifest (T1.4) is updated in the same commit.

### Test layout (created in T0.2)
```
tests/
├── conftest.py            shared fixtures: tmp HOME, isolated auth dir, fake Downloads, frozen clock
├── fixtures/
│   ├── builders/          code that builds test data at runtime
│   │   ├── adobe_auth.py  synthetic authorization folder: activation.xml, device.xml, devicesalt (T0.4)
│   │   ├── adept_epub.py  synthetic ADEPT-encrypted EPUB + matching RSA key (T0.4.6)
│   │   ├── tiny_pdf.py    minimal PDF with an EBX_HANDLER encrypt dictionary (T0.4.7)
│   │   ├── kobo.py        synthetic Kobo.sqlite + encrypted KEPUB (T0.4.3)
│   │   └── epub.py        plain EPUBs with chosen OPF, spine, CSS, images
│   ├── replies/           fulfillment reply XML: EPUB, PDF, returnable loan, error
│   ├── v0/                data written by 0.1.0: pending JSON, backup .tar.gz, link pages
│   ├── os_output/         captured ifconfig / getmac / Get-NetAdapter / /sys output
│   └── golden/            expected values: safe_filename table, key vectors, help option sets
├── tools/                 check.py (T0.5.4): the local check; dump_help.py (T0.3), make_fixtures.py (T0.4): regenerate golden, v0 and OS output files
├── fakes/                 FakeFulfiller, FakeDecryptor, FakeConverter, FakePrompter, RecordingReporter, fake libadobe
├── unit/                  one folder per package (domain, infra, adobe, drm, kobo, epub, conversion, library)
├── integration/           services with fakes at the network edge
├── cli/                   CliRunner tests of commands
├── characterization/      Phase 0 tests of the old code; removed or ported in Phase 6
└── live/                  real servers / real installs; skipped by default
```

**Markers:**
- `network`: talks to real Adobe servers.
- `live`: needs a real install or a private file, such as Kobo Desktop or a real ADEPT PDF.
- `weasyprint`: needs WeasyPrint's native libraries.
- `windows_only` / `macos_only` / `posix_only`.

The default run is `-m "not network and not live"`. `weasyprint` tests skip themselves when the libraries are missing.

During the refactor only Windows is available, so `macos_only` and `posix_only` tests are written but always skip. Their first real run is deferred (D2).

### Size key
**S** = under half a day, **M** = one to two days, **L** = three days or more. These are relative, meant for ordering work, not deadlines.

---

## Phase 0: Safety net
Goal: pin today's behaviour in tests and set up tooling and a local check before any code moves. Only packaging and tooling change here; no production behaviour does.

### T0.1 Tooling and packaging (S)
- [x] **T0.1.1** In `pyproject.toml`:
  - set `requires-python = ">=3.11"`
  - update the classifiers: drop 3.10, add 3.14
  - set black and ruff `target-version` to py311
  - fix the description and keywords to mention Kobo
- [x] **T0.1.2** Move the dev tools to `[dependency-groups] dev`: pytest, pytest-cov, black, ruff, and pyright (pyright is used from Phase 2). Remove `[project.optional-dependencies] dev`.
- [x] **T0.1.3** Add `[tool.pytest.ini_options]` with the markers above, `addopts = -m "not network and not live"`, and `testpaths = ["tests"]`.
- [x] **T0.1.4** Run `uv lock` and commit `uv.lock`.
- [x] **T0.1.5** Create `CHANGELOG.md` with an "Unreleased" section.
- [x] **T0.1.6** Added 2026-09-29. Exclude the vendored files from ruff and black in `pyproject.toml` (`force-exclude`, so they stay excluded when a file is passed directly), then run one formatting pass on project code only: `ruff check --fix` (safe fixes) and `black`. Vendored files are never formatted or linted.
- **Tests:**
  - `uv run ruff check src tests` and `uv run black --check src tests` pass, and no vendored file changed.
  - `--help` output of every command is identical before and after the formatting pass.
  - `uv sync` works on a clean checkout.
  - `uv run book-loader --help` and `uv run python -m book_loader.cli --help` work.
  - On Python 3.10, `pip install .` is refused (checked by hand once).

### T0.2 Test scaffolding (S)
- [x] **T0.2.1** Create the `tests/` tree above.
- [x] **T0.2.2** Write the `conftest.py` fixtures:
  - `tmp_home`: points `HOME`, `USERPROFILE` and `LOCALAPPDATA` at a temp folder.
  - `auth_dir`: an empty auth folder, with `BOOK_LOADER_AUTH_DIR` set to it.
  - `downloads_dir`
  - `frozen_time`
  - `cli_runner`: a Click `CliRunner` with stderr kept separate from stdout.
- **Tests:** a smoke test that every fixture works on all three OSes. Runs on Windows now; macOS and Linux are deferred (D2).

### T0.3 Help parity snapshot (S)
- [x] **T0.3.1** Write `tests/tools/dump_help.py`. It walks the Click command tree and saves, for every command and group, the set of option names (for example `-o/--output`, `--to-pdf`, `-v/--verbose`) and arguments, into `tests/fixtures/golden/help_options.json`.
- [x] **T0.3.2** Write `tests/cli/test_help_parity.py`. For every command in the golden file, the command still exists and still has every option. New commands and options are allowed. The test compares sets of options, not help text, so rich-click's formatting in Phase 6 doesn't break it.
- [x] **T0.3.3** Include `process --optimize/--no-optimize`, `process -v`, `process --auth-dir` and `auth reset --yes`.
- **Tests:** the parity test passes against today's CLI. Remove one option by hand to check that the test catches it, then revert.

### T0.4 Characterization tests of the current code (L)
These tests pin what 0.1.0 does today. They live in `tests/characterization/`, except for builders and golden files, which later phases reuse.

- [x] **T0.4.1 Redaction** (`utils/redact.py`):
  - a table of URLs, header pairs and text blocks (XML with UUIDs, emails, IPs, tokens), each with the expected output
  - saved as `golden/redact.json`
- [x] **T0.4.2 `safe_filename`** (`core/kobo/decryptor.py`):
  - at least 40 titles: ASCII, punctuation, CJK, accented letters, emoji, slashes, colons, very long titles, empty
  - their outputs saved as `golden/safe_filename.json`
  - this file is the contract for plain-mode Kobo names (REFACTOR_PLAN §5.11)
- [x] **T0.4.3 Kobo builder and tests:**
  - **Builder** `builders/kobo.py`:
    - creates `Kobo.sqlite` with the tables and columns the current queries use: `content(ContentID, Title, Attribution)`, `content_keys(volumeid, elementid, elementkey)`, `user(UserID)`
    - creates a `kepub/<volumeid>` ZIP whose entries are AES-ECB encrypted with page keys, each page key wrapped with a user key derived from a chosen fake MAC, user ID and hash key
    - offers a WAL-mode variant and a DRM-free book
  - **Tests** on `KoboLibrary` and `KoboDecryptor`, with `_get_mac_addrs` monkeypatched to return the fake MAC:
    - books are listed, and the book after the first is still found (the old cursor bug)
    - `userkeys` includes the right key
    - decryption output matches the plaintext
    - DRM-free books are copied as they are
    - a wrong-key-only setup raises `KoboDecryptionError`
    - a missing folder raises `KoboLibraryNotFoundError`
    - added 2026-09-29, as `xfail(strict=True)` for known bugs that T4.2 must fix: a change still in the WAL file is listed, and an XHTML file starting with a UTF-8 BOM is decrypted with the right key
  - **Key vectors:** save the derived key for the fixed inputs as `golden/kobo_keys.json`.
  - **OS output fixtures:** save real `getmac` and `Get-NetAdapter` output from this Windows machine in `fixtures/os_output/`. The macOS `ifconfig -a` and Linux `/sys/class/net` fixtures are written by hand from documented examples, and replaced with real captures later (deferred D6).
- [x] **T0.4.4 Backup helpers** (`cli.backup_auth`, `list_backups`, `restore_auth`):
  - a round trip into a folder with the same name
  - backups listed newest first
  - the known bug as an `xfail(strict=True)` test: restoring into a folder with a different name puts the files in the wrong place. T3.8 must make it pass.
  - save one archive to `fixtures/v0/auth_anonymous_20260101_000000.tar.gz`, and one named in `reset`'s style (`auth_backup_*`)
- [x] **T0.4.5 Pending store** (`ACSMFulfiller._save_pending`, `_load_pending`, `_clear_pending`) with a stub account whose `get_device_key()` returns fixed bytes:
  - JSON fields and file mode (`0600` on POSIX; `posix_only`, deferred D4)
  - the link page is written into the folder passed in
  - a mismatched fingerprint is ignored
  - clearing deletes both files
  - save a v0 record and link page to `fixtures/v0/`
- [x] **T0.4.6 Synthetic ADEPT EPUB builder** (`builders/adept_epub.py`):
  - makes an RSA key pair (pycryptodome), whose private key in DER form is the "device key"
  - makes a random 16-byte AES book key
  - adds `META-INF/rights.xml` containing `<adept:encryptedKey>` (the book key encrypted with RSA PKCS#1 v1.5, base64) and no `keyType`, so no hardening applies
  - adds `META-INF/encryption.xml` listing the encrypted entries
  - compresses each listed entry with raw deflate, then encrypts it with AES-CBC and a random IV at the front
  - **Test:** the vendored `ineptepub.decryptBook(device_key, in, out)` returns `0` and the output's entries equal the plaintext; a plain EPUB returns `1`.
- [x] **T0.4.7 Tiny PDF builder and `libpdf` test:**
  - a hand-written PDF with an `/Encrypt` dictionary whose `/Filter` is `/EBX_HANDLER`
  - **Test:** `libpdf.patch_drm_into_pdf` returns success and appends an incremental update containing `ADEPT_LICENSE` and `EBX_BOOKID`.
- [x] **T0.4.8 Workflow characterization** (`BookLoader.process_acsm` with `ACSMFulfiller` and `DRMRemover` monkeypatched):
  - 3 steps, or 4 with `--to-pdf`
  - encrypted file and `.temp/` removed on success
  - `--keep-encrypted` keeps them
  - a failed conversion keeps the EPUB and warns
  - `--to-pdf` on a PDF is skipped
  - the **data-loss bug** as `xfail(strict=True)`: an existing `Title.epub` in the output folder is overwritten and then deleted by `--to-pdf`. T5.1 must make it pass.
  - the manual-download loop: wrong file, then right file
  - with `manual_download=None`, `ManualDownloadRequired` is raised
- [x] **T0.4.9 CLI characterization** (`CliRunner`):
  - `info` exits 0 and shows "Not authorized" on an empty auth folder
  - `auth info` does the same
  - `auth create` when already authorized prints the warning and exits 0
  - `kobo --source <missing> list` exits 1
  - `kobo dedrm --overwrite --skip-existing` exits 1 with the message
  - `convert` on a missing file exits 2 (Click's check)
  - `process` without a terminal prints the "finish later" command and exits 1 when downloading is blocked (fulfiller monkeypatched)
- [x] **T0.4.10 Fulfillment reply fixtures** in `fixtures/replies/`:
  - synthetic XML shaped like the structures `libadobeFulfill.parse_fulfillment` and `updateLoanReturnData` read, for an EPUB, a PDF, and a returnable loan with a `permissions/display/until` date
  - **Test:** `parse_fulfillment` gives the expected `book_name`, format and URL for each, which checks the fixtures themselves.

### T0.5 Local check and continuous integration (S)
No remote CI is available during the refactor (REFACTOR_PLAN §2.1). The local check in T0.5.4 is the gate for every task; the workflows are written now so they can be switched on later.
- [x] **T0.5.1** `.github/workflows/ci.yml`, written but not run until after the refactor (deferred D1):
  - runs on Windows, macOS and Linux, each with Python 3.11 and 3.14
  - steps: `uv sync`, ruff, `black --check`, then `pytest` with coverage
- [x] **T0.5.2** A separate manual workflow runs `-m network` with secrets, for later use. Written but not run (deferred D1).
- [x] **T0.5.3** Check whether `import book_loader.core.adobe.libadobe` works on Linux, because of the `oscrypto`/OpenSSL 3 risk (REFACTOR_PLAN §16). **Deferred (D3):** no Linux system is available, so record the result as "not checked".
  - Result, 2026-09-29: **not checked** (no Linux system). The T1.3 decision to replace `oscrypto` with a shim makes this check moot for the new code; D3 checks the shim on Linux instead.
- [x] **T0.5.4** A local check script, `uv run python tests/tools/check.py`. It runs `uv sync --locked`, ruff, `black --check`, pyright (from Phase 2) and `pytest` with coverage, first under Python 3.11 and then under 3.14, and stops at the first failure. The CI workflow in T0.5.1 runs the same script, so both stay in step.
- **Tests:** the local check passes on Windows under both Python versions. A deliberate ruff error makes it fail, and is then reverted.

**Phase 0 exit:** the local check passes on Windows under Python 3.11 and 3.14, and the CI workflows are written but not run. The golden files (`redact`, `safe_filename`, `kobo_keys`, `help_options`) and the v0 fixtures are committed. The four strict-xfail tests (restore location, `--to-pdf` data loss, Kobo WAL, Kobo BOM) are present. All tests block network access except to this machine unless marked `network` or `live`.

---

## Phase 1: Move the vendored code
Goal: vendored files move to their final home with only import changes, and are protected against accidental edits.

### T1.1 Create the target packages (S)
- [x] **T1.1.1** Create `src/book_loader/adobe/__init__.py`, `adobe/_vendor/__init__.py`, `drm/__init__.py` and `drm/_vendor/__init__.py`.
- [x] **T1.1.2** Move the files with `git mv`:
  - `core/adobe/{libadobe,libadobeAccount,libadobeFulfill,libpdf,customRSA}.py` → `adobe/_vendor/`
  - `core/drm/{ineptepub,ineptpdf,adobekey,utilities,argv_utils,zeroedzipinfo}.py` → `drm/_vendor/`
  - in `pyproject.toml`, remove the old-path entries from the ruff and black exclusions (T0.1.6); the `_vendor` patterns already cover the new location
- [x] **T1.1.3** Check the vendored imports:
  - relative imports among the vendored files still work because the files move together
  - `from ...utils.redact import …` still reaches `book_loader.utils` from the new depth (`adobe/_vendor` is also two levels below `book_loader`)
  - fix anything that doesn't resolve
- [x] **T1.1.4** Update the old project code to import from the new paths: `core/adobe/account.py`, `fulfill.py`, `__init__.py`, and `core/drm/remover.py`. The old `core/adobe` and `core/drm` folders keep only project code.
- **Tests:**
  - the whole Phase 0 suite passes unchanged
  - `git diff -M --stat` shows the vendored files as renames (at least 95% similar) with only import lines changed

### T1.2 PATCHES.md (M)
- [ ] **T1.2.1** Find the upstream versions. Diff each file against acsm-calibre-plugin releases (for the five `adobe` files) and DeDRM/noDRM releases (for the six `drm` files), and choose the closest commit.
- [ ] **T1.2.2** Write `adobe/_vendor/PATCHES.md` and `drm/_vendor/PATCHES.md`. For each file, record:
  - the upstream repository and commit
  - its license
  - every local change: relative imports, the `report()` hook, redacted logging, the parse/download/apply split, `apply_license`, `_save_error_body`, the HTTP 429 handling
- **Tests:** none (documentation). The reviewer checks one file's diff against upstream using the notes.

### T1.3 oscrypto decision (S)
Decided 2026-09-29: replace `oscrypto` with a shim (REFACTOR_PLAN decision 21, §16). T1.5 builds it.
- [ ] **T1.3.1** Record the decision and the reason (the OpenSSL 3 bug on Linux, which can't be checked during the refactor) in `adobe/_vendor/PATCHES.md` and the README draft notes.
- **Tests:** none (documentation).

### T1.4 Vendor guard (S)
- [ ] **T1.4.1** Write `tests/unit/test_vendor_manifest.py`. It hashes every file in both `_vendor/` folders and compares against `_vendor/MANIFEST.sha256`.
- [ ] **T1.4.2** Add a script, `uv run python tests/tools/update_vendor_manifest.py`, to regenerate the manifest on purpose.
- **Tests:** the guard passes. Changing one byte in a vendored file makes it fail.

### T1.5 PKCS#12 shim for oscrypto (M)
- [ ] **T1.5.1** Write `adobe/pkcs12.py`, project code rather than vendored. It offers the three names `libadobe` uses, with the same arguments and return types as `oscrypto`:
  - `keys.parse_pkcs12(data, password)` returns `(private_key, certificate, extra_certificates)`
  - `dump_certificate(cert, encoding="der")` returns the certificate's DER bytes
  - `dump_private_key(key, None, "der")` returns an unencrypted PKCS#8 DER key, as `oscrypto` does
  - it parses with `asn1crypto`, derives keys and MAC keys with the RFC 7292 Appendix B algorithm on `hashlib`, and decrypts with `pycryptodome`: the PKCS#12 PBE schemes (SHA-1 with 3DES, and with RC2-40 and RC2-128) and PBES2 (PBKDF2 with AES-CBC)
  - it checks the MAC, and raises a clear error for a wrong password or an unsupported algorithm, naming the algorithm
- [ ] **T1.5.2** In `adobe/_vendor/libadobe.py`, change only the two `oscrypto` import lines to import the same names from `..pkcs12`. This is the one vendored edit in this task: update `MANIFEST.sha256` and `PATCHES.md` in the same commit.
- [ ] **T1.5.3** In `pyproject.toml`, move `oscrypto` from the dependencies to the dev group, where only the comparison tests use it. `asn1crypto` stays a core dependency. Add `cryptography` to the dev group for the test builder. Run `uv lock`, and add a changelog entry: `oscrypto` is no longer needed.
- **Tests:**
  - a builder, `fixtures/builders/pkcs12.py`, makes an RSA key, a certificate and PKCS#12 files with `cryptography`: 3DES for both bags, and PBES2 with AES-256. An RC2-40 certificate bag, the old OpenSSL default that Adobe servers may use, is generated once with `openssl pkcs12 -export -legacy` and committed under `fixtures/`
  - for each file, the shim's three results are byte-identical to `oscrypto`'s. These tests skip when `oscrypto` can't be imported
  - a wrong password, a changed MAC, and an unsupported algorithm each raise the expected error
  - `sign_node` on a synthetic activation and device key gives the same signature through the shim as through `oscrypto`, and the signature verifies with the certificate's public key
  - `libadobe` imports and signs with `oscrypto` blocked (`sys.modules["oscrypto"] = None`)
  - `live`, local only: the real `activation.xml` on this machine gives identical results through the shim and `oscrypto`. Nothing from it is written to disk or committed
  - the Linux run is deferred (D3)

**Phase 1 exit:** vendored code is in `_vendor/` and documented; the guard is active; `libadobe` no longer needs `oscrypto`; the CLI behaves the same.

---

## Phase 2: `domain/` and `infra/`
Goal: build the foundation pieces, each fully unit-tested. The old code doesn't use them yet, except redact (T2.12).

### T2.1 `domain/errors.py` (S)
- [ ] **T2.1.1** Create `BookLoaderError(message, hint=None, step=None)` and the subclasses from `utils/errors.py`: `AuthorizationError`, `ACSMFulfillmentError`, `ManualDownloadRequired(reason, url, link_file, acsm_path, pending_id)`, `DRMRemovalError`, `KoboLibraryNotFoundError`, `KoboDecryptionError`. Add `ConversionError` with `CalibreNotFoundError`, `WeasyPrintUnavailableError` and `ConversionFailedError`, plus `ConfigError`, `LockedError`, `SecretUnavailableError`, `ArchiveError` and `LibraryError`.
- [ ] **T2.1.2** Drop `WorkflowError`, which is never raised.
- [ ] **T2.1.3** `ManualDownloadRequired`'s message mentions `--downloaded-file` and `pending resume <id>`.
- **Tests:** every error builds a message and a hint; `ManualDownloadRequired`'s message holds both finishing instructions and the link page path, and never the URL.

### T2.2 `domain/models.py`, `events.py`, `prompts.py` (M)
- [ ] **T2.2.1** Create the models as frozen dataclasses:
  - `AuthType(StrEnum)` with `anonymous`, `adobe_id`, `ade_unusable`, `none`, `unknown`
  - `AuthInfo`
  - `BookFormat` (`epub`, `pdf`)
  - `ProcessRequest`, `ProcessResult`, `StepResult`
  - `BatchResult` with `exit_code`
  - `BookRecord`, `PendingRecord`, `LoanRecord`
- [ ] **T2.2.2** Create the typed events: `StepStarted`, `StepProgress(done, total)`, `StepNote`, `StepDone`, `StepFailed`, `Warning`, `ServerContact(kind, url)`, `Redirected(url)`, `Retrying(message)`. Add the `Reporter` protocol (`emit(event)`, plus `suspend()` returning a context manager).
- [ ] **T2.2.3** Create the `Prompter` protocol: `manual_download`, `resolve_conflict`, `select_books`, `select_many`, `confirm`, `choose`, `text`, `secret`.
- **Tests:** model invariants (for example, `BatchResult.exit_code` is 0 only when nothing failed or is pending), and each event is immutable.

### T2.3 `domain/conflicts.py` and `retention.py` (S)
- [ ] **T2.3.1** Port `ConflictAction` and the logic of `_resolve_file_conflict` into `ConflictResolver(policy, prompter, batch)`:
  - `--overwrite` together with `--skip-existing` is invalid
  - a remembered "all" choice wins
  - a single book gets a yes/no prompt, a batch gets the menu
  - cancel raises `OperationCancelled`
  - add the new `rename` choice
- [ ] **T2.3.2** Create `RetentionPolicy.from_options(keep_encrypted, keep_epub, library_config)`. It returns which files the pipeline keeps, following REFACTOR_PLAN §7, including the one-release notice flag.
- **Tests:**
  - table-driven tests covering every policy with single and batch runs, and remembered choices
  - every row of the §7 table for plain and library mode
  - the notice appears only for `--keep-encrypted --to-pdf` without `--keep-epub`

### T2.4 `infra/paths.py` (S)
- [ ] **T2.4.1** Global folder per OS: `%LOCALAPPDATA%\book-loader\` on Windows (Known Folder API when the variable is unset), `~/.config/book-loader/` elsewhere. The auth folder is `adobe\` on Windows and `.adobe/` elsewhere. Add `logs/`, `backups/`, `config.toml` and `state.json` paths.
- [ ] **T2.4.2** Old Windows location `~\.config\book-loader\.adobe\`: returned as the auth folder only when the new one doesn't exist and the old one does, with a flag so the CLI can show the notice.
- **Tests:**
  - fake environments for Windows, macOS and Linux, including `LOCALAPPDATA` unset. The OS and environment are passed in, not read from the host, so all three run on Windows. Real macOS and Linux checks are deferred (D5).
  - the old-location fallback when only the old folder exists, when both exist, and when neither does
  - resolving paths never creates folders

### T2.5 `infra/settings.py` (M)
- [ ] **T2.5.1** A generic lookup with layers: flag, env, local.toml, library.toml, global config.toml, default. It reads TOML with `tomllib`. The library layers return nothing until Phase 9 plugs them in. Each value records where it came from.
- [ ] **T2.5.2** Auth folder resolution: `--auth-dir`, then `BOOK_LOADER_AUTH_DIR`, then the library, then global.
- [ ] **T2.5.3** Settings never write to disk.
- **Tests:**
  - precedence for each layer with conflicting values
  - the source is reported correctly
  - an invalid TOML file gives a `ConfigError` with the file and line
  - no folders or files are created (checked with a watched temp folder)

### T2.6 `infra/names.py` (M)
- [ ] **T2.6.1** `kobo_plain_name(title)`: must reproduce `golden/safe_filename.json` exactly.
- [ ] **T2.6.2** `disambiguate(name, author, volume_id)`: add the author, then an 8-character volume ID, the same way on every run.
- [ ] **T2.6.3** `library_component(text)`:
  - converts to NFC
  - replaces characters Windows rejects
  - removes trailing dots and spaces
  - avoids reserved names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, with any extension)
  - caps each name at 100 characters without splitting a character
- [ ] **T2.6.4** `render_template(template, record)` for `{author}`, `{title}`, `{year}`, `{series}`. A path that would exceed 260 characters on Windows is shortened, or rejected when long-path support is off.
- **Tests:**
  - golden parity
  - the same duplicate name on every run
  - property-style tests: every output is valid on all three OSes, idempotent, and NFC
  - reserved names and long-path cases

### T2.7 `infra/fs.py` (M)
- [ ] **T2.7.1** `atomic_write(path)`: write to a temp file in the same folder, then `os.replace`.
- [ ] **T2.7.2** `unique_path(path)`: adds ` (2)`, ` (3)` and so on.
- [ ] **T2.7.3** `private_dir(path)`:
  - creates the folder with mode `0700` on POSIX (`posix_only` test, deferred D4)
  - on Windows, checks that no broader permissions were added (read-only check)
  - only called by code that writes
- [ ] **T2.7.4** `retry_locked(fn)`: on Windows, retries `PermissionError` and sharing violations with backoff for about 2 seconds, then raises `LockedError` naming the file.
- [ ] **T2.7.5** `Workspace(parent)`:
  - a hidden temp folder in `parent`
  - `preserve(pattern, dest_resolver)`
  - on exit, runs the preserve rules, then removes the folder
  - a locked file left inside produces a warning, not an error
- [ ] **T2.7.6** `safe_move(src, dst)`: `os.replace` on the same drive; across drives, copy, fsync, check the hash, then delete the source.
- **Tests:**
  - an interrupted atomic write leaves the old file intact
  - `unique_path` sequences
  - the workspace is removed on success and on an exception, and preserved files survive
  - a locked-file simulation (`windows_only`: an open handle in a thread)
  - a cross-drive move simulated by forcing the copy path

### T2.8 `infra/locks.py` (S)
- [ ] **T2.8.1** `Lock(path, timeout)`:
  - created atomically (`O_CREAT|O_EXCL`), holding the process ID, host name and start time
  - stale when that process is gone on the same host
  - `LockedError` after the timeout
- **Tests:**
  - two locks: the second waits and then fails
  - a stale lock from a dead process ID is taken over
  - the lock is released on an exception
  - a real subprocess holds a lock (both OSes; the POSIX run and stale-lock detection on POSIX are deferred, D8)

### T2.9 `infra/secrets.py` (S)
- [ ] **T2.9.1** `resolve_secret(kind, file_opt, stdin_flag, env_var, legacy_value, prompter, interactive)`:
  - applies the orders from REFACTOR_PLAN §9.9 and §10.7
  - `--password` returns a warning to show
  - raises `SecretUnavailableError` with hints when nothing is available
- [ ] **T2.9.2** A secret is never included in `repr` or exceptions: wrap it in a `Secret` type.
- **Tests:**
  - every source order
  - `--password-stdin` combined with other input on stdin gives an error
  - no prompt when not interactive
  - `str(Secret)` and `repr(Secret)` are masked

### T2.10 `infra/logging.py` (M)
- [ ] **T2.10.1** `capture_vendored_print(logger)`: a context manager that sends vendored `print()` lines to the logger at DEBUG instead of swallowing them.
- [ ] **T2.10.2** A redaction filter on the handler: every record's message and arguments go through `redact_text`, and URLs through `redact_url`.
- [ ] **T2.10.3** `setup_run_logging(logs_dir, keep_days, extra_file, enabled)`:
  - rotates `latest-book-loader.log` to `book-loader-<start time>.log`
  - falls back to `latest-book-loader-<pid>.log` when the file is locked
  - deletes `*.log` and `download_error*.html` older than `keep_days`
  - `keep_days = 0` keeps everything
- **Tests:**
  - rotation naming with a frozen clock
  - cleanup by modification time
  - the locked-latest fallback
  - fixture log lines containing a password, a download URL, a UUID and an email are written redacted
  - vendored `print` output reaches the log

### T2.11 `infra/archive.py` (L)
- [ ] **T2.11.1** Writer:
  - a tar stream with `manifest.json` as its first member
  - gzip, or xz on request
  - written to a temp file and renamed, with mode `0600`
- [ ] **T2.11.2** Encryption layer:
  - header: magic bytes, format version, scrypt settings, salt
  - AES-256-GCM in 1 MiB chunks, each with its own nonce and tag
  - the chunk index is bound into each chunk, and a final-chunk flag prevents truncation
- [ ] **T2.11.3** Reader:
  - detects encryption from the header
  - `read_manifest()` without extracting everything
  - `extract(members, dest)` with `filter="data"`, rejecting absolute paths and `..`, applying `names.library_component` rules, and detecting case collisions
  - checks every file's SHA-256 against the manifest
- [ ] **T2.11.4** Reader for original archives: plain `.tar.gz` files with a single top folder of any name.
- **Tests:**
  - round trips, plain and encrypted
  - wrong passphrase
  - a flipped byte in any chunk
  - a truncated file (the missing final chunk is detected)
  - reordered chunks
  - the manifest is readable without full extraction
  - malicious members (absolute path, `..`, symlink escape)
  - names invalid on Windows or NFD, and case collisions
  - a failed write leaves no final file
  - a v0 fixture archive from T0.4.4 is read

### T2.12 Move `redact.py` (S)
- [ ] **T2.12.1** `git mv utils/redact.py infra/redact.py`. Leave `utils/redact.py` re-exporting it until Phase 6.
- [ ] **T2.12.2** Change the import in `libadobe.py` and `libadobeFulfill.py` to `from ...infra.redact import …`: the one allowed vendored edit in this phase. Update `MANIFEST.sha256` and `PATCHES.md`.
- **Tests:** the T0.4.1 golden tests are moved to point at `infra.redact`, the characterization suite passes, and the vendor guard passes after the manifest update.

### T2.13 `infra/known_dirs.py` (S)
- [ ] **T2.13.1** Find the Downloads folder:
  - Windows: `SHGetKnownFolderPath(FOLDERID_Downloads)` via `ctypes`
  - Linux: the `XDG_DOWNLOAD_DIR` entry in `~/.config/user-dirs.dirs`
  - macOS and fallback: `~/Downloads`
  - an override from settings
- **Tests:** a parser test for `user-dirs.dirs`; `windows_only`, the Known Folder result is an existing folder; the override wins. A real Linux desktop and macOS are deferred (D5).

### T2.14 Type checking (S)
- [ ] **T2.14.1** Add a pyright config that checks `domain`, `infra` and each new package as it appears. The old `core`, `utils` and `cli.py` are excluded until they're deleted, and `_vendor` is always excluded. Add it to the local check (T0.5.4), which the CI workflow also runs.
- **Tests:** the local check runs pyright. It fails on a deliberate type error, which is then reverted.

**Phase 2 exit:** every infra and domain module is fully tested, with at least 90% line coverage for these packages. The old CLI is unchanged, apart from where it imports redact.

---

## Phase 3: Adobe adapters and EPUB inspection
Goal: all Adobe-side code rebuilt on the new foundation, tested with fixtures and a fake `libadobe`.

### T3.1 `epub/inspect.py` (S)
It comes first because T3.7 and Phase 11 use it.
- [ ] **T3.1.1** `sniff(path)` returns `epub`, `pdf`, `html` or `unknown` from the first bytes: `PK`, `%PDF`, or `<!doctype html` / `<html` after trimming whitespace and a BOM.
- [ ] **T3.1.2** `read_opf(epub)` returns the title, authors, language, identifiers and spine, via `container.xml`.
- [ ] **T3.1.3** `is_adept_encrypted(path)`: an EPUB with `META-INF/rights.xml`, or a PDF with `/EBX_HANDLER`.
- **Tests:** built EPUBs (normal, with a BOM, several identifiers, broken container), ADEPT EPUBs from T0.4.6, the tiny PDF from T0.4.7, and saved HTML check pages.

### T3.2 `adobe/store.py`: AuthStore (M)
- [ ] **T3.2.1** The file layout. `status()` returns an `AuthInfo` with the type, email, device UUID and key fingerprint, parsing `activation.xml` once and caching it. An `activation.dat`-only folder gives `ade_unusable`.
- [ ] **T3.2.2** `device_key()` returns the DER bytes. `fingerprint()` returns the SHA-256 of the key.
- [ ] **T3.2.3** Account operations, run through `AdeptSession` (T3.3):
  - `authorize_anonymous()` and `authorize_adobe_id(email, Secret)`, with the four protocol steps
  - partial files are removed on failure
  - the error names the step that failed
  - holds the auth lock
- [ ] **T3.2.4** `reset()` deletes the auth files, `pending/` and `loans.json`, under the lock.
- **Tests:**
  - `activation.xml` fixtures, anonymous and Adobe ID (generated from a template with a fake key)
  - each status case, including `ade_unusable` and unknown
  - authorization with the fake `libadobe`: success, and failure at each of the four steps leaves no partial files
  - reset removes exactly the expected files
  - parity with 0.1.0: port T0.4-style expectations for `get_auth_type` and `get_adobe_id_email`

### T3.3 `adobe/session.py`: AdeptSession (M)
- [ ] **T3.3.1** A context manager that saves and later restores `libadobe`'s account path, verbose flag and status callback. It sets them for this store, captures vendored `print` output (T2.10.1), and holds nothing after it exits.
- [ ] **T3.3.2** Map `report(event, **data)` events to domain events. Normal mode keeps only the host name of a URL; verbose passes the redacted URL. Use the old `StepReporter.on_event` as the reference for the mapping.
- **Tests:**
  - with a fake `libadobe` module, globals are set inside the context and restored after, even when an exception is raised
  - each `report` event maps to the right domain event
  - URLs are reduced to the host in normal mode

### T3.4 `adobe/pending.py`: PendingStore (M)
- [ ] **T3.4.1** A v1 schema:
  - `version`, `id` (the first 16 hex characters of the ACSM's SHA-256), `saved_at`, `acsm_name`, `acsm_path`, `acsm_content` (base64)
  - `key_fingerprint`, `library`, `output_dir`, `link_page`, the settings (retention and convert), `title`, `acsm_expires`, `info`
- [ ] **T3.4.2** Load v0 records transparently, with empty ACSM fields. Add `save`, `load(id or prefix)`, `list(library=None)`, `clear(id)` (which also deletes the link page) and `stale()`.
- [ ] **T3.4.3** Files use mode `0600` and the folder `0700`; writes are atomic.
- **Tests:**
  - v1 round trip
  - loading the v0 fixture from T0.4.5
  - prefix lookup, including an ambiguous prefix error
  - a mismatched fingerprint is marked stale
  - clearing removes the link page
  - listing filtered by library

### T3.5 `adobe/loans.py`: LoanStore (S)
- [ ] **T3.5.1** `capture(reply)`:
  - when the reply has `<returnable>true</returnable>`, it calls `libadobeFulfill.updateLoanReturnData(reply, forceTestBehaviour=True)`
  - it adds the title, library and file locations, and saves to `<auth>/loans.json` (atomic, mode `0600`)
- [ ] **T3.5.2** `list`, `get`, `mark_returned`, `clear_expired`.
- **Tests:**
  - capture from the loan reply fixture (T0.4.10) gives the right loan ID, operator and `validUntil`
  - a non-returnable reply records nothing
  - the Calibre settings path is never called (monkeypatched to fail if it is)
  - clearing expired loans uses a frozen clock

### T3.6 `adobe/fulfillment.py`: Fulfiller (L)
- [ ] **T3.6.1** `fulfill(acsm, workspace, session)`:
  - calls the vendored `fulfill(do_notify=True)`, then `parse_fulfillment`, `download_book` and `apply_license`, just as today's `ACSMFulfiller` does
  - on a download failure where a URL exists, it saves the pending record and link page and raises `ManualDownloadRequired`
  - captures loans (T3.5)
- [ ] **T3.6.2** `fulfill_from_file(acsm_or_pending, file, workspace)`:
  - uses the pending record when the fingerprint matches, and otherwise fulfills again (the v1 record's `acsm_content` means no ACSM file is needed)
  - works on a copy
  - clears the pending record on success
- [ ] **T3.6.3** Before `apply_license`, `sniff` the file. HTML is rejected with the check-page hint, and the vendored code still saves its diagnostic page.
- [ ] **T3.6.4** Register `Workspace.preserve` rules for the link page (output or book folder) and `download_error*.html` (logs folder), and rewrite the path in the error message.
- [ ] **T3.6.5** Warn about ACSM expiry: read `<expiration>` before any network call and return it so batch can use it.
- **Tests (fake `libadobe` functions):**
  - success for both EPUB and PDF, with the correct file type
  - a blocked download produces a pending record and raises
  - the link page survives workspace cleanup, and the error message names the preserved path
  - `fulfill_from_file` with a matching record, a mismatched record (fulfills again), and a v0 record without an ACSM (asks for one)
  - an HTML file is rejected
  - a loan reply is captured
  - an expired ACSM warns

### T3.7 `adobe/identity.py` (S)
- [ ] **T3.7.1** `matches(file, pending) -> Match.yes | Match.no | Match.unknown`: compare the OPF identifiers (with the `urn:uuid:` prefix removed) against the pending `resource`. PDFs and EPUBs without that identifier give `unknown`.
- **Tests:** a matching EPUB, a different book's EPUB, an EPUB without identifiers, and a PDF.

### T3.8 `adobe/backup.py` (M)
- [ ] **T3.8.1** `backup_auth(store, dest_dir, secret, encrypt=True)`:
  - writes an auth-only archive through `infra/archive`, named `auth_<type>_<ts>.blbackup`
  - includes `pending/` and `loans.json`
  - refuses to write without a passphrase unless `encrypt=False`
- [ ] **T3.8.2** `list_backups(dir)`: both `*.blbackup` and `*.tar.gz` files, newest first, reporting type, whether encrypted, size and modified time.
- [ ] **T3.8.3** `restore_auth(archive, store, secret)`:
  - handles original archives, auth-only archives, and full library archives (taking only the `auth` part)
  - checks the restored folder holds a usable authorization before replacing the current one
  - backs up the current one first
  - extracts into the configured folder whatever it's named
- **Tests:**
  - the T0.4.4 strict-xfail case is ported and must now pass
  - round trip with and without encryption
  - no passphrase gives an error and writes nothing
  - an archive without a usable authorization is refused and the current one is kept
  - a v0 archive restores into a folder with a different name
  - listing sorts correctly

**Phase 3 exit:** every Adobe adapter works against fixtures. The old `core/adobe` is still in use by the CLI.

---

## Phase 4: DRM, Kobo and conversion
### T4.1 `drm/adept.py`: AdeptDecryptor (S)
- [ ] **T4.1.1** `decrypt(src, dst, device_key)`:
  - chooses by `sniff`, not by the file extension
  - result `0` means decrypted, `1` means already DRM-free (the file is copied), anything else raises `DRMRemovalError`
  - vendored `print` output is captured
- **Tests:**
  - a synthetic ADEPT EPUB decrypts to the plaintext
  - a plain EPUB is copied as is
  - the wrong key raises the error
  - a PDF path is checked with a fake `ineptpdf` for how codes are handled, and against a real ADEPT PDF only under `live`

### T4.2 `kobo/` (L)
- [ ] **T4.2.1** `paths.py`: default library folder per OS (`%LOCALAPPDATA%\Kobo\Kobo Desktop Edition`, `~/Library/Application Support/Kobo/Kobo Desktop Edition`, Linux: none unless configured).
- [ ] **T4.2.2** `macs.py`:
  - parsers for macOS `ifconfig -a`, Windows `getmac /fo csv /nh /v` with `Get-NetAdapter` as fallback, and Linux `/sys/class/net/*/address`
  - adds `extra_macs` and `--mac`
  - normalises to upper case with colons and removes duplicates
  - skips all-zero addresses
- [ ] **T4.2.3** `keys.py`: the pure derivation of `userkeys(macs, user_ids)`.
- [ ] **T4.2.4** `library.py`:
  - a context manager that copies the database with the SQLite backup API from a read-only URI connection, falling back to the byte-patch copy
  - keeps the outer `fetchall()` and inner second cursor, with a comment
  - `books`
  - the temp database is deleted on exit, including on an exception
- [ ] **T4.2.5** `decryptor.py`:
  - `with` blocks for the ZIP files
  - a PKCS#7 padding check
  - the key is chosen from a sample of up to 5 files, allowing a UTF-8 BOM and JPEG/PNG/GIF signatures, then the whole book is decrypted with it without checking each file
  - `last_good_key` is tried first for the next book
  - DRM-free books are copied
  - names come from `infra/names`
- **Tests:**
  - MAC parsers against the fixtures in `fixtures/os_output/`, including disconnected adapters and localized `getmac` output. The `ifconfig` and `/sys` fixtures are hand-written until real captures exist (deferred D6).
  - key vectors match `golden/kobo_keys.json`
  - `library` tests on the T0.4.3 builder, including the WAL variant: a change still in the WAL is visible, and the fallback path is exercised
  - the temp database is gone after an exception
  - decryption with a BOM-prefixed XHTML
  - a slightly malformed XHTML is decrypted, not rejected
  - bad padding means the key is rejected
  - key reuse order
  - plain-mode names match the golden values; two books with the same title get stable distinct names
  - `live` + `windows_only`: a real Kobo Desktop install is listed
  - Kobo on macOS, the only platform it supports today, is checked against a real install after the refactor (deferred D7)

### T4.3 `conversion/` (M)
- [ ] **T4.3.1** `base.py`: the `Converter` protocol, a registry, and `get_converter(name)` raising `ConfigError` for an unknown name.
- [ ] **T4.3.2** `weasyprint_engine.py`:
  - built from `epub_to_pdf_improved.py`: spine order, the book's CSS, resources, page numbers
  - titles and authors escaped
  - options `page_size`, `margin`, `page_numbers`, `css`
  - WeasyPrint imported lazily; an `OSError` at import becomes `WeasyPrintUnavailableError`, naming the missing library and suggesting Calibre
  - warnings go through the reporter
- [ ] **T4.3.3** Split HTML building from rendering, so HTML can be tested without WeasyPrint.
- [ ] **T4.3.4** `calibre_engine.py`: discovery on `PATH`, then the standard install folders, then `calibre_path`; runs `ebook-convert`; `CalledProcessError` becomes `ConversionFailedError` with the tail of stderr.
- **Tests:**
  - HTML building: spine order, CSS kept, `<script>` removed, title escaping (`<b>&"'`), images rewritten
  - an import failure simulated by monkeypatching `import`
  - `weasyprint` marker: a real PDF is produced from a small EPUB
  - Calibre discovery with fake folders on each OS layout, and a subprocess fake for success and failure
  - WeasyPrint and Calibre on real macOS and Linux installs are deferred (D10)

**Phase 4 exit:** every adapter layer is complete and tested. The old code is still in use.

---

## Phase 5: Application services and the pipeline
### T5.1 `app/acsm.py`: AcsmService (L)
- [ ] **T5.1.1** Build the steps from `ProcessRequest`: `EnsureAuth`, `Acquire`, `Decrypt`, then `Optimize` (only an extension point until Phase 8), `Convert?` and `Store`.
- [ ] **T5.1.2** `EnsureAuth`:
  - plain mode keeps automatic anonymous creation, as today
  - a library hook asks first (wired in Phase 9)
  - reports the auth type
- [ ] **T5.1.3** `Acquire`: automatic download, `--downloaded-file`, or the manual loop through `Prompter.manual_download`, where a wrong file gives the error back and asks again.
- [ ] **T5.1.4** `Decrypt` into the workspace, `Convert` inside the workspace (a failure keeps the EPUB and warns; a PDF is skipped), then `Store`:
  - moves files according to `RetentionPolicy`
  - resolves conflicts with `ConflictResolver`
  - `--keep-encrypted` writes `<Title>.encrypted.<ext>`
  - refuses to write over its own input
- [ ] **T5.1.5** Return a `ProcessResult` with the output paths, format and size, loan info and warnings. The lock is held for the whole pipeline.
- **Tests (fakes: FakeFulfiller, FakeDecryptor, FakeConverter, FakePrompter, RecordingReporter), each run for both EPUB and PDF:**
  - **every row of the retention table** in REFACTOR_PLAN §7
  - the T0.4.8 data-loss xfail is ported and must now pass: an existing `Title.epub` survives `--to-pdf`
  - conflict policies for `process`
  - the workspace is removed after success and after a failure in each step
  - the link page and diagnostic pages are preserved
  - the manual loop: wrong file, then right file
  - a non-interactive prompter makes `ManualDownloadRequired` reach the caller
  - the step count and order of events
  - the ported T0.4.8 expectations about step counts and failed conversions still hold

### T5.2 `app/auth.py`: AuthService (M)
- [ ] **T5.2.1** `create`: refuses when authorized and gets the password through `infra/secrets`.
- [ ] **T5.2.2** `info`: returns the type, email, fingerprint, folder and source.
- [ ] **T5.2.3** `reset`:
  - makes an encrypted backup first, in the old default folder `~/adobe-ade-auth-bk/`
  - stops if no passphrase is available and `--no-encrypt` isn't given, deleting nothing
  - then resets
- [ ] **T5.2.4** `backup`, `restore`, `list_backups` go through `adobe/backup.py`.
- [ ] **T5.2.5** Every method returns a target description: the folder and where it came from (flag, env, library, global).
- **Tests:**
  - reset in a script with no passphrase deletes nothing and gives a hint
  - reset with `--no-encrypt` writes a plain `0600` archive
  - the reset backup name is `auth_<type>_<ts>`
  - target descriptions for each source

### T5.3 `app/kobo.py` and `app/convert.py` (M)
- [ ] **T5.3.1** `KoboService.list()` and `decrypt(books, output, policy)`:
  - a book that isn't downloaded is reported separately and counts as a failure (the exit code stays 1)
  - cancelling stops the batch
  - returns a `BatchResult`
- [ ] **T5.3.2** `ConvertService`: refuses to write over its own input and applies the conflict policy to an existing PDF.
- **Tests:**
  - a batch mixing success, skip, failure, not downloaded, and cancel, with the right exit codes
  - convert refuses `-o` equal to the input
  - convert applies the conflict policy

### T5.4 Service factory (S)
- [ ] **T5.4.1** `app/factory.py` builds services from `Settings`. Tests can swap adapters for fakes, so the CLI tests in Phase 6 reuse the same fakes.
- **Tests:** the factory builds real adapters by default and swaps in fakes when asked.

**Phase 5 exit:** every use case works end to end with fakes, and the two strict-xfail bugs are fixed.

---

## Phase 6: New CLI with Rich, then switch over
### T6.1 CLI skeleton (M)
- [ ] **T6.1.1** Create the `cli/` package. When a `cli/` package and `cli.py` sit side by side, Python imports the package, so in the same commit:
  - `git mv cli.py cli/_legacy.py`
  - change its relative imports one level deeper (`.core` becomes `..core`)
  - have `cli/__init__.py` export the legacy `cli` until the commands are ported
  - the entry point `book_loader.cli:cli` then keeps working throughout Phase 6
  - `_legacy.py` is deleted at T6.6
  - **Test:** the characterization CLI tests pass after the move.
- [ ] **T6.1.2** Root group with rich-click and the global options `--verbose`, `--debug`, `--no-color`, `--json`, `--auth-dir`, `--library` (hidden until Phase 9), `--interactive/--no-interactive`, `--log-file` and `--no-log`.
- [ ] **T6.1.3** Old positions: `process` also accepts `-v` and `--auth-dir`. When both places are used, the value after the subcommand wins.
- [ ] **T6.1.4** `AppContext`: settings, the console, logging setup (T2.10.3), the service factory, and the one-line notice for the old Windows folder.
- [ ] **T6.1.5** Error boundary:
  - `BookLoaderError` becomes a red panel with a hint, exit 1
  - anything unexpected becomes a panel, with a traceback only under `--debug` (`show_locals=False`)
  - every message is redacted
- **Tests:**
  - `-v` and `--auth-dir` work in both positions
  - an unexpected exception exits 1, and a traceback appears only with `--debug` with no local variables shown
  - an error message containing a URL is redacted

### T6.2 UI layer (L)
- [ ] **T6.2.1** `theme.py`:
  - one `Console(stderr=True)` for status, one stdout writer for results
  - the theme
  - ASCII fallback when the encoding isn't UTF-8
- [ ] **T6.2.2** `terminal.py`:
  - prompts are possible only when stdin and stderr are terminals and `--json` isn't set
  - mintty detection (`MSYSTEM`/`TERM_PROGRAM` set together with a missing Windows console) picks `PlainPrompter`
  - `--interactive/--no-interactive` override the detection
- [ ] **T6.2.3** `reporter.py`: `RichReporter` maps domain events to permanent step lines, a live spinner, and a download bar (`DownloadColumn`, `TransferSpeedColumn`, `TimeRemainingColumn`), with `suspend()`. Verbose log records go through `RichHandler` on the same console.
- [ ] **T6.2.4** `prompter.py`:
  - `RichPrompter`: questionary for the menus, prompt_toolkit password mode with no history for `secret()`, all on stderr
  - `PlainPrompter`: numbered menus, `input()`, and `getpass` for secrets
  - `NonInteractivePrompter`: raises with hints
- [ ] **T6.2.5** `views.py`: the auth panel, the backups table, and the Kobo table (ellipsis columns, DRM badge, "Downloaded" column), plus the batch summary and info checklist.
- **Tests:**
  - reporter snapshots with `Console(record=True, width=100)` for a full successful run, a failed step, verbose mode, and ASCII mode
  - prompter selection for every combination of terminal flags, mintty, and the override
  - `secret()` is created with password mode and without history (check how the prompt_toolkit session is built)
  - prompts write to stderr only: stdout stays empty in a prompted run
  - a table snapshot with CJK titles lines up

### T6.3 Port the commands (L)
Port one command group per commit; each commit includes its CLI tests.
- [ ] **T6.3.1** `process` (single file):
  - calls `AcsmService`
  - prints result paths to stdout, or `--json`
  - with a blocked download, prints the "finish later" command (with `--downloaded-file` and `pending resume`) and exits 1
- [ ] **T6.3.2** `auth create`, `info`, `reset`, `backup`, `restore`:
  - every command prints its target
  - `reset` keeps `--yes`
  - the backup commands take the passphrase options and `--no-encrypt`
  - `create` takes `--password-file`, `--password-stdin` and `--password` (with its warning)
- [ ] **T6.3.3** `kobo [--source] list` and `dedrm [--all] [-o] [--overwrite|--skip-existing] [--mac]`.
- [ ] **T6.3.4** `convert EPUB [-o] [--convert-engine] [--page-size] [--margin] [--no-page-numbers] [--css]`.
- [ ] **T6.3.5** `info`: the checklist, with the source of each setting.
- [ ] **T6.3.6** `logs path|show [-n]|clean`.
- [ ] **T6.3.7** `--json` on `info`, `auth info` and `kobo list`: `"schema": 1`, nothing else on stdout.
- **Tests (CliRunner + fakes via the factory):**
  - every T0.4.9 characterization case is ported to the new CLI with the same exit codes
  - the stdout contract: `process` prints only paths
  - a JSON schema test per command, and a check that no URL or key is ever included
  - `auth reset --yes` with no passphrase in a script exits 1 and deletes nothing
  - `--password` prints its warning
  - `logs show` prints the latest log

### T6.4 Help parity and rich-click (S)
- [ ] **T6.4.1** Group options into rich-click panels (Output, Retention, Conversion, Advanced).
- **Tests:** the T0.3 parity test passes against the new CLI; help renders without errors for every command.

### T6.5 Manual checks on real systems (M)
Run and record in the PR, on Windows (including Git Bash). The same checks on macOS or Linux are deferred (D12):
- [ ] `info`, `auth info` on a fresh machine: no folders created
- [ ] `auth create --anonymous` (network), `auth info`, `auth backup` (passphrase prompt shows `*`), `auth restore`, `auth reset`
- [ ] `process` with a real EPUB ACSM and a real PDF ACSM
- [ ] a blocked Google Play download, finished with `--downloaded-file`
- [ ] `convert` with both engines where installed
- [ ] Kobo `list` and `dedrm` on Windows, after Phase 4's Windows support is confirmed. On macOS, deferred (D7)
- [ ] the same commands in Git Bash: plain prompts, no crash

### T6.6 Switch over and delete the old code (M)
- [ ] **T6.6.1** Delete `core/`, `utils/`, the old `cli.py`, `main.py`, the re-export shims and `tests/characterization/`. Everything they pinned must be ported by now: check against the list in T0.4.
- [ ] **T6.6.2** Check that the `pyproject` entry point `book_loader.cli:cli` resolves to the package.
- [ ] **T6.6.3** Drop `rsa` and `Pillow`; add `rich`, `rich-click` and `prompt_toolkit`. Run `uv lock`. `asn1crypto` stays for the PKCS#12 shim, and `oscrypto` already left the core dependencies in T1.5.
- [ ] **T6.6.4** Remove the pyright exclusions for the deleted folders.
- **Tests:** the whole suite and the parity test pass; `pip install .` in a clean virtual environment gives a working `book-loader`, and `python -m book_loader` works too.

### T6.7 Release 0.2.0 (S)
- [ ] **T6.7.1** `CHANGELOG.md` covers:
  - Python 3.11
  - `%LOCALAPPDATA%` on Windows, with the old folder read until migrated
  - `--keep-encrypted` narrowed, `--keep-epub` added, and the notice
  - encrypted auth backups
  - the stdout contract
  - `--json` and logs
  - masked passwords
  - the fixes from REFACTOR_PLAN §6
  - a note that this release was tested on Windows only, and that macOS and Linux users may hit problems (REFACTOR_PLAN §2.1)
- [ ] **T6.7.2** README and README.zh-TW sections for the changed behaviour (the full rewrite comes in Phase 12).
- [ ] **T6.7.3** Bump the version and tag `v0.2.0`.
- **Tests:** the local check passes on the tag, and the Windows part of T6.5 is complete.

---

## Phase 7: Pending, watched downloads, batch, loans
### T7.1 Pending commands (M)
- [ ] **T7.1.1** `app/pending.py`: `list(library, all)`, `open(id)` (via `webbrowser.open`, never `click.launch`), `resume(id, file=None)` (runs the pipeline from `Acquire` with the record's saved settings), and `clear(id | all | stale, older_than)`.
- [ ] **T7.1.2** The CLI `pending list|open|resume|clear` with `--json` on `list`.
- **Tests:**
  - listing v0 and v1 records together
  - `resume` of a v1 record without the ACSM file on disk
  - `resume` of a v0 record asks for the ACSM, or takes `--acsm`
  - `clear --stale`, also by age
  - `open` calls `webbrowser` with the exact URL, including `&` characters
  - the JSON never includes the URL

### T7.2 Watched downloads (L)
- [ ] **T7.2.1** `app/downloads.py` watcher, polling once a second:
  - candidates must be newer than the start time minus 60 seconds
  - partial files (`.crdownload`, `.part`, `.download`, `.tmp`), empty files, and files with a matching `.part` are ignored
  - a file must keep the same size for two polls
  - the type is sniffed, and HTML is rejected with a message
  - `identity.matches` is applied
- [ ] **T7.2.2** Look up typed answers as today: a path, then inside Downloads, with `.epub`/`.pdf` added.
- [ ] **T7.2.3** `cli/ui/watch.py`:
  - a prompt_toolkit prompt running asynchronously next to the watcher task
  - when a match is found, the prompt is cancelled and "Use X? [Y/n]" is shown
  - `q` quits
  - a `console.status` spinner
  - `PlainPrompter` falls back to today's line prompt
- [ ] **T7.2.4** Wire it into `Prompter.manual_download`.
- **Tests:**
  - watcher unit tests with a fake clock and a temp folder, scripted to imitate Chrome (`.crdownload` renamed), Firefox (empty final file plus `.part`), and Edge, plus an HTML check page, the wrong book, and the right book
  - lookup of typed names
  - `watch.py`, driven by prompt_toolkit's pipe input and a fake watcher: a found file cancels the prompt, a typed path wins, `q` exits
  - manual check on Windows with Chrome, Edge and Firefox

### T7.3 Batch `process` (M)
- [ ] **T7.3.1** Expand the inputs: files, folders, and `--recursive`.
- [ ] **T7.3.2** Checks before any network call: unreadable files, duplicates (by ACSM hash), and expired ACSMs (skipped unless `--try-expired`).
- [ ] **T7.3.3** Process books one after another. A blocked download becomes pending and the batch continues. The summary uses the four statuses. At the end, in a terminal, offer to handle pending books now, matching downloads with `identity`.
- [ ] **T7.3.4** `--force`: decrypt the stored encrypted copy again when one exists, and otherwise fulfill again with a warning.
- [ ] **T7.3.5** stdout: one path per finished book, or one JSON document.
- **Tests:**
  - input expansion
  - the expired-ACSM skip
  - a mix of results gives the right summary and exit code 1
  - "handle now" matches two downloads to the right books
  - `--force` with and without a stored copy
  - JSON output for the batch

### T7.4 Loans (M)
- [ ] **T7.4.1** `app/loans.py`: `list`, `return_(id, delete_files)` (through `AdeptSession` and `tryReturnBook`), `clear_expired`.
- [ ] **T7.4.2** The CLI `loan list|return|clear` with `--json` on `list`. `process` shows "Loan until …" and the one-line reminder of the terms.
- **Tests:**
  - return against a fake operator server, for success, an `<error>` answer and an invalid answer
  - the files are deleted after confirmation and kept with `--keep-files`
  - the `process` summary shows the loan line and the reminder for a loan reply
  - `network` marker: returning a real loan, only when a tester provides one (documented in `tests/live/README.md`)

### T7.5 Release 0.3.0 prep
Finished after Phase 8: changelog entries for pending, watch, batch and loans.

---

## Phase 8: Optimizer
### T8.1 `epub/optimizer.py` (M)
- [ ] **T8.1.1** Remove `rights.xml`, remove `encryption.xml` when it only lists removed entries, and remove `Adept.resource` / `Adept.expected.resource` meta tags from the OPF and XHTML files.
- [ ] **T8.1.2** Repair the container: `mimetype` first and stored uncompressed, check `container.xml` and the OPF, and report missing manifest items.
- [ ] **T8.1.3** Recompress with deflate level 9, keeping the zeroed `external_attr` behaviour (reuse the vendored `ZeroedZipInfo`).
- [ ] **T8.1.4** Return a report: size before and after, what was removed, warnings (manifest items nothing uses are reported, never removed).
- [ ] **T8.1.5** Optional `--optimize-images[=Q]` through the `images` extra, with a clear error when Pillow is missing.
- **Tests:**
  - the `mimetype` entry comes first and is stored uncompressed
  - Adept meta tags are removed, and other meta tags stay
  - the text of every XHTML file is byte-identical apart from the removed tags
  - the output opens in ebooklib, and in epubcheck if it's available on the machine (optional marker)
  - running it twice gives the same output
  - PDFs are skipped with a note
  - image recompression reduces size, and it's skipped without Pillow

### T8.2 Wiring (S)
- [ ] **T8.2.1** An `Optimize` pipeline step (`--optimize` / `--no-optimize`), the standalone `optimize` command writing `book.optimized.epub`, and `kobo dedrm --optimize`.
- **Tests:** the pipeline runs with and without the step, in the right order; the standalone command never changes its input; `--no-optimize` wins over config (tested again in Phase 9).

### T8.3 Release 0.3.0 (S)
- [ ] Changelog and README sections, manual checks of watched downloads and batch on Windows, and tag `v0.3.0`. The changelog repeats the Windows-only testing note while deferred items remain.

---

## Phase 9: Libraries
### T9.1 `library/` basics (M)
- [ ] **T9.1.1** `layout.py`:
  - find the active library: `--library`, `BOOK_LOADER_LIBRARY`, a `library.toml` in the current folder or a parent, then `default_library`
  - the folder layout and naming via `names.render_template`
- [ ] **T9.1.2** `config.py`: read and validate `library.toml` and `local.toml` (including `[watch]`, `[logs]` and `[backup.presets.*]`). `verify` warns about device-specific keys found in `library.toml`.
- [ ] **T9.1.3** `records.py`: `book.json` v1 read, write (atomic) and merge, stored in NFC.
- [ ] **T9.1.4** Plug the library layers into `Settings`, and pending and loan filtering into their stores.
- **Tests:**
  - discovery order, including from a nested subfolder
  - config validation errors name the key
  - `book.json` round trip and merge rules
  - settings precedence with all six layers

### T9.2 `library init` (M)
- [ ] **T9.2.1** Create the layout with `.book-loader/` at mode `0700`, and warn when the folder is cloud-synced (OneDrive, Dropbox, iCloud, Google Drive paths and environment variables).
- [ ] **T9.2.2** The authorization questions: when a global authorization exists, choose copy (the default), move, use global, or create new, with the explanation from REFACTOR_PLAN §10.6. `--auth` settles it without a prompt.
- **Tests:**
  - each `--auth` choice
  - interactive choices through FakePrompter
  - the cloud warning with fake paths
  - `init` inside an existing library is refused

### T9.3 Library-aware `process` and `kobo dedrm` (L)
- [ ] **T9.3.1** `Store` into `Books/<template>/`, writing `book.json`, keeping files according to `[keep]` and `[output] formats` (`--to-pdf` adds pdf for the run), and keeping the ACSM: copied by default, moved from `Inbox/` or with `--move-acsm`, using `safe_move`.
- [ ] **T9.3.2** `process` with no arguments processes `Inbox/`.
- [ ] **T9.3.3** Duplicate detection by resource ID, with `--force` as in T7.3.4.
- [ ] **T9.3.4** `EnsureAuth` in a library asks before creating an identity, and stops when not interactive.
- [ ] **T9.3.5** Kobo `dedrm` writes into `Books/` with `source: kobo`.
- **Tests:**
  - every §7 library-mode row
  - Inbox ACSMs moved, argument ACSMs copied, `--move-acsm` moves
  - a move across drives checks the copy first (forced copy path)
  - the duplicate skip
  - no silent identity creation in a library
  - Kobo books appear with correct records

### T9.4 `library status|list|convert|verify` (M)
- [ ] **T9.4.1** `status`: counts per format, pending, loans, auth mode, problems. `list` with filters and `--json`.
- [ ] **T9.4.2** `convert --missing` creates PDFs where they're missing.
- [ ] **T9.4.3** `verify`: hashes, missing and orphan files, invalid `book.json`, device-specific keys in `library.toml`.
- **Tests:** a built library with planted problems, where each problem is found exactly once; `convert --missing` with FakeConverter; list JSON schema.

### T9.5 `library watch` (M)
- [ ] **T9.5.1** `app/watch.py`: sources from the flags or `[watch] sources`; the ACSM handling rules from REFACTOR_PLAN §9.10; the library lock; a queue; blocked books become pending; `Ctrl+C` finishes cleanly with a summary.
- [ ] **T9.5.2** The Rich live view.
- **Tests:**
  - with a fake clock and folders: `--inbox` only, `--downloads` only, both, and the config default
  - partially downloaded ACSMs are ignored until complete
  - a second watcher is refused by the lock
  - a simulated `KeyboardInterrupt` gives the summary and leaves no workspace behind
  - a live-view snapshot

**Phase 9 exit:** libraries work end to end with fakes, and a manual run on Windows creates and fills a real library.

---

## Phase 10: Library backup and restore
### T10.1 Backup (L)
- [ ] **T10.1.1** Parts collection: `config`, `records`, `auth`, `global-auth`, `acsm`, `encrypted`, `epub`, `pdf`, `local`. Logs and the lock file are never included.
- [ ] **T10.1.2** Presets:
  - `full`, `restorable` (with the rule that adds DRM-free files for books without an encrypted copy), `books`, and `custom`
  - saved presets from `library.toml`
  - `--save-preset NAME`
  - `--include`/`--exclude` on top of a named preset
- [ ] **T10.1.3** Book selection: all, a filter expression, or `--books-file`.
- [ ] **T10.1.4** Encryption rules (decision 10) and passphrase sources. In a script without a preset or parts, stop and list the presets.
- [ ] **T10.1.5** Interactive flow: pick a preset showing sizes; for `custom`, the parts table, checkboxes, the book picker and the offer to save; a masked passphrase entered twice; a progress bar.
- **Tests:**
  - each preset's part set against a built library that has Adobe, Kobo, imported and loan books
  - `restorable` includes the Kobo and imported DRM-free files
  - saving a custom preset and reusing it
  - include/exclude adjustments
  - encryption forced on when `auth` is included, unless `--no-encrypt`
  - a script with no preset exits 1
  - an interactive run through FakePrompter

### T10.2 Restore and backup-info (L)
- [ ] **T10.2.1** `backup-info` shows the manifest without extracting.
- [ ] **T10.2.2** Restore:
  - choose parts and books
  - target a new library, or an existing one with `--merge` (matching by resource ID, then NFC title and author; skipping files with the same hash; conflicts through `ConflictPolicy`; merging `book.json`)
  - authorization handling: keep or replace (backing up first), flag encrypted copies whose key doesn't match, and choose where `global-auth` goes
  - names made safe for the OS, with any new name recorded
  - every file's hash checked, then a summary
- **Tests:**
  - round trip of each preset (hash-identical)
  - restoring some parts or some books
  - merging with conflicts and skips
  - a mismatched key is flagged
  - a macOS-made archive with NFD and `:` in names restores on Windows (simulated, plus `windows_only` real; an archive really made on macOS is deferred, D11)
  - a case collision
  - a tampered archive is rejected before anything is written

### T10.3 Manual cross-device check (S)
- [ ] Back up a library on one machine (`full` and `restorable`), restore on a second machine, `process` a new ACSM there with the restored authorization, and `loan return` there. Record the result. During the refactor both machines run Windows; the check with a different OS is deferred (D11).

---

## Phase 11: Migrations and import
### T11.1 Framework (M)
- [ ] **T11.1.1** `migrations/` registry. Each migration has `id`, `description`, `detect(ctx) -> [Action]` and `apply(action)`; applied IDs go in `state.json`.
- [ ] **T11.1.2** `migrate`: detect, show the plan table, confirm (`--yes`), make an encrypted backup of what will be touched, apply, then report. `--dry-run`, `--status`, `--scan DIR…`, `--cleanup`.
- [ ] **T11.1.3** A startup notice at most once a day when old data is found, with the last notice time kept in `state.json`.
- **Tests:** a registry with fake migrations for ordering, running twice, a dry run that writes nothing, and a failure in the middle that leaves applied IDs consistent; notice throttling with a frozen clock.

### T11.2 The migrations (M)
- [ ] **T11.2.1** `0001_pending_v1`: upgrade v0 records in place.
- [ ] **T11.2.2** `0002_backup_permissions`: set old archives to `0600`. `--scan` offers to re-encrypt them, replacing each only after the new copy is verified.
- [ ] **T11.2.3** `0003_windows_localappdata`: copy the old folder, check hashes, and don't delete; `--cleanup` deletes it later. Skipped when `BOOK_LOADER_AUTH_DIR` points elsewhere.
- [ ] **T11.2.4** `0004_link_pages`: move referenced link pages out of `.temp/` and update `link_file`.
- [ ] **T11.2.5** `--scan`: list leftover `.temp/` folders, `*.manual.tmp` files, diagnostic pages and loose books. Folders of still-pending books are protected.
- **Tests (using the fixtures in `fixtures/v0/`):**
  - each migration applied twice
  - 0003 on a fake Windows environment, including the case where `BOOK_LOADER_AUTH_DIR` is set, and a check that the old folder survives until cleanup
  - 0004 updates the record and the file
  - `--scan` protects pending folders
  - after `--cleanup`, the old folder is gone and nothing else is touched

### T11.3 `library import` (M)
- [ ] **T11.3.1** Scan for EPUB, PDF and ACSM files; read metadata with `epub/inspect`; detect encrypted files; group by resource ID, then by NFC title and author; confirm the grouping in a table; copy (or `--move`); merge `book.json`; decrypt encrypted copies when the matching key is available.
- **Tests:** a folder of mixed old output (including 0.1.0-style names) grouped correctly; encrypted EPUB detection and decryption with the key from the synthetic builder; `--move` uses `safe_move`; running it again imports nothing new.

---

## Phase 12: Documentation and release 0.4.0
### T12.1 Docs (M)
- [ ] **T12.1.1** Rewrite README and README.zh-TW:
  - install (Python 3.11)
  - plain mode, libraries, backups, pending and loans, logs, migration, Windows notes (`%LOCALAPPDATA%`, Kobo, Git Bash)
  - the legal terms kept, including the reminder about loans
- [ ] **T12.1.2** Update CLAUDE.md:
  - the new layout and layering rule
  - the vendor rules and guard
  - the `activation.dat` rule replacing "always check both formats"
  - Kobo no longer macOS only
  - the Kobo cursor rule kept
  - test commands and markers
- [ ] **T12.1.3** Replace `TESTING.md` with a "Testing" section (markers, live tests, fixtures).
- [ ] **T12.1.4** A migration guide from 0.1.0: what moves, the `migrate` steps, scripts affected by flag changes.
- **Tests:** a docs check that every command and option mentioned in the README exists (parsed from the README code blocks and compared with the Click tree).

### T12.2 Release 0.4.0 (S)
- [ ] Final changelog, manual checks from T6.5, T9 and T10.3 on Windows, and tag `v0.4.0`. The checks on another OS are deferred (D12); until they're done, the changelog keeps the Windows-only testing note.

---

## Task dependency summary
```
P0 ─► P1 ─► P2 ─┬─► P3 (T3.1 before T3.7) ─┐
                └─► P4 ────────────────────┴─► P5 ─► P6 ─► release 0.2.0
                                                     │
                                                     ├─► P7 ─► P8 ─► release 0.3.0
                                                     │
                                                     └─► P9 ─► P10 ─► P11 ─► P12 ─► release 0.4.0
```
- Phases 3 and 4 can run in parallel after Phase 2.
- Phase 9 needs Phase 7's `--force` logic (T7.3.4) and Phase 8's optimize step for complete library behaviour. They can be started in parallel and joined before T9.3 is finished.
- T2.11 (archive) must be finished before T3.8, and T3.8 before T5.2.

## Checklist of tests that must flip or stay fixed
| Test | Created | Must pass by |
|---|---|---|
| Restore into a folder with a different name (strict xfail) | T0.4.4 | T3.8 |
| `--to-pdf` keeps an existing `Title.epub` (strict xfail) | T0.4.8 | T5.1 |
| Help option parity | T0.3 | every phase |
| `safe_filename` golden values | T0.4.2 | every phase (via `names.kobo_plain_name` from T2.6) |
| Kobo key vectors | T0.4.3 | every phase |
| Kobo change still in the WAL is listed (strict xfail) | T0.4.3 | T4.2 |
| Kobo XHTML with a UTF-8 BOM is decrypted (strict xfail) | T0.4.3 | T4.2 |
| Redaction golden values | T0.4.1 | every phase |
| Vendor manifest | T1.4 | every phase |
| PKCS#12 shim matches `oscrypto` | T1.5 | every phase, while `oscrypto` is in the dev group |

---

## Deferred until after the refactor
Everything here needs macOS, Linux or a remote CI pipeline, and none is available during the refactor (REFACTOR_PLAN §2.1, §18). Run these checks once the platforms are available. **Any of them may turn up bugs that need debugging or fixing**, because the code for those systems was only tested with fakes. For each fix, add a test that runs on Windows where possible, and a changelog entry. Remove the Windows-only note from the changelog once every item is done.

- [ ] **D1 CI.** From T0.5.1, T0.5.2 and T2.14. Switch on `ci.yml` and run the whole suite on Windows, macOS and Linux with Python 3.11 and 3.14. Run the manual network workflow once. *Possible fixes:* any behaviour the fakes didn't model, path separators, line endings, encodings, and temp-folder handling.
- [ ] **D2 Fixtures and platform-only tests.** From T0.2 and every `macos_only`/`posix_only` test. Run the fixture smoke tests and all skipped platform tests for the first time. *Possible fixes:* `HOME` handling in `tmp_home`, and platform tests that never ran and may be wrong themselves.
- [ ] **D3 PKCS#12 shim on Linux.** From T0.5.3, T1.3 and T1.5. On a current Linux distribution with OpenSSL 3 and without `oscrypto` installed, import `libadobe`, sign a test node, and run `auth create --anonymous` and `process` with a real ACSM. *Possible fixes:* the shim is pure Python, so problems are unlikely; any that appear are in the shim's algorithm support. Once this passes, decide whether to drop `oscrypto` from the dev group and retire the comparison tests.
- [ ] **D4 File permissions.** From T0.4.5, T2.7.3, T2.11, T3.4.3, T3.5, T5.2, T9.2.1 and T11.2.2. On macOS and Linux, check that private folders are `0700` and that key-holding files (pending records, loans, backups) are `0600`, including files created before the folder existed. *Possible fixes:* the umask, missing `chmod` calls, and modes lost by atomic writes.
- [ ] **D5 Paths and known folders.** From T2.4 and T2.13. Check `~/.config/book-loader/` and `.adobe/` on macOS and Linux, with `BOOK_LOADER_AUTH_DIR` and `--auth-dir`. Check the Downloads lookup on a Linux desktop with `XDG_DOWNLOAD_DIR`, on a Linux server without it, and on macOS. *Possible fixes:* `user-dirs.dirs` quoting and `$HOME` expansion.
- [ ] **D6 MAC address parsers.** From T0.4.3 and T4.2.2. Capture real `ifconfig -a` output on macOS and `/sys/class/net/*/address` on Linux. Replace the hand-written fixtures and rerun the parser tests. *Possible fixes:* the parsers, for unexpected adapter types, bridges, and virtual interfaces.
- [ ] **D7 Kobo on macOS.** From T4.2 and T6.5. Run `kobo list` and `kobo dedrm` against a real Kobo Desktop on macOS, the only platform Kobo works on in 0.1.0. Check that output names and bytes match what 0.1.0 produces. *Possible fixes:* the default library path, MAC reading, the SQLite copy of a database Kobo Desktop has open, and name normalisation on APFS.
- [ ] **D8 Locks and file moves.** From T2.7 and T2.8. On POSIX, check that stale-lock detection spots a dead process, a real subprocess holds a lock, `atomic_write` keeps the old file on failure, and `safe_move` works between file systems. *Possible fixes:* process-liveness checks and `os.replace` across mounts.
- [ ] **D9 Terminal and prompts.** From T6.2 and T7.2. In the macOS Terminal and a Linux terminal, check Rich output and colours, questionary menus, masked password input, the watched-downloads prompt, and plain output when piped. *Possible fixes:* terminal detection and prompt_toolkit behaviour.
- [ ] **D10 Conversion.** From T4.3. Run the `weasyprint` tests with the native libraries installed on macOS and Linux. Check that Calibre is found at `/Applications/calibre.app/…` on macOS and on `PATH` on Linux. *Possible fixes:* Calibre discovery and the hint text for missing WeasyPrint libraries.
- [ ] **D11 Cross-OS backups.** From T10.2 and T10.3. Back up a library on macOS and restore it on Windows, then the reverse, with NFD names, `:` in names, and case collisions. Then `process` an ACSM and `loan return` on the target. *Possible fixes:* name mapping and permissions after restore.
- [ ] **D12 Manual release checks.** From T6.5 and T12.2. Run the whole T6.5 list on macOS or Linux. *Possible fixes:* anything the earlier items missed.
