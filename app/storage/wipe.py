"""Erase everything the app saved on this PC (Settings › Erase everything): each service's session, the
history, preferences, saved searches, the app's own browser profiles (they hold the Google session) and the log.
The app is left as it was the first time. The services' playlists are not touched.

What is in use while the app is still open (the window's folder, `webview`) can't be deleted on Windows:
it is returned in the list and tried again when the app closes (`app/main.py`).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def _close_logs_inside(folder: Path) -> None:
    """Windows won't delete an open file: the log is closed (it reopens on its own when written to)."""
    for h in logging.getLogger().handlers:
        name = getattr(h, "baseFilename", None)
        if name and Path(name).resolve().is_relative_to(folder.resolve()):
            h.close()


def wipe(folder: Path) -> list[str]:
    """Deletes the contents of `folder`. Returns what couldn't be deleted (in use)."""
    folder = Path(folder)
    if not folder.exists():
        return []
    _close_logs_inside(folder)
    left = []
    for item in sorted(folder.iterdir()):
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError:
            # A folder in use may be left half-deleted: what can be deleted is, and the rest is noted
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            if item.exists():
                left.append(item.name)
    return left
