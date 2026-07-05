from __future__ import annotations

from pathlib import Path


def remove_stale_chromium_locks(profile_dir: Path) -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = profile_dir / name
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
        except OSError:
            pass
