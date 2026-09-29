# book-loader refactor plan

> Status: **planning only, no code changed yet.** Written 2026-09-28 against commit `3f70d22`, and revised the same day after a review against [ORIGINAL_STRUCTURE.md](ORIGINAL_STRUCTURE.md). That file describes the code this plan starts from. References like "§6" point within this file. The step-by-step tasks and tests for carrying it out are in [REFACTOR_ACTION_PLAN.md](REFACTOR_ACTION_PLAN.md).
>
> Revised 2026-09-29: the refactor is carried out on Windows only, with no remote CI. §2.1 describes these working constraints, and §18 lists what must be checked, and possibly fixed, on macOS and Linux once the refactor is done.

## 1. Goal and decisions

Rewrite book-loader's own code into clean layers. No current feature is lost, and several are made better. The vendored Adobe and DeDRM code is only moved, never changed.

Decisions made while planning (2026-09-28):

| # | Question | Decision |
|---|---|---|
| 1 | Kobo on Windows | **Yes**, add it (§9.1) |
| 2 | `--optimize` | **Implement it** (§8) |
| 3 | File retention | **Split the flags.** `--keep-encrypted` keeps only the encrypted file; a new `--keep-epub` keeps the decrypted EPUB after PDF conversion. Files the user supplied are never overwritten or deleted (§7) |
| 4 | Extras | **Yes** to `rich-click`, batch processing, the improved converter, watched downloads and a pending list (§9) |
| 5 | Library | **Add library folders** that hold config, an optional local authorization, and every file of each book (§10) |
| 6 | Migration | **Provide tools** to move data from the original version to the new one (§11) |
| 7 | Python version | **Raise the minimum to 3.11** (§4.1) |
| 8 | Config location on Windows | **Use `%LOCALAPPDATA%\book-loader\`** on Windows, so the private key stays on one machine. macOS and Linux keep `~/.config/book-loader/` (§4.1, §11.2) |
| 9 | Library backups | **The library holds its authorization so a whole library can be backed up and restored, including on another device.** A backup is one compressed file, and the user chooses what goes in it (§10.7) |
| 10 | Backup encryption | **Every backup that contains a key is encrypted unless the user passes `--no-encrypt`.** This includes `auth backup` and the automatic backup before `auth reset`. With no passphrase available, the command stops instead of writing an unencrypted key (§10.7) |
| 11 | Library loans | **Add loan tracking and early return** (`loan list` / `loan return`). Loans are still decrypted like other books, with a one-line reminder of the terms (§9.7) |
| 12 | `--keep-encrypted` in plain mode | **Write `<Title>.encrypted.epub` / `.pdf` next to the output** instead of leaving it in `.temp/` (§7) |
| 13 | ACSM files in a library | **Copied by default. Moved when they come from `Inbox/`, or when `--move-acsm` is given** (§7) |
| 14 | JSON output | **Add `--json`** to `info`, `auth info`, `kobo list`, `pending list`, `loan list` and `library list` (§9.8) |
| 15 | Adobe ID password | **Look for a password from a file, stdin or the environment first; only then ask in the terminal.** Terminal input shows `*` for each character and is never stored in any history (§9.9) |
| 16 | `library watch` | **Add it.** It watches `Inbox/`, the Downloads folder, or both (§9.10) |
| 17 | Logs | **Always keep redacted logs.** The current run writes `latest-book-loader.log`; older logs are kept for 30 days (§9.11) |
| 18 | Backup selection | **Named presets plus a `custom` choice** for advanced backups. Scripts must name a preset or list parts (§10.7) |
| 19 | Future plans | `auth import-ade` and `auth export-key` are planned for later; `auth upgrade` is dropped (§9.12) |
| 20 | Test platforms during the refactor (2026-09-29) | **Windows only, checked locally.** No macOS, Linux or remote CI is available until the refactor is done. Behaviour on those systems is covered by fakes where possible and verified afterwards (§2.1, §18) |
| 21 | `oscrypto` (2026-09-29) | **Replace it with a small shim** in `adobe/pkcs12.py`, written in pure Python on `asn1crypto` and `pycryptodome`. `oscrypto` leaves the dependencies, which removes its OpenSSL 3 bug on Linux (§16) |

## 2. Guardrails

- **Keep every existing feature.** Every command, flag, default, environment variable and exit code keeps working, apart from the planned changes to `--keep-encrypted` (§7) and backup encryption (decision 10), which come with notices. New behaviour is added next to the old. §14 maps each current behaviour to its new home.
- **Keep plain-mode output names identical.** Without a library, EPUB and PDF output names must match what 0.1.0 produced byte for byte, so conflict checks and `--skip-existing` still recognise earlier output (§5.11).
- **Vendored code gets no logic changes.**
  - `libadobe*`, `libpdf`, `customRSA`, `ineptepub`, `ineptpdf`, `adobekey`, `utilities`, `argv_utils` and `zeroedzipinfo` are only moved with `git mv`, with import paths updated.
  - They are excluded from ruff and black, so no formatting pass or lint fix ever touches them (decided 2026-09-29).
  - These files are not pure upstream copies. `libadobe` has the redaction and `report()` hooks, and `libadobeFulfill` splits the download into parse, download and apply steps.
  - `_vendor/PATCHES.md` records, for each file, its upstream project and commit, and every local change. Future upstream updates can then be diffed and reapplied.
- **Cover both EPUB and PDF.** Every ACSM code path and test handles both formats: EPUB licenses go in `META-INF/rights.xml`, PDF licenses go in `ADEPT_LICENSE` in the `EBX_HANDLER`.
- **Tests come first.** There are no tests today, so Phase 0 (§15) builds the safety net before any code moves.
- **Plain mode stays the default.** Without a library, book-loader works as it does today: files go to `-o` or the current folder.
- **Don't change on-disk formats without a reason.** Every format that changes keeps a reader for the old version and gets a migration (§11).

### 2.1 Working constraints during the refactor

Recorded 2026-09-29. These hold until the refactor is complete.

- **Only Windows is available.** There is no macOS, Linux or other POSIX machine, including virtual machines and WSL.
- **No remote pipelines.** GitHub Actions and comparable hosted CI can't be used.

What this changes:
- **A local check replaces CI as the gate.** It runs ruff, black `--check`, the type checker and pytest on Windows, under both Python 3.11 and the newest release. Every task must pass it.
- **The CI workflow is still written,** so it can be switched on later, but it isn't run during the refactor.
- **Code for other OSes is tested through fakes.** OS-specific code takes the platform, environment and command output as inputs, so tests for macOS and Linux behaviour run on Windows. Examples are fake environments for paths, and saved `ifconfig` and `/sys` output for MAC parsing.
- **Some tests can't run at all.** Tests marked `posix_only` or `macos_only` are still written but always skip. On Windows, `chmod` only toggles the read-only flag, so file-mode checks such as `0600` and `0700` can't be tested there even with fakes.
- **Results for macOS and Linux are unverified.** Nothing in this refactor may claim they work there. Releases made before §18 is done say in the changelog that only Windows was tested.
- **Deferred checks are collected in §18.** They may turn up bugs that need fixing once the platforms are available.

## 3. What's wrong today

### Structure
- [cli.py](src/book_loader/cli.py) is 912 lines. It mixes Click parsing with real logic: tar backup and restore, conflict rules, Downloads-folder scanning and Kobo batch handling. About 10 commands repeat the same `try/except Exception → secho → sys.exit(1)` block.
- [core/workflow.py](src/book_loader/core/workflow.py) imports display code (`StepReporter`, `quiet`), so the core layer depends on the terminal.
- `libadobe`'s global state (account path, verbose flag, status callback) is changed from both [account.py](src/book_loader/core/adobe/account.py) and `fulfill.py._adobe_session`.
- Constructors have side effects. `Config` creates the auth folder and runs chmod on it, and `AdobeAccount` runs mkdir, so even `book-loader info` writes to disk.
- `activation.xml` is parsed from scratch in three different methods.

### Bugs and gaps
- **Data loss with `--to-pdf`.** `process --to-pdf` writes the decrypted EPUB straight into the output folder ([workflow.py:97](src/book_loader/core/workflow.py#L97)), overwriting any existing file with that name, then deletes it after conversion ([workflow.py:141](src/book_loader/core/workflow.py#L141)). A user's own `Title.epub` in that folder is lost.
- **Silent overwrites.** `process` overwrites existing output without asking; only Kobo has conflict handling.
- **Restore goes to the wrong place.** `restore_auth` extracts to `auth_dir.parent` under the archive's own folder name ([cli.py:136](src/book_loader/cli.py#L136)), so a custom auth folder with a different name ends up in the wrong place. It also doesn't pass `filter="data"` to `extractall`.
- **`auth *` commands ignore `--auth-dir`.** They only read `BOOK_LOADER_AUTH_DIR`.
- **Backups aren't protected.** Archives contain the private key, unencrypted and without mode `0600`.
- **Backup names don't match.** `reset` names its backup `auth_backup_*` while `backup` uses `auth_<type>_*`.
- **`info`** prints the raw auth type (`AdobeID`) instead of the display name.
- **`activation.dat`** makes `is_authorized()` return true, but nothing can fulfill or decrypt with it.
- **`--keep-encrypted` has two jobs.** It also decides whether the decrypted EPUB is kept after PDF conversion.
- **Temporary files are left behind.** `.temp/` and the encrypted file stay when a later step fails.
  - Some of those files matter. `fulfill()` receives `<output>/.temp/` as its folder ([workflow.py:76-84](src/book_loader/core/workflow.py#L76-L84)). So the `<title> - download link.html` page needed to finish a blocked download is written there ([fulfill.py:181](src/book_loader/core/adobe/fulfill.py#L181)).
  - The vendored code also writes its redacted `download_error.html` / `download_error_429.html` pages there ([libadobeFulfill.py:1178](src/book_loader/core/adobe/libadobeFulfill.py#L1178)).
  - Any cleanup must keep these files (§5.3).
- **Loan records are dropped.** For returnable (borrowed) books the vendored code tries to save a loan record into Calibre's plugin settings ([libadobeFulfill.py:599](src/book_loader/core/adobe/libadobeFulfill.py#L599)). Outside Calibre that fails silently, so loans can't be returned early.
- **Kobo:**
  - `KoboLibrary.close()` isn't called when an error happens, which leaves a copy of the database, including purchase keys, in the system temp folder.
  - ZIP files aren't closed on errors.
  - Two books with the same title get the same file name.
  - `_unpad` doesn't check the padding, so a wrong key can pass as a right one.
  - The raw-byte database copy misses changes still in the WAL file while Kobo Desktop is open.
  - It only works on macOS (`/sbin/ifconfig`).
  - There is no way to supply the network address of an adapter that has since been removed.
- **Calibre isn't found on macOS.** `shutil.which("ebook-convert")` misses the default macOS install, which isn't on `PATH`.
- **Errors:** `CalibreNotFoundError` and `WorkflowError` are never raised. `ValueError`, `FileNotFoundError` and `CalledProcessError` reach the user as "Unexpected error".
- **Converter:**
  - The title and author are inserted into the HTML without escaping ([epub_to_pdf.py:92](src/book_loader/core/conversion/epub_to_pdf.py#L92)).
  - Chapters don't follow the spine order.
  - Warnings go through `print`.
  - `epub_to_pdf_improved.py` isn't wired in.
  - `--optimize` does nothing.
- **Packaging:**
  - `rsa` and `Pillow` are declared but nothing imports them, and `asn1crypto` is only used through `oscrypto`.
  - The package description and keywords mention only Adobe.
  - `main.py` is a placeholder, and `epub_optimizer.py` is empty.
  - `get_version` falls back to a hardcoded `0.1.0`.

## 4. Target architecture

There are four layers, and imports only go one way:

```
cli  ──►  app  ──►  domain  ◄──  adapters (adobe, drm, kobo, epub, conversion, library, infra)
```

Nothing below `cli/` imports `click`, `rich`, `questionary` or `prompt_toolkit`. Everything shown to the user leaves the lower layers as typed events through a `Reporter`, and every question comes back through a `Prompter`.

```
src/book_loader/
├── __init__.py              __version__ (read from package metadata once)
├── __main__.py              python -m book_loader
├── cli/
│   ├── __init__.py          exports `cli` (entry point stays book_loader.cli:cli); root group (rich-click);
│   │                        global --verbose/--debug/--no-color/--json/--auth-dir/--library/--interactive; ONE error boundary
│   ├── context.py           AppContext: Settings + Console + lazily built services
│   ├── commands/            process · auth · pending · loan · kobo · convert · optimize · library · migrate · logs · info
│   └── ui/
│       ├── theme.py         Console factory + Theme
│       ├── terminal.py      works out whether prompts are possible (§5.10)
│       ├── reporter.py      RichReporter (implements domain Reporter)
│       ├── prompter.py      RichPrompter + PlainPrompter + NonInteractivePrompter
│       ├── watch.py         watched-downloads prompt (§9.4)
│       ├── views.py         panels and tables (auth, books, backups, pending, loans, batch, library, migration plan)
│       └── errors.py        BookLoaderError → panel (message + hint); redacts messages
├── app/                     use cases; each returns a result object instead of printing
│   ├── acsm.py              AcsmService.process(ProcessRequest) -> ProcessResult (pipeline of steps)
│   ├── batch.py             BatchService: many ACSMs, carries on past failures (§9.2)
│   ├── pending.py           PendingService: list / open / resume / clear (§9.5)
│   ├── loans.py             LoanService: list / return (§9.7)
│   ├── watch.py             WatchService: library watch over Inbox and/or Downloads (§9.10)
│   ├── auth.py              AuthService: create/info/reset/backup/restore
│   ├── kobo.py              KoboService.list() / decrypt(books, policy) -> BatchResult
│   ├── convert.py           ConvertService
│   ├── optimize.py          OptimizeService
│   ├── library.py           LibraryService: init/status/list/import/verify/convert/backup/restore
│   └── migrate.py           MigrationService: detect → plan → apply → cleanup (§11)
├── domain/
│   ├── models.py            AuthInfo, AuthType(StrEnum), BookFormat, ProcessRequest/Result, BatchResult, BookRecord, LoanRecord, …
│   ├── events.py            Reporter Protocol + typed events (StepStarted, Progress, StepDone, Warning, …)
│   ├── prompts.py           Prompter Protocol
│   ├── retention.py         RetentionPolicy (§7)
│   ├── conflicts.py         ConflictPolicy + ConflictResolver (remembers an "all" choice; used by Adobe and Kobo)
│   └── errors.py            hierarchy; every error carries a .hint
├── adobe/
│   ├── store.py             AuthStore: file layout; parses activation.xml once → AuthInfo, device key
│   ├── session.py           AdeptSession: the ONLY code that touches libadobe globals; maps report() → events
│   ├── fulfillment.py       Fulfiller (fulfill / fulfill_from_file); captures loan records (§9.7)
│   ├── pending.py           PendingStore (v1 schema, reads v0) (§11.2)
│   ├── loans.py             LoanStore: <auth>/loans.json
│   ├── identity.py          checks a downloaded file belongs to a pending book (§9.4)
│   ├── backup.py            auth-only archives on top of infra/archive (+ reads original .tar.gz)
│   ├── pkcs12.py            oscrypto replacement: the three calls libadobe makes, in pure Python (§16)
│   └── _vendor/             libadobe, libadobeAccount, libadobeFulfill, libpdf, customRSA, PATCHES.md   (git mv only)
├── drm/
│   ├── adept.py             AdeptDecryptor: dispatch by format; result codes 0 / 1 / other
│   └── _vendor/             ineptepub, ineptpdf, adobekey, utilities, argv_utils, zeroedzipinfo, PATCHES.md
├── kobo/
│   ├── paths.py             default library folder for each OS
│   ├── macs.py              MacProvider per OS (macOS ifconfig, Windows getmac/PowerShell, Linux /sys) + extra_macs
│   ├── keys.py              pure key derivation (unchanged formula)
│   ├── library.py           context manager; keeps the fetchall + second-cursor rule
│   └── decryptor.py
├── epub/
│   ├── inspect.py           sniff format (PK / %PDF / HTML), read OPF metadata (title, author, identifiers)
│   └── optimizer.py         §8
├── conversion/
│   ├── base.py              Converter Protocol + registry {"python", "calibre"}
│   ├── weasyprint_engine.py built from the "improved" converter; lazy import kept
│   └── calibre_engine.py    finds ebook-convert on PATH, in standard install folders, or at calibre_path
├── library/
│   ├── layout.py            finds a library, folder layout, naming templates
│   ├── config.py            library.toml + local.toml read/validate
│   └── records.py           book.json read/write (per-book sidecar)
├── migrations/              one module per migration (0001_pending_v1.py, …) + registry
└── infra/
    ├── paths.py             global folder per OS (§4.1), including the old Windows location
    ├── settings.py          settings resolution (§10.3); no side effects
    ├── names.py             file and folder naming rules (§5.11)
    ├── archive.py           backup archive format: manifest, encryption, safe extract (§10.7)
    ├── fs.py                unique_path, private_dir(), atomic_write, Workspace, retry for locked files
    ├── locks.py             lock files for auth folders and libraries (§5.8)
    ├── known_dirs.py        Downloads folder per OS (Windows Known Folder API, Linux XDG user-dirs)
    ├── redact.py            unchanged (the vendored libadobe imports it; only that import path changes)
    ├── secrets.py           password and passphrase sources: file, stdin, environment (§9.9)
    └── logging.py           captures vendored print() → logger; redacted log files with 30-day cleanup (§9.11)
```

### 4.1 Platform baseline

**Python 3.11 or newer.**
- Update `requires-python = ">=3.11"`, the classifiers (drop 3.10, add 3.14), and `target-version = "py311"` for black and ruff. `.python-version` already says 3.13.
- 3.11 brings the standard `tomllib` for reading `library.toml`, `local.toml` and `config.toml`. Writing uses fixed template strings, so no `tomli-w` dependency is needed.
- It also brings a `tarfile` `filter="data"` that every release has, so backup restores need no version check.
- Code can use `ExceptionGroup`, `typing.Self` and `StrEnum`.

**Global config folder, resolved only in `infra/paths.py`:**

| OS | Global folder | Auth folder inside it |
|---|---|---|
| Windows | `%LOCALAPPDATA%\book-loader\` (Local AppData; the Known Folder API `FOLDERID_LocalAppData` is used if `LOCALAPPDATA` is unset) | `adobe\` |
| macOS, Linux | `~/.config/book-loader/` (unchanged) | `.adobe/` (unchanged) |

- The global folder holds `config.toml`, `state.json`, `logs/`, `backups/` and the auth folder. The auth folder holds its own `pending/` and `loans.json` (§5.7).
- `BOOK_LOADER_AUTH_DIR` and `--auth-dir` still take priority.
- **Old location on Windows:** the original version used `C:\Users\<you>\.config\book-loader\.adobe\`. Until `migrate` copies it (§11.2), that folder is still read whenever the new one doesn't exist, and a one-line notice is shown.
- **Folder protection on Windows:** `chmod 0700` does nothing there. The user's profile folders already allow only that user, so `private_dir()` only checks that no broader permissions were added.
- **Why Local rather than Roaming AppData:** Local AppData is never synced by Windows roaming profiles, so the private key and pending licenses stay on the machine that created them. Moving an authorization to another device is a deliberate step, through `auth backup`/`restore` or a library backup (§10.7). This is also where Kobo Desktop keeps its own data.

**Dependencies (`pyproject.toml`):**
- **Add** `rich`, `rich-click`, and `prompt_toolkit`. `prompt_toolkit` is used directly by the watched-downloads prompt, so it's declared rather than relied on through questionary.
- **Add** the optional extra `images = ["Pillow"]`.
- **Drop** `rsa` and `Pillow` from the core dependencies, and `oscrypto`, which the PKCS#12 shim replaces (§16). `asn1crypto` stays, because the shim uses it directly.
- **Metadata:** the description and keywords should mention Kobo.
- **Dev tooling:** move the dev tools into uv's `[dependency-groups] dev` (pytest is already there under `optional-dependencies`), and add `pyright` or `mypy`.
- **Lock file:** regenerate `uv.lock`.

## 5. Key design decisions

1. **One adapter for `libadobe`'s global state.** `AdeptSession(store, reporter, verbose)` is a context manager that owns `update_account_path`, `set_verbose`, `set_status_callback` and the stdout capture. It turns `libadobe.report()` events (`fulfill`, `notify`, `download`, `redirect`, `progress`, `retry`, `info`, `warning`) into typed events. Authorization, fulfillment and loan returns all run inside it, so no other code touches that state.
2. **The ACSM pipeline is a list of steps.** `AcsmService` builds `[EnsureAuth, Acquire, Decrypt, Optimize?, Convert?, Store]` from the request, so the "n/total" counter comes from that list instead of `step_count()`. `Acquire` covers three cases:
   - the automatic download
   - `--downloaded-file`
   - the manual-download retry loop, which keeps today's "wrong file → ask again" behaviour.
3. **Work happens in a workspace, but anything the user needs survives it.**
   - A `Workspace` context manager creates a hidden temp folder inside the output folder (on the same drive, so moves are atomic). Every intermediate file is written there, and only the final `Store` step moves files into place, after checking the conflict policy. This fixes the `--to-pdf` data loss.
   - The workspace is always removed in `finally`, but two kinds of file are moved out first:
     - **Download link pages** for blocked downloads. In plain mode they go to the output folder; in a library they go to the book's folder.
     - **Diagnostic pages** that the vendored code saves (`download_error*.html`). They go to the active logs folder (§9.11), named after the run's log, and the error message is rewritten to point at the new path.
   - `Workspace.preserve(pattern, dest)` handles this, so the rule is in one place.
4. **Prompts go through one interface.** The `Prompter` interface covers `manual_download`, `resolve_conflict`, `select_books`, `select_many` (backup parts), `confirm`, `choose`, `text` and `secret` (Adobe ID password, backup passphrase). The CLI picks one of three implementations based on §5.10:
   - `RichPrompter` in a real terminal.
   - `PlainPrompter` in terminals where prompt_toolkit can't run.
   - `NonInteractivePrompter`, which fails with a hint. For a blocked download that hint is today's "finish later" instructions.
5. **Errors carry a hint.** Each `BookLoaderError` has `message`, an optional `hint`, and a `step` that the pipeline fills in. The CLI has one error boundary that formats them and passes every message through `redact_text`, including in `--debug` mode, because exception text from HTTP calls can contain full download URLs. A `ConversionError` family covers Calibre missing, WeasyPrint's native libraries missing, and the engine failing.
6. **Settings don't touch the disk.** Settings only work out paths. Folders are created, with mode `0700` where they hold keys, only when something is written.
7. **Pending licenses and loans live with the key they belong to.**
   - As today, pending fulfillments are stored in `<auth folder>/pending/`, and loan records in `<auth folder>/loans.json`. A library with a local authorization therefore has them in `.book-loader/auth/`, and a library in global mode shares the global ones.
   - Each record stores the library it was made for, so `pending list` and `loan list` in a library show only that library's entries. `--all` shows every entry.
   - `auth reset` still deletes exactly the pending records and loans that stop working with the old key.
8. **Lock files.** `infra/locks.py` takes a lock in the auth folder before fulfilling, returning or resetting, and in `.book-loader/` before a library changes. A second book-loader run, such as a watcher plus a manual `process`, waits briefly and then stops with a clear message. It never corrupts pending records or `book.json`. Stale locks from crashed runs are detected by process ID.
9. **Output contract for scripts.**
   - Status output goes to stderr.
   - `process` writes each final output path to stdout, one per line. That replaces today's `✓ Success! Output file: …` line, which the terminal still shows in its status output.
   - `--json` writes a single JSON document to stdout instead: paths, statuses and pending IDs.
   - **Exit codes:**
     - `0`: everything finished
     - `1`: something failed or is still pending, as today
     - `2`: usage error (Click's default)
10. **Knowing when prompts are possible (`cli/ui/terminal.py`).**
    - **When prompts are allowed:** only when both stdin and stderr are terminals, and stdout isn't carrying `--json` output. Prompts are drawn on stderr, so they never end up in piped output.
    - **`--interactive/--no-interactive`** overrides the detection.
    - **Git Bash / mintty on Windows** reports no terminal, and prompt_toolkit fails there with `NoConsoleScreenBufferError`. When `--interactive` is forced there, `PlainPrompter` uses numbered menus and line input instead of questionary.
11. **Naming rules (`infra/names.py`).**
    - **Plain mode keeps today's names exactly.** Adobe names come from the vendored `parse_fulfillment`, which keeps letters, digits, spaces, `-` and `_`. Kobo names come from today's `safe_filename`, with identical output.
    - **Two Kobo books with the same name** are told apart by adding the author, then a short volume ID. The result is the same on every run, so `--skip-existing` keeps recognising earlier output.
    - **Library naming templates:**
      - names are normalised to Unicode NFC, so macOS and Windows copies match
      - characters Windows rejects are replaced, and trailing dots and spaces removed
      - reserved names (`CON`, `NUL`, `COM1`, …) are avoided
      - each folder or file name is capped at 100 characters, and the full path is checked against Windows' 260-character limit (long-path support is used when enabled)
    - The same rules apply when names are generated and when they are restored (§10.7).

## 6. Fixes during the rewrite

- **Restore:**
  - extracts into the configured auth folder, whatever it's named
  - passes `filter="data"`
  - checks that the archive holds a usable authorization before touching the current one
  - accepts old archives (§11)
- **Backups:** encrypted by default (§10.7), written with mode `0600`, and `reset` names its backup `auth_<type>_<ts>`.
- **Global options:** `--auth-dir` and `-v/--verbose` become global options. They are still accepted in their old place after `process` (`book-loader process x.acsm -v`); when given in both places, the value after the subcommand wins.
- **Auth commands name their target.** Because a library is found from the current folder, `auth create`, `auth reset` and `auth restore` always print which authorization they act on: the folder, and whether it came from the flag, the environment variable, a library or the global default. The confirmation prompt repeats it. `auth reset --yes` still skips the question, but the target is still printed.
- **`activation.dat`:** an `activation.dat`-only folder is reported as "ADE authorization, not usable yet"; `auth import-ade` is a future plan (§9.12).
- **Conflicts:** `process` gets `--overwrite` / `--skip-existing` and prompts on conflicts, like Kobo already does. A new `rename` choice writes `Title (2).epub`.
- **Kobo:**
  - `KoboLibrary` is a context manager, and the temporary database is always deleted.
  - ZIP files are closed with `with` blocks.
  - PKCS#7 padding is checked.
  - Deterministic names for duplicate titles (§5.11).
- **Kobo key checks:** these checks only choose the right key and don't reject individual files.
  - A key is accepted when a sample of decrypted files looks right: XHTML (a UTF-8 BOM is allowed), JPEG, PNG or GIF signatures.
  - Once a key is accepted, the whole book is decrypted with it, even if some XHTML is slightly malformed, as today.
  - The key that worked is tried first for the next book.
- **Kobo database copy:** use SQLite's backup API on a read-only connection, so WAL changes are included. The byte-patching copy stays as a fallback.
- **Kobo extra MACs:** `[kobo] extra_macs = ["AA:BB:…"]` in `local.toml` or `config.toml`, and `--mac` on `kobo dedrm`, add network addresses that are no longer present. For example, the one Kobo Desktop was registered with before a network card or dock changed.
- **Calibre discovery:** check `PATH`, then the standard install locations (`/Applications/calibre.app/Contents/MacOS/ebook-convert`, `C:\Program Files\Calibre2\ebook-convert.exe`), then `calibre_path` from config.
- **Converter:** titles and authors are escaped, pages follow the spine order, and warnings go through the reporter.
- **Dead code:** drop `main.py` and the empty optimizer. The version comes from package metadata.
- **CLAUDE.md:** replace the rule "Always check both formats in `is_authorized()`" with the new `activation.dat` behaviour. Keep the Kobo cursor section.

## 7. File retention policy

**Rule: book-loader never overwrites, changes or deletes a file the user gave it** (the ACSM, a `--downloaded-file`, the input to `convert` or `optimize`). The only exception is moving an ACSM into a library, which happens only for the library's own `Inbox/` or when the user passes `--move-acsm`. A move never happens across drives without the copy being checked first. This applies even more strongly when the output is a different type from the input. It only deletes files it made itself, and only once they're no longer needed.

| File | Plain mode default | Keep it with | Library mode default |
|---|---|---|---|
| ACSM ticket | left where it is | n/a (never touched) | copied into the book folder; **moved** when it came from `Inbox/` or `--move-acsm` is given |
| Licensed encrypted book | deleted after decryption | `--keep-encrypted` → `<Title>.encrypted.epub` / `.pdf` next to the output | kept (`[keep] encrypted`) |
| Decrypted EPUB when converting to PDF | deleted after conversion | `--keep-epub` | kept when `"epub"` is in `[output] formats` (the default) |
| Final output (EPUB or PDF) | written; conflict policy applies | n/a | written into the book folder |
| Download link page (blocked download) | output folder; deleted when the book finishes | n/a | book folder; deleted when the book finishes |
| Diagnostic error page | global logs folder | n/a | `.book-loader/logs/` (both cleaned after 30 days, §9.11) |
| Workspace temp folder | always removed | n/a | always removed |

More rules:
- **No output over its own input.** An output path that resolves to its own input (for example `convert book.epub -o book.epub`) is refused.
- **`convert` and `optimize` write new files.** They write `book.pdf` and `book.optimized.epub` by default and never replace the source. An existing output goes through the conflict policy.
- **In a library, `[output] formats` decides which DRM-free files are kept.** `--to-pdf` adds `"pdf"` for that run. `--keep-epub` has no effect there, because the EPUB is kept whenever `"epub"` is listed.
- **Pending licenses** stay in their auth folder (§5.7) until the book finishes or `pending clear` removes them.
- **`--keep-encrypted` changes meaning.** Today it also keeps the decrypted EPUB. For one release, `--keep-encrypted --to-pdf` without `--keep-epub` prints a one-line notice saying the EPUB is no longer kept and to add `--keep-epub`.

## 8. `--optimize`

`--optimize` is used during `process` (and Kobo `dedrm`), and also as a standalone `book-loader optimize book.epub [-o out.epub]`. It is **safe by default** and never changes the text. `--no-optimize`, which exists today, turns it off. That matters in a library with `optimize = true`.

**Always, with `--optimize`:**
1. **Remove DRM leftovers:** `META-INF/rights.xml`, `META-INF/encryption.xml` if it only lists removed entries, and Adobe transaction identifiers (`<meta name="Adept.resource">`, `Adept.expected.resource`) in the OPF and XHTML. Those identifiers link the file to your purchase.
2. **Repair the container:** `mimetype` becomes the first entry, stored uncompressed, as the EPUB spec requires. `META-INF/container.xml` must point to an OPF file that exists, and manifest entries whose files are missing are reported.
3. **Recompress** everything else with deflate level 9, and keep the zeroed `external_attr` behaviour.
4. **Report** the size before and after, what was removed, and any warnings about manifest items nothing uses. Those items are reported, never removed automatically.

**Opt-in:**
- `--optimize-images[=QUALITY]` recompresses JPEG and PNG and downscales anything over a maximum size. This is lossy, so it's off by default. It uses Pillow through an optional extra: `pip install book-loader[images]`.

**Other rules:**
- **PDFs** are skipped with a note. PDF optimization could come later, for example with `pikepdf`.
- **In the pipeline**, optimization runs in the workspace before `Store`, so nothing the user supplied is touched. In library mode `optimize = true` in `library.toml` makes it the default, and `book.json` records that the EPUB was optimized.

## 9. Confirmed features

### 9.1 Kobo on Windows
- The default library folder is `%LOCALAPPDATA%\Kobo\Kobo Desktop Edition`. `kobo/paths.py` chooses per OS, and `--source` still overrides it.
- **MAC addresses:** `getmac /fo csv /nh /v`, with PowerShell `Get-NetAdapter | Select MacAddress` as a fallback. `wmic` is not used because it's removed in recent Windows 11 builds. Linux reads `/sys/class/net/*/address`, for Wine installs. Extra addresses come from config or `--mac` (§6).
- The key derivation doesn't change: 4 hash keys × MACs × user IDs.
- **Checking:** the key derivation and decryptor are tested offline (§13). Only the Windows folder and MAC reading need testing on a real Kobo Desktop install.

### 9.2 Batch processing
- `book-loader process A.acsm B.acsm ~/Downloads/acsm/` accepts files and folders, with `--recursive` for folders.
- **Books are processed one after another.** Adobe fulfillment is stateful, and running books in parallel could trip rate limits.
- **Checked before any network call:** unreadable ACSMs and duplicates are listed. So are expired ACSMs, using the `<expiration>` date that the vendored code already reads; they're flagged and skipped unless `--try-expired` is passed.
- **One failure doesn't stop the batch.** A blocked download moves that book to pending (§9.5) and the batch carries on.
- **At the end:** a summary table (✓ done / ⊘ skipped / ⏸ pending / ✗ failed). Then, in a terminal, "3 books need a manual download: handle them now?", which goes through them with watched downloads. Downloads are matched to books automatically where possible (§9.4).
- **Exit codes and output** follow §5.9.
- **Already-processed ACSMs are skipped.** An ACSM already processed into the current library, matched by Adobe resource ID, is skipped. `--force` processes it again from the stored encrypted copy (decrypt, optimize, convert), without fulfilling a second time. Only when no encrypted copy exists is the ACSM fulfilled again, with a warning that the server may refuse.

### 9.3 Improved converter
- `epub_to_pdf_improved.py` becomes the `python` engine. It follows the spine order, keeps the book's CSS, and embeds all resources and page numbers. The WeasyPrint import stays lazy.
- **New options:** `--page-size A4|A5|Letter|…`, `--margin`, `--no-page-numbers`, and `--css FILE` for a user stylesheet.
- **A clear message when WeasyPrint can't run:** if its native libraries are missing, the error says which one is missing, suggests `--convert-engine calibre`, and links the install notes.
- Calibre stays as the other engine, found as described in §6. `info` reports whether each engine can be used.

### 9.4 Watched downloads
When the automatic download is blocked:
1. Show the link panel (§12) and offer to open it with `webbrowser`. Keep avoiding `click.launch`, which splits URLs at `&` on Windows.
2. **Watch the Downloads folder.** On Windows the Known Folder API finds it, even when it has been moved; on Linux the XDG user-dirs config gives the real, possibly translated, folder name. The folder can be overridden with `--downloads-dir` or `[downloads] dir`.
3. **Ignore files that are still downloading:**
   - partial files: `.crdownload`, `.part`, `.download`, `.tmp`
   - empty files
   - any file with a matching `.part` beside it, because Firefox creates an empty file with the final name first
   - files whose size hasn't stayed the same for two checks
4. **Check the file's type as soon as it lands.** An HTML file (the check page) is rejected at once with "that's the check page, download again"; a ZIP or `%PDF` file is considered.
5. **Check it's the right book (`adobe/identity.py`).**
   - An ADEPT EPUB's OPF identifier usually contains the resource UUID from the fulfillment. When it's there, a mismatch is rejected ("this is a different book"). With several books pending, a download is matched to its book automatically.
   - When the file can't be identified (most PDFs, EPUBs without that identifier), the user confirms as today.
6. **The user can type a path at any time.**
   - The prompt uses `prompt_toolkit`, running asynchronously next to the watcher. When a file is found, the prompt is cancelled and the file is offered with "Use `Title.epub`? [Y/n]".
   - Typed names are looked up as today: as a path, then inside Downloads, with `.epub` or `.pdf` added when no extension is given.
   - `q` quits and prints the "finish later" command, as today. `PlainPrompter` (§5.10) falls back to today's line prompt without the live watcher.
7. The existing 60-second allowance for downloads that started just before the prompt stays.

No new dependencies beyond `prompt_toolkit`: the watcher polls once a second.

### 9.5 Pending list
```
book-loader pending list [--all]         table: id, title, saved, age, ACSM expiry, key matches?, link page
book-loader pending open ID              open the download link in the browser
book-loader pending resume [ID] [--file F]   watched-downloads flow, then carries on to decrypt/convert/store
book-loader pending clear [ID | --all | --stale]   --stale = saved under a different key, or older than N days
```
- IDs are the short ACSM hashes used today, and any unique prefix works.
- **Pending files can be resumed without the original ACSM.** The v1 pending record (§11.2) stores the ACSM's contents, the library or output folder, and the output settings.
- `process` without arguments in a library, or `book-loader library status`, points out books that are pending.
- `ManualDownloadRequired`'s message mentions `pending resume` alongside `--downloaded-file`.

### 9.6 rich-click
- `rich-click` gives formatted `--help` output, with options grouped into panels: Output, Retention, Conversion and Advanced.
- It only needs the import changed, and `--help` output is covered by the parity snapshot.

### 9.7 Library loans
Public libraries and some stores lend ebooks through ACSM files. A loan expires on its own, but it can also be **returned early**. Returning frees one of your borrowing slots, and a waiting list moves on sooner. ADE has a "Return borrowed item" button; book-loader has nothing today, because the loan record is dropped (§3).

**How it works, without changing vendored code:**
- **Recording a loan.** When a fulfillment reply says `<returnable>true</returnable>`, `Fulfiller` calls the vendored `updateLoanReturnData(reply, forceTestBehaviour=True)`. That returns the loan record (loan ID, user, device, operator URL, `validUntil`) instead of trying to write Calibre's settings.
- **Where it's stored.** `LoanStore` saves the record to `<auth folder>/loans.json` (mode `0600`), together with the book title, the library, and where the book's files are.
- **Returning a loan.** `loan return` calls the vendored `tryReturnBook(record)` inside `AdeptSession`. That call signs the request with this authorization's key, sends it to the operator, and notifies the server.

**Commands:**
```
book-loader loan list [--all]            title, borrowed, valid until (with time left), library, status
book-loader loan return ID|--expired     return early; asks whether to delete the local copies (default yes)
book-loader loan clear --expired         forget loans past validUntil (they have ended on their own)
```

**Other rules:**
- **Loans are shown as loans.**
  - `process` reports "Loan until 2026-10-12" in its summary.
  - `book.json` records `"loan": {"id": …, "valid_until": …, "returned": false}`.
  - `library list` and `library status` show loans separately.
- **Terms of use.** Loans are decrypted like any other book, as today (decision 11). The project's terms cover books you own, and a borrowed book isn't one, so `process` prints a one-line reminder of this whenever the fulfillment is a loan.
- **Returning deletes the book's files by default.** The files belong to a loan that no longer exists, and book-loader created them, so the retention rule in §7 allows it. The user is asked first, and `--keep-files` keeps them.
- **Backups:** loan records are part of the `auth` backup part, because only that key can return them.
- **Loans from before this version** can't be returned by book-loader, because their records were never kept. They end on their own at their expiry date.

### 9.8 JSON output
- **Where it's available:** `--json` on `info`, `auth info`, `kobo list`, `pending list`, `loan list` and `library list`, alongside `process` (§5.9).
- **What it writes:** exactly one JSON document to stdout, with a top-level `"schema": 1` field so scripts can detect changes. Nothing else goes to stdout.
- **Prompts:** `--json` never prompts. Status messages and warnings still go to stderr.
- **Sensitive data:** it never includes key material, passwords, passphrases or download URLs. Pending entries show their ID and whether a link exists, not the link itself. `pending open` is the way to use a link.
- **Paths:** absolute, as strings. Dates use ISO 8601 with a time zone.
- **Structure:** each command's JSON comes from the same result object its table view uses, so the two can't drift apart.

### 9.9 Adobe ID password input
`auth create --adobe-id` (and any other command needing the Adobe ID password) looks for the password in this order and **only prompts if none is found**:
1. `--password-file F`: the first line of the file.
2. `--password-stdin`: one line read from stdin. It can't be combined with other input on stdin.
3. `BOOK_LOADER_ADOBE_PASSWORD`.
4. `--password PASSWORD`: kept for compatibility with 0.1.0. It prints a warning that the password is visible in shell history and process lists.
5. A terminal prompt, only when prompts are possible (§5.10). Otherwise the command stops and names the options above.

**The terminal prompt:**
- It shows one `*` per character typed, so you can see how much you've typed but never the password.
- It uses prompt_toolkit's password mode with **no history attached**, so the password is never stored in or recalled from prompt history. Pasting works.
- `PlainPrompter` (§5.10) uses `getpass` instead. It shows nothing while you type, because not every terminal can show `*`, and it keeps no history either.
- The password is never logged, even in verbose logs or log files (§9.11). Error messages go through redaction.

**Backup passphrases use the same code** (`infra/secrets.py`): sources in the order given in §10.7, then a masked prompt entered twice.

### 9.10 `library watch`
`book-loader library watch [--inbox] [--downloads]` keeps running and processes new ACSM files as they appear.
- **Sources:** `--inbox` watches the library's `Inbox/`, and `--downloads` watches the Downloads folder (§9.4). Give one, the other, or both. With neither flag, `[watch] sources` in `library.toml` decides; the default is both.
- **Handling each file:**
  - A new `.acsm` is processed through the normal pipeline once it has finished downloading, using the same partial-file rules as §9.4.
  - ACSMs from `Inbox/` are moved into the book folder (decision 13).
  - ACSMs from Downloads are copied, unless `--move-acsm` is given.
- **Blocked downloads** become pending, and watch carries on. They're listed in the live display and can be handled later with `pending resume`.
- **Only one watcher per library,** enforced by the library lock (§5.8). A manual `process` in the same library waits for the watcher to finish its current book.
- **Stopping:** `Ctrl+C` finishes or cleanly abandons the current step, then prints a summary.
- **Display:** a Rich live view showing the watched folders, the queue, the book being processed, and recent results.

### 9.11 Logs
Every run writes a redacted log file, so a problem can be investigated after the fact without `-v`.
- **Where logs go:** the global `logs/` folder (§4.1), or `.book-loader/logs/` when a library is active.
- **The current run** writes `latest-book-loader.log`. When the next run starts, the previous file is renamed `book-loader-<YYYYMMDD-HHMMSS>.log`, using the time that run started, and a new `latest-book-loader.log` is created. The newest log therefore always has the same name.
- **Cleanup:** at startup, log files and diagnostic pages (§5.3) older than 30 days are deleted. The period is set with `[logs] keep_days` in `config.toml` or `library.toml`, and `0` keeps logs forever.
- **Content:** the log always has the full verbose detail: protocol steps, HTTP status codes and redacted URLs. `-v` controls only what the console shows, not what the log records.
  - Everything written to the log goes through `redact_text` / `redact_url`.
  - Passwords, passphrases and keys are never written.
- **Options:**
  - `--log-file PATH` writes an extra copy of this run's log to that path, for bug reports.
  - `--no-log` turns logging off for one run.
- **`book-loader logs`:**
  - `logs path` prints the logs folder
  - `logs show` prints the latest log, with `-n` for the last lines
  - `logs clean` deletes old logs now
- **Two runs at once** (for example `library watch` and a manual command in another library) each write their own file. If `latest-book-loader.log` is locked by another running process, the run writes `latest-book-loader-<pid>.log` instead.
- **Backups:** logs are never included in backups.

### 9.12 Future plans
Not part of this refactor; recorded so the design leaves room for them.
- **`auth import-ade`:** import an existing Adobe Digital Editions authorization (Windows registry, or `activation.dat` on macOS) through the vendored `adobekey`. Until then, an `activation.dat`-only folder is reported as not usable (§6).
- **`auth export-key`:** write the `.der` key for Calibre DeDRM (`exportAccountEncryptionKeyDER`).

Dropped: `auth upgrade` (turning an anonymous authorization into an Adobe ID one).

## 10. Library folders

A library is an ordinary folder that holds everything about a collection: its config, optionally its own authorization, and every file of every book. **Its main purpose is portability.** Because a library can hold its own authorization, the whole library can be backed up to one file and restored later or on another device, with the key needed for its encrypted copies (§10.7).

### 10.1 Layout
```
MyBooks/
├── library.toml                   portable config (§10.2); the file that marks the folder as a library
├── .book-loader/                  mode 0700
│   ├── state.json                 layout version, applied migrations, created-with version
│   ├── local.toml                 settings for this device only (§10.2); never backed up by default
│   ├── lock                       lock file (§5.8)
│   ├── auth/                      local authorization (anonymous by default; optional)
│   │   ├── pending/               pending fulfillments made with this key (§5.7)
│   │   └── loans.json             loans made with this key (§9.7)
│   ├── logs/                      run logs and diagnostic pages (§9.11); never backed up
│   └── backups/                   automatic backups before reset / migrate / restore
├── Inbox/                         drop ACSM files here; `process` with no args or `library watch` picks them up (and moves them)
└── Books/
    └── <Author>/<Title>/          naming template (§5.11)
        ├── book.json              the record (§10.4)
        ├── <Title>.acsm
        ├── <Title>.encrypted.epub
        ├── <Title>.epub
        ├── <Title>.pdf
        └── <Title> - download link.html   only while the download is pending
```
Kobo books go in the same tree, with `source: "kobo"` in `book.json`.

### 10.2 `library.toml` and `local.toml`
Settings are split so a library can move between devices:
- **`library.toml`** holds settings that mean the same on any machine. It goes into backups.
- **`.book-loader/local.toml`** holds paths and anything else specific to one device. It's left out of backups by default, and a restored library starts with an empty one.

`library.toml`:
```toml
version = 1

[auth]
mode = "local"            # local: .book-loader/auth | global: the global auth folder (§4.1) | path (set in local.toml)

[output]
formats = ["epub"]        # DRM-free files to keep; add "pdf" to also convert
convert_engine = "python"
optimize = true
naming = "{author}/{title}"

[keep]
acsm = true
encrypted = true

[conflicts]
policy = "ask"            # ask | overwrite | skip | rename

[watch]
sources = ["inbox", "downloads"]   # §9.10

[logs]
keep_days = 30            # §9.11; 0 = keep forever

[backup.presets.travel]   # saved custom presets (§10.7)
include = ["records", "epub"]
books = "author:Pratchett"
```

`.book-loader/local.toml`:
```toml
[auth]
# path = "D:/auth/adobe"  # only used when library.toml has mode = "path"

[kobo]
# source = "C:/Users/me/AppData/Local/Kobo/Kobo Desktop Edition"
# extra_macs = ["AA:BB:CC:DD:EE:FF"]

[downloads]
# dir = "D:/Downloads"

[conversion]
# calibre_path = "C:/Program Files/Calibre2/ebook-convert.exe"
```
If a device-specific key turns up in `library.toml`, `library verify` warns and offers to move it to `local.toml`.

### 10.3 Finding the library and settings
- **Which library is active**, first match wins:
  1. `--library DIR`
  2. `BOOK_LOADER_LIBRARY`
  3. the nearest `library.toml` in the current folder or a parent, the way git finds `.git`
  4. `default_library` in the global config
  5. none (plain mode)
- **Settings**, first match wins:
  1. CLI flag
  2. environment variable
  3. `.book-loader/local.toml`
  4. `library.toml`
  5. global `config.toml` (§4.1)
  6. built-in default
- **Authorization**, first match wins:
  1. `--auth-dir`
  2. `BOOK_LOADER_AUTH_DIR`
  3. the library's `[auth]`
  4. the global default
- `info` shows which source each value came from, and auth commands print their target (§6).

### 10.4 `book.json`
The per-book sidecar is the only record of the book; there is no central database to fall out of sync. A folder of books can be moved or copied as it is.
```json
{
  "version": 1,
  "metadata": {
    "title": "…", "authors": ["…"], "publisher": "…", "isbn": "9780306406157",
    "language": "en", "year": 2024, "series": null,
    "identifiers": [{ "scheme": "doi", "value": "…" }]
  },
  "source": "adobe",
  "adobe": { "resource": "urn:uuid:…", "acsm_sha256": "…", "fulfilled_at": "…", "auth_fingerprint": "…" },
  "loan": null,
  "files": {
    "acsm":      { "name": "…", "sha256": "…" },
    "encrypted": { "name": "…", "sha256": "…" },
    "epub":      { "name": "…", "sha256": "…", "optimized": true },
    "pdf":       { "name": "…", "sha256": "…", "engine": "python" }
  },
  "added_at": "…", "updated_at": "…"
}
```
- **Duplicates:** the Adobe resource ID catches them. The same ACSM added twice is recognized, and a batch skips it.
- **Speed:** if listing a big library gets slow, a cache index can be rebuilt from these files later.
- **Unicode:** titles and names are stored in NFC.
- **Metadata** comes from the OPF (or the Kobo database) and can be filled in or corrected later. Only the title is required. The ISBN is stored as bare digits after its check digit is verified; a source with an invalid ISBN keeps its other metadata and drops the ISBN. Identifiers other than the ISBN and the Adobe resource ID go in `identifiers`.

### 10.5 Commands
```
book-loader library init [DIR] [--auth local|global|copy-global|move-global]
book-loader library status                 counts, auth, pending, loans, problems
book-loader library list [--format epub|pdf|missing-pdf] [--loans] [--json]
book-loader library watch [--inbox] [--downloads] [--move-acsm]   §9.10
book-loader library import PATH… [--move]  adopt existing EPUB/PDF/ACSM files (§11.4)
book-loader library convert [--missing]    make PDFs (or other formats) where they're missing
book-loader library verify                 check hashes, missing files, orphan files, invalid book.json
book-loader library backup [-o FILE] [--preset full|restorable|books|custom|<saved>] [--include …] [--exclude …]
                           [--books …] [--save-preset NAME]
                           [--passphrase-file F | --passphrase-stdin] [--no-encrypt | --encrypt]
book-loader library restore ARCHIVE [DIR] [--include …] [--books …] [--merge] [--passphrase-file F | --passphrase-stdin]
book-loader library backup-info ARCHIVE    show an archive's manifest without restoring it
```
Existing commands become library-aware and don't need a second version:
- `process` in a library stores into `Books/`, and with no arguments it processes `Inbox/`. ACSMs given as arguments are copied unless `--move-acsm` is given; ACSMs from `Inbox/` are moved (decision 13).
- `kobo dedrm` in a library stores into `Books/`.
- `pending` and `loan` show the library's entries.

### 10.6 Authorization in a library
- **Why the library holds an authorization:** so that a backup of the library is complete. Encrypted copies, pending licenses and loan returns only work with the key that fetched them. With that key in the library, one archive restores a library that can still decrypt, re-convert, finish pending downloads and return loans on any device.
- **Anonymous authorizations default to living in the library.** An anonymous authorization has no account to recover it from, so the library is the natural place to keep it.
- **Existing global authorization at `library init`.** If a global authorization exists, `library init` asks whether to copy it into the library (the default), move it, use it in global mode, or create a new one. It explains that a new one is a different identity: ACSMs already fulfilled with the other key usually can't be fulfilled with it, and encrypted copies made with the other key can't be decrypted with it.
- **No surprise identities.** In a library with no authorization yet, `process` asks before creating a new anonymous one. With `--no-interactive` it stops with a hint instead. Plain mode keeps today's automatic creation.
- **Adobe ID authorizations default to `global`**, because they're tied to an account rather than to a collection. A backup can still include the global authorization (§10.7).
- **Copying doesn't use up activations.** Restoring an authorization on another device copies the existing activation instead of making a new one, so it doesn't count against Adobe ID activation limits. The original tool's `auth backup` and `auth restore` already rely on this.
- **Security:**
  - A library with a local authorization contains a private key, so `.book-loader/` is created with mode `0700`; on Windows the profile's permissions apply (§4.1).
  - `library init` warns if the folder is inside OneDrive, Dropbox, iCloud or Google Drive, and says the key travels with any copy of the library.
  - Backups that include a key are always encrypted unless `--no-encrypt` is given (§10.7).

### 10.7 Library backup and restore
A library backup is **one compressed file** holding the parts the user picks when making it. Restoring can bring back all of it or only some parts, into a new library or into an existing one, on the same device or another.

**Parts.** A backup is made of these parts:

| Part | Contents | In `full` |
|---|---|---|
| `config` | `library.toml` | yes |
| `records` | every selected book's `book.json` | always (small; restore needs them) |
| `auth` | the library's local authorization, with its pending records and loans | yes, if the library has one |
| `global-auth` | the global authorization (with its pending records and loans), for libraries in `global` mode | no |
| `acsm` | ACSM tickets | yes |
| `encrypted` | licensed encrypted books | yes |
| `epub` | DRM-free EPUBs | yes |
| `pdf` | PDFs | yes |
| `local` | `.book-loader/local.toml` (device-specific) | no |

Logs (§9.11) and the lock file are never included.

**Named presets** (decision 18). Most backups use one of these, so the user picks a name instead of ticking parts:
- `full`: everything above except `local` and `global-auth`, for every book.
- `restorable`: the smallest set from which every book can be brought back.
  - It holds `config` + `records` + `auth` + `acsm` + `encrypted`.
  - It also includes the DRM-free file of every book that has no encrypted copy: Kobo books, imported DRM-free books, and books whose encrypted copy wasn't kept. Without these, those books would be lost.
  - It needs the authorization to still work.
- `books`: `records` + `epub` + `pdf` only. DRM-free files that need no key, so it can be shared between your own devices without carrying a private key.

**`custom`, for advanced backups:**
- Choose any parts, including `global-auth` and `local`.
- Choose which books: all, a checkbox list, or a filter such as `--books "author:Pratchett"` or `--books-file list.txt`.
- **Saved presets:** `--save-preset NAME` stores the selection as `[backup.presets.NAME]` in `library.toml`. After that `--preset NAME` repeats it, and saved presets are listed next to the built-in ones.

**Interactive flow** (`library backup` in a terminal):
1. Pick a preset from a list: the built-in presets, any saved presets, and `custom`. Each entry shows its size and file count for this library. `full` is highlighted by default.
2. For `custom` only: a Rich table of parts with sizes, a checkbox list of parts, then an optional book picker, then an offer to save the selection as a preset.
3. The total size, the output path (default `<library name>-<preset>-<YYYYMMDD-HHMMSS>.blbackup`), and a masked passphrase prompt, entered twice, when the backup will be encrypted (§9.9).
4. A progress bar showing bytes written.

**Scripts must say what to back up.** Without a terminal, `library backup` needs `--preset NAME`, or `--include`/`--exclude` (which means `custom`). With neither it stops and lists the presets, rather than guessing. `--include`/`--exclude` combined with a named preset start from that preset and adjust it.

**Encryption (decision 10):**
- **Always on when `auth` or `global-auth` is included.** These parts hold a private key, plus pending records that contain licenses and purchase links. Only `--no-encrypt` writes them unencrypted, with a warning. Other backups can be encrypted with `--encrypt`.
- **Where the passphrase comes from**, first match wins:
  1. `--passphrase-file F`
  2. `--passphrase-stdin`
  3. `BOOK_LOADER_BACKUP_PASSPHRASE`
  4. a masked prompt in a terminal (`*` per character, no history; §9.9)
- **Without a terminal or any of these sources**, the command stops before writing anything and says how to provide one.
- **The same rules apply to `auth backup`, and to the automatic backup before `auth reset`.** `auth reset --yes` in a script therefore needs a passphrase source or `--no-encrypt`. Without one it stops, and nothing is deleted.
- **How it's done:** the key comes from the passphrase through scrypt, and the stream is encrypted with AES-256-GCM, in chunks each with its own tag, so large libraries are never held in memory. `pycryptodome`, which is already a dependency, provides both. A short unencrypted header holds the magic bytes, format version and scrypt settings.

**Archive format** (`infra/archive.py`):
- A tar stream whose **first member is `manifest.json`**, so the archive can be described without reading all of it. The manifest records:
  - format version and book-loader version
  - creation time and library name
  - the parts included
  - for each book: its ID, title and files with size and SHA-256
  - the auth type and key fingerprint, never the key itself
- **Compression:** gzip by default. `--compression xz` is also available, since `lzma` is in the standard library. EPUBs and PDFs are already compressed, so gzip's speed matters more than xz's ratio.
- **The archive is written to a temporary file and renamed at the end,** so a failed backup never leaves a truncated archive under the final name. The file is set to mode `0600`.

**Restore** (`library restore ARCHIVE [DIR]`):
1. Read the header, get the passphrase if needed (same sources as above), and show the manifest: parts, book count, total size, auth type and fingerprint, and the source library name.
2. Let the user pick which of the included parts and books to restore.
3. **Target:** a new folder, which becomes a new library, or an existing library with `--merge`.
   - When merging, books are matched by resource ID, then by NFC-normalised title and author.
   - Files with the same hash are skipped, and other conflicts go through `ConflictPolicy`.
   - `book.json` files are merged, never replaced wholesale.
4. **Authorization:**
   - If the target already has an authorization with a different fingerprint, ask whether to keep it or replace it. The current one is backed up first. A library holds only one authorization.
   - If encrypted copies are restored without a matching key, they're flagged with "N encrypted copies can't be decrypted with this library's authorization".
   - `global-auth` can be installed as the library's local authorization or as the global one.
5. **Safe extraction on any OS:**
   - uses `filter="data"`
   - rejects absolute paths and `..`
   - applies the naming rules of §5.11 (characters, reserved names, length, NFC) and records any new name in `book.json`
   - catches names that collide only by letter case on Windows and macOS
6. **Check the result:** every restored file's SHA-256 is compared with the manifest, and a summary table lists restored, skipped, renamed and failed files.

**Kobo:** Kobo's encrypted files are never included, because decrypting them needs the original computer's network address. Kobo books appear in backups through their DRM-free EPUBs (also in the `restorable` preset).

**How it relates to `auth backup` / `auth restore`:**
- Both keep working. `auth backup` writes the same archive format with only the `auth` part, named `auth_<type>_<timestamp>.blbackup`. It still asks for a folder when `-o` isn't given, defaulting to `~/adobe-ade-auth-bk/`.
- `auth restore` lists both `*.tar.gz` and `*.blbackup` files there. It accepts three kinds of archive: original `.tar.gz` backups (§11.2), new auth-only archives, and full library backups, from which it takes only the authorization.

## 11. Migration from the original version

### 11.1 Principles
- **The new version reads everything the old one wrote, where it is.** Upgrading doesn't force a migration, and nothing breaks before `migrate` runs.
- **`book-loader migrate` never changes anything without showing a plan first.** It shows the plan as a table, asks for confirmation (`--yes` skips the question, `--dry-run` only shows the plan) and makes an encrypted backup in the global `backups/` folder (§4.1) before changing anything. The passphrase comes from the sources in §10.7.
- **Each migration is a small module** in `migrations/` with an ID, a `detect()` that returns actions, and an `apply()`. It can be run again safely: running `migrate` twice does nothing the second time. Applied IDs are recorded in `state.json`.
- **Old data is only deleted by `migrate --cleanup`,** never by `migrate` itself. Until then, reinstalling 0.1.0 still finds its data and won't silently create a new authorization.
- **Old data is noticed cheaply.** When a command finds old data, it prints one dim line suggesting `book-loader migrate`, at most once a day.

### 11.2 What the original version stored, and what happens to it

| Original data | Location | Change | Migration |
|---|---|---|---|
| Authorization (`activation.xml`, `device.xml`, `devicesalt`) on macOS / Linux | `~/.config/book-loader/.adobe/` or `BOOK_LOADER_AUTH_DIR` | **None.** Same format and location | None needed. Optional: `library init --auth copy-global/move-global` |
| Global auth folder on **Windows** (auth + `pending/`) | `C:\Users\<you>\.config\book-loader\.adobe\` | **Moves** to `%LOCALAPPDATA%\book-loader\adobe\` (§4.1); the file formats don't change | `0003_windows_localappdata`: copy to the new folder and check every file's hash. Once the new folder exists, the old one is no longer read; it's deleted only by `migrate --cleanup`. Skipped when `BOOK_LOADER_AUTH_DIR` points somewhere else, since that folder isn't moved |
| Pending fulfillments | `<auth>/pending/<hash16>.json`. Fields: `saved_at`, `acsm` (name only), `key_fingerprint`, `link_file`, `info` | **v1** adds `version`, the ACSM's contents, the ACSM path, the library or output folder, the retention and convert settings, and `title` | `0001_pending_v1`: adds the fields it can fill (`version`, `title` from `info.book_name`) and leaves the ACSM fields empty. `pending resume` asks for the ACSM once, or finds it through `--acsm`. The v1 reader also reads v0 |
| Download link pages | `<output>/.temp/<title> - download link.html`; the path is in the pending record's `link_file` | New pages go to the output folder or book folder (§5.3) | `0004_link_pages`: moves each page still referenced by a pending record out of `.temp/` into its output folder, and updates `link_file` |
| Diagnostic pages | `<output>/.temp/download_error*.html` | New ones go to the output folder or `.book-loader/logs/` | Listed by `migrate --scan`; kept unless the user deletes them |
| Auth backups | `~/adobe-ade-auth-bk/auth_backup_*.tar.gz` and `auth_<type>_*.tar.gz`, unencrypted, top folder named like the auth folder (usually `.adobe`) | New auth backups use the archive format of §10.7: encrypted, with a manifest and mode `0600`. The default folder stays `~/adobe-ade-auth-bk/` | Restore reads both kinds. `0002_backup_permissions` sets old archives to `0600`. `migrate --scan` offers to re-encrypt old archives, which replaces each one only after the encrypted copy has been verified |
| Leftover `.temp/` folders and `*.manual.tmp` files | Output folders from failed or stopped runs | No longer created outside a workspace | `migrate --scan DIR` lists them. Folders that hold a link page or encrypted book for a **still-pending** record are left alone until `0004` has moved the link page. The rest can be deleted or imported into a library |
| Loan records | Never stored (§3) | Stored in `<auth>/loans.json` from now on | None possible: earlier loans can't be returned and end at their expiry date |
| DRM-free books | Wherever `-o` pointed | Can live in a library | `library import` (§11.4) |
| Python 3.10 installs | n/a | The new version needs Python 3.11 or newer | None for data. The README and changelog say so; pip refuses to install on 3.10, so nobody gets a half-working install |
| CLI behaviour in scripts | n/a | `--keep-encrypted` narrowed, and its file now lands next to the output instead of in `.temp/` (§7); `--password` warns (§9.9); `--optimize` now works (§8); `--auth-dir` and `-v` are global (old position still accepted); `auth backup` and the backup in `auth reset` are encrypted (decision 10); `process` prints output paths to stdout (§5.9) | A notice for one release when `--keep-encrypted --to-pdf` is used without `--keep-epub`. A clear error, not a hang, when an encrypted backup has no passphrase source. The changelog lists every change |

### 11.3 Commands
```
book-loader migrate                 detect, show the plan, confirm, back up, apply
book-loader migrate --dry-run       show the plan only
book-loader migrate --scan DIR…     also look for leftovers, link pages, old backups and loose books in these folders
book-loader migrate --status        which migrations are applied and which are pending
book-loader migrate --cleanup       delete old data that has been migrated and verified (e.g. the old Windows folder)
```

### 11.4 Adopting existing books into a library
`library import PATH…` scans files and folders for `.epub`, `.pdf` and `.acsm` files:
- **EPUB:** title and author come from the OPF. Adobe `rights.xml`, if still present, means the file is encrypted: it's kept as the encrypted copy, and decrypted if the matching authorization is available.
- **PDF:** title comes from the metadata, falling back to the file name. An `EBX_HANDLER` means the file is encrypted.
- **ACSM:** matched to a book by the `resource` in its XML, and otherwise by title.
- **Files are grouped into book folders** by resource ID, then by NFC-normalised title and author. The proposed grouping is shown as a table for confirmation first.
- **Copies by default; `--move` moves.** Existing `book.json` files are merged, never overwritten.

## 12. Rich output

**Setup**
- **Two streams.** Status output and prompts go to `Console(stderr=True)`, and results go to stdout (§5.9), so `book-loader kobo list --json | jq` works (§9.8).
- **One log handler.** The same log records feed the console, which shows them only with `-v`, and the log file, which always gets them (§9.11). Both are redacted.
- **Colour follows the environment.** Rich already respects `NO_COLOR` and simplifies output when the terminal isn't interactive. `--no-color` forces plain output.
- **One `Theme`** (`step`, `ok`, `warn`, `err`, `path`, `muted`), so no command hardcodes colours.
- **Fall back to ASCII** (`OK` instead of `✓`) when the console encoding isn't UTF-8, for older Windows consoles.
- **`rich-click`** for help output (§9.6).
- **Wide characters:** Rich measures CJK and other wide characters correctly, so tables line up with Chinese or Japanese titles. Today's hand-padded `kobo list` doesn't.

**`process` pipeline** (replaces `StepReporter` and its hand-written `\r` redraws)
- Each finished step prints a permanent line: `✓ [1/4] Auth detected: Anonymous`.
- The current step is a live `Progress` line with a spinner while fulfilling or notifying.
- **During the download** it switches to `BarColumn` + `DownloadColumn` + `TransferSpeedColumn` + `TimeRemainingColumn`.
- **Conversion** shows `TimeElapsedColumn`, because WeasyPrint can take minutes.
- **Verbose mode:** redacted protocol logs go through `RichHandler` on the same console, so they scroll above the live bar.
- **Redaction stays as it is.** Normal mode shows only the host; verbose shows the redacted URL.
- **Short facts about the book** appear in the summary: format and size (as today's "Done (EPUB, 1.2 MB)"), "Loan until …", and ACSM expiry warnings.

**Errors**
- A red `Panel` titled with the failing step, for example "Step 2/4 failed · Downloading". It shows the redacted message and a dim **Hint** line.
- `--debug` shows `rich.traceback` with `suppress=[click]` and **`show_locals=False`**, because local variables hold private keys and passwords. Exception messages are redacted there too.

**Manual download and pending**
- **Link panel:** a clickable `[link=…]` (Windows Terminal supports these) plus the plain URL for copying, and a reminder not to share the link.
- **While watching:** a `console.status("Watching Downloads for the book… type a path or q")` spinner.
- **`pending list`:** a table with age shown relatively ("3 h ago"), ACSM expiry, and a ✓/✗ column for whether the key matches.

**Tables and summaries**
- **`auth info`:** a key/value grid in a panel with a status badge and the folder's source (flag, environment, library, global).
- **`auth restore`:** a backups table (#, name, type, encrypted?, size, modified) with `IntPrompt.ask`.
- **`kobo list`:** columns that shorten with an ellipsis instead of manual truncation, a DRM badge, a "Downloaded" column and a count caption.
- **`loan list`:** title, valid until with a colour for time left (red under 2 days), library, status.
- **`library watch`:** a live view with the watched folders, the queue, the current book's pipeline steps, and a rolling list of recent results (✓ ⏸ ✗). `Ctrl+C` prints the session summary.
- **Password and passphrase prompts:** prompt_toolkit password mode with a `*` per character and no history (§9.9).
- **`logs show`:** the latest log with Rich's log highlighting; the path of the file is always printed first.
- **Batch `process` and `kobo dedrm`:** an overall progress bar ("Decrypting 3/12"), one line per book (✓ ⊘ ⏸ ✗), then a summary table with the reason for each failure. Kobo books that aren't downloaded still count as failures, as today, but with their own reason: "not downloaded in Kobo Desktop".
- **`optimize`:** a before/after size table and a list of what was removed.
- **`library status`:** a short dashboard with counts per format, pending books, loans, auth mode and verify problems.
- **`migrate`:** the plan as a table (migration, action, target, reversible?), then a checklist of what was applied.
- **`library backup`:** a table of parts with size and file count before the checkbox selection, a running total, and a progress bar showing bytes written. A 🔒 badge shows the archive will be encrypted, and a yellow warning shows when `--no-encrypt` is used with a key included.
- **`library restore` / `backup-info`:** a manifest panel (source library, date, parts, book count, auth type and fingerprint), then a result table of restored, skipped, renamed and failed files.
- **`info`:** a checklist of the version, the authorization, WeasyPrint's native libraries, Calibre (and where it was found), the Kobo library and the active library. Each ✗ gets a fix hint, and each setting shows where its value came from.

**Details to get right**
- **Pause the live display for prompts.** Prompts can't draw while a Rich `Live` display is running, so `RichPrompter` asks `RichReporter.suspend()` before every prompt, including the questionary menus.
- **Keep `questionary`** for the multi-select book picker, the backup-part picker and the conflict menu, because Rich has no multi-select. `PlainPrompter` uses numbered lists instead (§5.10).
- **Tests** use `Console(record=True, width=100)` and `export_text()` for snapshot tests.

## 13. Tests

- **Option parity:** a snapshot of `--help` for every command before the rewrite. The test asserts that no option disappears; new options are allowed. It includes `process -v`, `--optimize/--no-optimize` and `auth reset --yes`, and checks that `-v` and `--auth-dir` work both before and after `process`.
- **Unit tests:**
  - redaction, including the error boundary's redaction of exception messages
  - Kobo key derivation (known vector) and MAC parsing for each OS (fixture output from `ifconfig`, `getmac` and `/sys`), plus `extra_macs`
  - naming (§5.11):
    - plain-mode Kobo names identical to 0.1.0's `safe_filename`
    - deterministic duplicate names
    - NFC, reserved names, length caps
  - `ConflictResolver` and `RetentionPolicy`
  - settings and library precedence
  - download lookup: typed names, Downloads folder, extension added
  - the watcher's rules: partial files, empty files, Firefox `.part`, type sniffing, identity matching
  - terminal detection and prompter selection, including a fake mintty
  - lock files, including stale-lock recovery
  - Calibre discovery with fake install folders
  - password and passphrase sources:
    - the order is file, stdin, environment, `--password`, prompt
    - `--password` prints its warning
    - no prompt without a terminal
    - the prompt is created with password masking and without history
    - the value never appears in logs
  - `--json`: every listed command writes one document with `"schema": 1`, writes nothing else to stdout, and includes no URLs or keys
  - logs:
    - `latest-book-loader.log` is renamed with its start time when the next run begins
    - files and diagnostic pages older than `keep_days` are deleted, and `0` keeps everything
    - a locked `latest-book-loader.log` leads to the `-<pid>` name
    - the log always gets verbose detail, with passwords and URLs redacted
  - `library watch`: `--inbox`, `--downloads`, both, and the `[watch] sources` default; Inbox ACSMs are moved, Downloads ACSMs copied
  - ACSM retention in a library: copied by default, moved from `Inbox/` or with `--move-acsm`, and a move across drives checks the copy first
  - backup presets:
    - each built-in preset gives the right parts
    - `custom` with `--save-preset`, and reusing the saved preset
    - a script with no preset or parts stops
    - `--include` adjusts a named preset
- **Adobe storage:**
  - `PendingStore` reads v0 and v1, rejects a mismatched fingerprint, and saves, loads and clears
  - `LoanStore`: a record captured from a fixture fulfillment reply through `updateLoanReturnData(…, forceTestBehaviour=True)`; `tryReturnBook` against a fake operator server
  - backup and restore round trip into a folder with a different name, and restore of an original `.tar.gz` archive
- **Real decryption, not only fakes:**
  - a synthetic ADEPT EPUB built in the test (RSA key pair, `rights.xml` with the wrapped book key, AES-CBC encrypted entries), decrypted by the vendored `ineptepub` and compared with the plaintext
  - `libpdf.patch_drm_into_pdf` on a tiny PDF, checking `ADEPT_LICENSE` is added
  - a real ADEPT PDF only as a local, git-ignored fixture, marked `live`
- **Kobo end to end, fully offline:** build a synthetic `Kobo.sqlite` (including one in WAL mode) and a KEPUB encrypted with a key derived from a fake MAC. The output must equal the plaintext, including XHTML with a BOM.
- **ACSM pipeline, run for both EPUB and PDF** with fake `Fulfiller` and `Decryptor`:
  - the retention table in §7, row by row
  - `--to-pdf` skipped for a PDF
  - no user file ever deleted or overwritten
  - the link page and diagnostic pages survive workspace cleanup, and the error message points at their new path
  - manual-download loop: wrong file, then right file, then success
  - temp files removed after a failure
  - a non-interactive run fails with "finish later"
  - stdout holds only the output paths (or JSON)
- **Batch:**
  - a mix of success, skip, pending, expired and failure gives the right summary and exit code
  - `--force` decrypts again from the stored encrypted copy without fulfilling
- **Optimize:**
  - the `mimetype` entry comes first and is stored uncompressed
  - Adept meta tags are removed
  - the text content is byte-identical
  - an optimized EPUB still opens in ebooklib
- **Library:**
  - init, including the questions when a global authorization exists
  - import with grouping
  - verify detecting a changed hash
  - the resource-ID duplicate check
  - `local.toml` taking priority over `library.toml`
  - `process` asking before creating a new authorization
- **Library backup and restore:**
  - a round trip of each preset gives identical hashes
  - `restorable` includes the DRM-free files of Kobo and imported books
  - a restore of only some parts or some books
  - merging into an existing library, with conflicts
  - the wrong passphrase fails cleanly, and a tampered chunk fails the GCM check
  - each passphrase source works
  - with no terminal and no passphrase source, nothing is written (and for `auth reset`, nothing is deleted)
  - archives with `..` or absolute paths are rejected
  - Windows-invalid, NFD and case-colliding names are handled
  - `local.toml` is left out by default
  - a failed backup leaves no archive under the final name
  - `auth restore` accepts all three archive kinds
- **Paths:** the Windows global folder comes from `LOCALAPPDATA`, and the old `~/.config` location is used only when the new one is missing. Tested with fake environments on any OS.
- **Migrations:**
  - each migration against fixture data in the original format, applied twice (the second run changes nothing)
  - `--dry-run` changes no files
  - `--scan` leaves folders of pending books alone
  - `--cleanup` deletes only data that was verified
- **`AdeptSession`** against a fake `libadobe` module.
- **Live tests** (Adobe servers, a real Kobo install) are marked `@pytest.mark.network` / `@pytest.mark.live` and skipped by default.
- **Phase 0 realism:** the current code has few places to swap in fakes, so Phase 0 tests work at the level of the CLI and public functions, with `monkeypatch` (for example on `_get_mac_addrs`). That way they survive the rewrite.
- **CI:** GitHub Actions on Windows, macOS and Linux with Python 3.11 and the newest release, running ruff, black `--check`, the type checker and pytest. Paths, MAC addresses, permissions, known folders and console handling differ per OS, so all three are needed.
  - **During the refactor (§2.1):** the same checks run locally on Windows only, under both Python versions. The workflow file is written but not run. Running it on macOS and Linux is the first item in §18.
- `TESTING.md` is replaced by the test suite and a short README section.

## 14. Feature parity checklist

| Current behaviour | New home |
|---|---|
| Creates an anonymous auth if none exists, inside `process` | `EnsureAuth` step (plain mode; a library asks first, §10.6) |
| License storage (`rights.xml` for EPUB / `ADEPT_LICENSE` for PDF) | vendored `apply_license`, called by `Fulfiller` |
| Saved fulfillment + link page + key fingerprint | `adobe/pending.py` (v1, reads v0); link page kept outside the workspace (§5.3) |
| Redacted error page saved when a download returns HTML or HTTP 429, with its path in the message | `Workspace.preserve` (§5.3) |
| ACSM expiry warning | kept; also shown in `pending list` and checked before batches (§9.2) |
| Interactive manual-download loop; `webbrowser`, not `click.launch` | `Acquire` step + `cli/ui/watch.py` |
| Typed name resolved as a path or inside Downloads, `.epub`/`.pdf` added when missing; Enter = newest download since the link was shown (60 s slack) | watched downloads (§9.4) |
| Manual-download prompt only when stdin is a terminal | `cli/ui/terminal.py` (§5.10) |
| "Finish later" command printed, exit 1 | CLI error boundary + `NonInteractivePrompter`; the message also mentions `pending resume` |
| `ManualDownloadRequired` carries reason, URL, link page and ACSM path | `domain/errors.py` |
| Failed PDF conversion keeps the EPUB with a warning | `Convert` step (warning event) |
| `--to-pdf` on a PDF book is skipped | `Convert` step |
| `--optimize/--no-optimize` accepted | kept; `--optimize` now works (§8) |
| `-v/--verbose` on `process`; `--auth-dir` on `process` | global options, still accepted after `process` (§6) |
| Output path printed on success | stdout contract (§5.9) |
| Adobe ID failure removes partial files | `AuthStore` + `AuthService` |
| `auth create` refuses when an authorization exists; prompts for email and password | `AuthService.create` + `Prompter.text/secret`; other password sources are checked first, and the prompt is masked (§9.9) |
| `auth create --password` | kept, with a warning (§9.9) |
| `auth info` shows folder, status, type and Adobe ID email | `auth info` view |
| `auth reset` asks for confirmation (`--yes` skips it), backs up first, then deletes auth + `pending/` | `AuthService.reset`; the backup is encrypted (decision 10); loans are deleted too |
| `auth backup` asks for the folder when `-o` isn't given | kept |
| Backups listed newest first; `--file`, `--backup-dir`, default `~/adobe-ade-auth-bk/` | `adobe/backup.py` + views |
| Kobo database copied without WAL; nested-query cursor rule | `kobo/library.py` (rule kept, with a comment) |
| 4 hash keys × MACs × user IDs tried in turn | `kobo/keys.py` + `kobo/macs.py` |
| Kobo output names from `safe_filename` | `infra/names.py`, identical in plain mode (§5.11) |
| DRM-free Kobo books copied as they are | `kobo/decryptor.py` |
| Kobo books not downloaded count as failures | kept, with their own reason (§12) |
| `--source` for Kobo | kept; also `[kobo] source` in `.book-loader/local.toml` |
| Kobo checkbox menu; `--all` | `Prompter.select_books` |
| `--overwrite` together with `--skip-existing` is an error; single-book yes/no; batch menu with "all" choices and cancel | `ConflictPolicy` + `ConflictResolver` |
| Kobo exits 1 if any book fails | `BatchResult.exit_code` |
| Redacted URLs (host only; redacted full URL in verbose) | `AdeptSession` event mapping + reporter |
| Lazy WeasyPrint import | `conversion/weasyprint_engine.py` |
| `BOOK_LOADER_AUTH_DIR`, default `~/.config/book-loader/.adobe`, mode 0700 | `infra/paths.py` + `infra/settings.py` + `fs.private_dir`; same default on macOS/Linux, `%LOCALAPPDATA%\book-loader\adobe` on Windows (old folder read until migrated) |
| `auth backup` / `auth restore` of `.tar.gz` archives | `infra/archive.py` (auth-only archives); original archives still restore |
| `--convert-engine python\|calibre` on `process` and `convert`; `convert -o` | conversion registry |
| `--version`, `info` (version, auth status, Calibre) | root group, `info` command |

## 15. Phases and releases

One PR each. Every phase leaves the CLI working and the tests passing.

| Phase | Content |
|---|---|
| 0 | **Safety net:** the §13 tests that can run against the current code, the `--help` snapshot, and a local check on Windows with Python 3.11 and the newest release. A CI workflow for three OSes is written but not run until after the refactor (§2.1). Raise `requires-python` to 3.11, update the classifiers, black/ruff targets and dependency groups (§4.1). No other production changes |
| 1 | **Move the vendored files** to `adobe/_vendor/` and `drm/_vendor/` with `git mv`, changing only import paths, and write `PATCHES.md` for each |
| 2 | **`domain/` and `infra/`:** errors with hints, models, the `Reporter` and `Prompter` interfaces, `paths` (`%LOCALAPPDATA%` on Windows, with the old folder as a fallback), `names`, `locks`, `secrets`, `logging` (log files, rotation, 30-day cleanup), `Settings` without side effects, `Workspace` (with `preserve`), `RetentionPolicy`, `ConflictPolicy`, and `archive` (manifest, encryption, safe extraction) |
| 3 | **Adobe adapters:** `AuthStore`, `AdeptSession`, `PendingStore` (v1 plus v0 reader), `LoanStore`, `Fulfiller`, `identity`, `backup` on top of `infra/archive` (restore fixes, original archives) |
| 4 | **`drm/`, `kobo/` (including Windows and `extra_macs`) and `conversion/`** (improved engine, Calibre discovery), with the §6 fixes |
| 5 | **`app/` services and the ACSM step pipeline,** with the retention policy, conflict handling for `process`, and the stdout contract |
| 6 | **`cli/` package with Rich + rich-click,** including terminal detection, the three prompters, masked password input, `--json` on the listed commands, and the `logs` command. Delete `utils/console.py` and the old `cli.py`, then check the parity snapshot |
| 7 | **Pending commands, watched downloads, batch `process`, loans** |
| 8 | **`epub/optimizer.py`,** `--optimize` and the `optimize` command, and the `images` extra |
| 9 | **Library:** layout, `library.toml` + `local.toml`, `book.json`, the `library` commands, the authorization questions (§10.6), ACSM copy/move rules, library-aware `process`, `kobo dedrm`, `pending` and `loan`, and `library watch` |
| 10 | **Library backup and restore:** parts, named presets, `custom` and saved presets, the interactive selection, encryption and passphrase sources, restoring only some parts, merging, cross-OS name handling, `backup-info` |
| 11 | **Migrations:** the `migrate` command, `0001_pending_v1`, `0002_backup_permissions`, `0003_windows_localappdata`, `0004_link_pages`, `--scan`, `--cleanup`, `library import` |
| 12 | **Docs:** README and README.zh-TW, CLAUDE.md (new layout, Python 3.11, `%LOCALAPPDATA%` on Windows, `activation.dat` rule, Kobo no longer "macOS only"), changelog with every behaviour and location change. `ORIGINAL_STRUCTURE.md` stays as the historical snapshot |

**Releases:**
- **0.2.0** = phases 0–6: the same features in the new structure, with the §6 fixes, plus logs, `--json` and masked password input. The one-release notices (§7, §11.2) start here.
- **0.3.0** = phases 7–8: pending, watched downloads, batch, loans, optimize.
- **0.4.0** = phases 9–12: libraries, `library watch`, backups, migrations, docs.
- **Platform caveat (§2.1):** releases made before the §18 checks are done are tested on Windows only. Their changelog says so, and macOS and Linux users are told to expect possible problems.

## 16. Risks

- **`oscrypto` on Linux.** The vendored `libadobe` imports `oscrypto`. Its last PyPI release (1.3.0) has a known bug detecting OpenSSL 3.x versions on some Linux systems ("Error detecting the version of libcrypto"). Windows and macOS use their own crypto libraries and aren't affected. Options:
  - document a workaround
  - pin a fixed upstream commit in development installs
  - later, replace the few `oscrypto` calls through a small shim documented in `PATCHES.md`
  - **Decided 2026-09-29: the shim.**
    - `libadobe` uses only three `oscrypto` functions, all to read the PKCS#12 bundle in `activation.xml`: `keys.parse_pkcs12`, `dump_certificate(cert, encoding="der")` and `dump_private_key(key, None, "der")`. Signing itself already uses `pycryptodome` through `customRSA`.
    - `adobe/pkcs12.py` provides those three names with the same arguments and results. It parses the ASN.1 with `asn1crypto`, derives keys with the PKCS#12 algorithm from RFC 7292 using `hashlib`, and decrypts with `pycryptodome`. No native OpenSSL is loaded, so the Linux bug can't occur.
    - The only vendored change is the two `oscrypto` import lines in `libadobe`, recorded in `PATCHES.md`.
    - While `oscrypto` still works on Windows, tests check that the shim gives byte-identical results to it.
    - No Linux system is available (§2.1), so the shim on Linux is checked afterwards (§18).
    - Found in T1.5 (2026-09-29): Windows is affected by a different `oscrypto` bug. Its pure-Python PKCS#12 key derivation gets the 3DES key wrong for about one salt in 465, so about one Adobe authorization in 465 can't sign requests. The shim derives keys as OpenSSL does, which fixes this too.
- **Windows file locks.** A PDF open in a reader, or antivirus scanning a new file, blocks replacing or deleting it. `fs.py` retries briefly, then reports which file is locked. Workspace cleanup never fails the run over a locked temp file; it warns and leaves the file.
- **macOS and Linux are untested during the refactor (§2.1).** Kobo support works only on macOS today, and nothing confirms it still works there until §18 is done. File permissions, POSIX locks, terminal handling and the Linux Downloads lookup are also untested. The fakes reduce this risk, but real systems can differ from the fakes in ways nobody anticipated.
- **Kobo on Windows is only partly tested.** The key derivation and decryption are tested offline, but the folder layout and MAC reading need a real Kobo Desktop install on Windows (§9.1).
- **Adobe server behaviour.** Fulfilling an ACSM a second time, and early loan returns, depend on the store's server. Both paths report the server's answer clearly rather than assuming success.
- **Always-on logs.** Logs now exist without `-v`, so redaction must be complete. Every log record goes through the redaction functions at the handler, not at each call site. Tests assert that passwords, passphrases, download URLs, UUIDs and emails from fixture runs never appear unredacted. `--no-log` and `keep_days` let privacy-minded users limit what's kept.
- **Scope.** Thirteen phases is a lot. The release split in §15 means 0.2.0 delivers the restructure and fixes even if later phases slip.

## 17. Resolved questions

All questions raised while planning have been answered (2026-09-28); the answers are decisions 12–19 in §1.

| Question | Answer |
|---|---|
| Where `--keep-encrypted` puts the file in plain mode | Next to the output, as `<Title>.encrypted.epub` / `.pdf` (§7) |
| ACSM files in a library | Copied; moved when from `Inbox/` or with `--move-acsm` (§7) |
| `auth import-ade`, `auth export-key` | Future plans (§9.12) |
| `auth upgrade` | Dropped |
| `--json` on listing commands | Added (§9.8) |
| Adobe ID password input | File, stdin and environment first; then a masked prompt with no history (§9.9) |
| `library watch` | Added; Inbox, Downloads, or both (§9.10) |
| Logs | Always kept and redacted; `latest-book-loader.log`; 30-day cleanup (§9.11) |
| Default backup selection | Named presets, plus `custom` with saved presets; scripts must choose (§10.7) |
| DRM removal for loans | Kept, with a reminder of the terms (§9.7) |

New questions found during implementation:

| Question | Found | Status |
|---|---|---|
| `redact_url` keeps the host, even when it is an IP address, such as a home-network address. `redact_text` masks IP addresses elsewhere. Should IP hosts be masked in the new `infra/redact`? | T0.4.1 (2026-09-29); pinned as-is in `golden/redact.json` | **Decided 2026-09-29: keep showing it.** A URL's host stays visible even when it is an IP address; IP addresses elsewhere in text are still masked. The golden values stay as they are |

## 18. Deferred until after the refactor

These checks need macOS, Linux or a remote CI pipeline, none of which is available during the refactor (§2.1). Run them as soon as the platforms are available. Each one may turn up bugs that need fixing. Record any fix in the changelog, and add a test for it that runs on Windows where possible. The item IDs match the "Deferred until after the refactor" list in [REFACTOR_ACTION_PLAN.md](REFACTOR_ACTION_PLAN.md), which has the detail.

| Area | What to check | Items |
|---|---|---|
| CI | Switch on the workflow; the whole suite passes on Windows, macOS and Linux with Python 3.11 and the newest release; the manual network workflow runs | D1 |
| Test fixtures | The shared fixtures and every `posix_only` and `macos_only` test pass on real systems | D2 |
| PKCS#12 shim on Linux | `libadobe` imports and signs through the shim on current Linux distributions, with no `oscrypto` installed | D3 |
| File permissions | Private folders are `0700` and key-holding files `0600` on macOS and Linux | D4 |
| Paths and known folders | `~/.config/book-loader/` on macOS and Linux; the Downloads folder on a real Linux desktop and on macOS | D5 |
| MAC addresses | The `ifconfig` and `/sys` parsers work on real output, not only on the hand-written fixtures | D6 |
| Kobo on macOS | `kobo list` and `kobo dedrm` against a real Kobo Desktop, with output identical to 0.1.0 | D7 |
| Locks and file moves | Stale-lock detection, atomic writes and moves across file systems behave the same on POSIX | D8 |
| Terminal and prompts | Rich output, prompts, masked input and the watched-downloads prompt in macOS and Linux terminals | D9 |
| Conversion | WeasyPrint's native libraries and Calibre discovery on macOS and Linux | D10 |
| Cross-OS backups | A library backed up on macOS restores on Windows and the reverse, with NFD and `:` in names | D11 |
| Manual checks | The macOS or Linux half of the manual release checks | D12 |
