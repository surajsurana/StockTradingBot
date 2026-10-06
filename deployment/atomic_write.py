"""
Replacing a JSON file without ever leaving a half-written one on disk.

WHY. `open(path, "w")` truncates immediately and fills the file afterwards, so there is a window --
small, but hit every single run -- in which the file on disk is a prefix of valid JSON and nothing
more. Two things go wrong in that window:

  - a READER (the dashboard builds its state from these files several times a minute) gets a
    JSONDecodeError and, since it had no reason to expect one, took the whole page down with it.
    That is what was behind the dashboard's intermittent, traceback-free failures on 2026-10-06;

  - a CRASH leaves the file permanently truncated. For deployment/state/<pool>/<strategy>/
    portfolio.json that means a book's positions and cash are simply gone, and the engine's next run
    reads whatever survived. There is no second copy.

THE FIX is the standard one: write a sibling temp file, flush it, fsync it, then os.replace() over
the target. os.replace is atomic on POSIX and on Windows, so a reader sees either the whole old file
or the whole new one, never a prefix of either. The fsync matters because os.replace only orders the
rename against the data if that data has actually reached the disk.

This does not make a write transactional across several files, and it is not meant to. It makes a
single file's replacement all-or-nothing, which is what every caller here needs.
"""

import json
import os
from typing import Any


def write_json(path: str, payload: Any, *, indent: int = 2, sort_keys: bool = False) -> None:
    """Replaces `path` with `payload` as JSON, atomically.

    The temp file is a sibling (same directory, so the same filesystem -- os.replace cannot be atomic
    across mounts) and is removed if serialising fails, so a bad payload never leaves litter behind
    and never damages the file that was already there."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent, sort_keys=sort_keys)
            f.flush()
            os.fsync(f.fileno())           # os.replace orders the rename, not the data -- force it
    except BaseException:
        try:
            os.remove(tmp)                 # the existing file is untouched; drop the partial temp
        except OSError:
            pass
        raise
    os.replace(tmp, path)
