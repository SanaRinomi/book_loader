# Vendored code: DeDRM tools (noDRM)

The six Python files here come from the DeDRM plugin for Calibre, as maintained by noDRM. This
file records where they come from and every local change, so an upstream update can be diffed
and the changes reapplied. Update it, and `MANIFEST.sha256`, in the same commit as any change
to a vendored file.

`tests/unit/test_vendor_manifest.py` fails when a file here no longer matches
`MANIFEST.sha256`. After an intended change, run
`uv run python tests/tools/update_vendor_manifest.py`. The hashes ignore CRLF line endings,
and this file is not hashed.

## Upstream

| | |
| --- | --- |
| Repository | <https://github.com/noDRM/DeDRM_tools>, folder `DeDRM_plugin/` |
| Pinned commit | `7379b453199ed1ba91bf3a4ce4875d5ed3c309a9` (2024-11-10, "Remove future import from ion.py") |
| License | GPL v3. `ineptepub.py`, `ineptpdf.py`, `adobekey.py` and `utilities.py` declare `__license__ = 'GPL v3'`, and the plugin's `__init__.py` says it is released under the GNU GPL version 3. `argv_utils.py` and `zeroedzipinfo.py` have no header and fall under the plugin's license. The copyright headers ("i♥cabbages, Apprentice Harper et al.", and "noDRM et al." in `ineptpdf.py`) are kept. book-loader is GPL-3.0-or-later |

How the pin was chosen (T1.2.1, 2026-09-29): each file as it was first imported into
book-loader (commit `f84918b`) was compared with every upstream revision of that file. After
the one relative import is undone, all six match upstream exactly. That content hasn't changed
from `5492dcd` (2023-12-21, "More FileOpen fixes") to the upstream head, `7379b45`, which is
the pin. The newest release, `v10.0.9` (2023-08-02), is older: `ineptepub.py`, `ineptpdf.py`
and `adobekey.py` have changed since.

There are no upstream changes to these files after the pin.

## Comparing with upstream

```sh
git clone https://github.com/noDRM/DeDRM_tools
git -C DeDRM_tools show 7379b45:DeDRM_plugin/ineptepub.py \
  | diff -u --strip-trailing-cr - src/book_loader/drm/_vendor/ineptepub.py
```

## ineptepub.py

- Relative import: `from zeroedzipinfo import ZeroedZipInfo` → `from .zeroedzipinfo import ...`.
  Upstream already imports `.utilities` and `.argv_utils` relatively.

## ineptpdf.py, adobekey.py, utilities.py, argv_utils.py, zeroedzipinfo.py

Unchanged.

## What book-loader uses

`core/drm/remover.py` calls `decryptBook()` from `ineptepub.py` and `ineptpdf.py`. These
import `utilities.py` and `argv_utils.py`, and `ineptepub.py` also imports `zeroedzipinfo.py`.
Nothing imports `adobekey.py`, which extracts an installed Adobe Digital Editions' keys (from
the Windows registry, or from `activation.dat` on macOS). It is kept as upstream ships it.
