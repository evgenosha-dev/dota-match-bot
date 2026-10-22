from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DOTA_FOLDER_NAME = "dota 2 beta"
STEAM_ROOTS = (
    Path("C:/Program Files (x86)/Steam"),
    Path("C:/Program Files/Steam"),
    Path("C:/Steam"),
    Path("D:/Steam"),
    Path("D:/SteamLibrary"),
    Path("E:/SteamLibrary"),
    Path("E:/Steam"),
)
_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')
_STEAMID_RE = re.compile(r"/profiles/(\d{17})")


def _library_folders(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.exists():
        return libraries
    try:
        content = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return libraries
    for match in _PATH_RE.finditer(content):
        candidate = Path(match.group(1).replace("\\\\", "\\"))
        if candidate not in libraries:
            libraries.append(candidate)
    return libraries


def _registry_steam_roots() -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg

    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
    )
    roots: list[Path] = []
    for hive, key_name in locations:
        try:
            with winreg.OpenKey(hive, key_name) as handle:
                for value_name in ("SteamPath", "InstallPath"):
                    try:
                        value, _ = winreg.QueryValueEx(handle, value_name)
                    except OSError:
                        continue
                    if value:
                        roots.append(Path(os.path.expandvars(str(value))))
        except OSError:
            continue
    return roots


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    for root in [*STEAM_ROOTS, *_registry_steam_roots()]:
        if root not in roots:
            roots.append(root)
    return roots


def find_dota_dir() -> Path | None:
    """Ищет каталог установки Dota 2 в библиотеках Steam (реестр + стандартные пути)."""
    for steam_root in _steam_roots():
        if not steam_root.exists():
            continue
        for library in _library_folders(steam_root):
            dota_dir = library / "steamapps" / "common" / DOTA_FOLDER_NAME
            if dota_dir.exists():
                return dota_dir
    return None


def steamid_from_url(url: str) -> str | None:
    """Достаёт steamid64 из ссылки вида .../profiles/76561198000000000."""
    match = _STEAMID_RE.search(url.strip())
    if match:
        return match.group(1)
    digits = re.findall(r"\b\d{17}\b", url)
    return digits[0] if digits else None
