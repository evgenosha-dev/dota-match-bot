from pathlib import Path

from dota_bot import paths


def test_find_dota_dir_in_steam_root(tmp_path: Path, monkeypatch):
    steam = tmp_path / "Steam"
    dota = steam / "steamapps" / "common" / "dota 2 beta"
    dota.mkdir(parents=True)
    monkeypatch.setattr(paths, "STEAM_ROOTS", (steam,))
    monkeypatch.setattr(paths, "_registry_steam_roots", lambda: [])

    assert paths.find_dota_dir() == dota


def test_find_dota_dir_in_library_from_vdf(tmp_path: Path, monkeypatch):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    library = tmp_path / "Games"
    dota = library / "steamapps" / "common" / "dota 2 beta"
    dota.mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % library.as_posix(),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "STEAM_ROOTS", (steam,))
    monkeypatch.setattr(paths, "_registry_steam_roots", lambda: [])

    assert paths.find_dota_dir() == dota


def test_find_dota_dir_returns_none_when_missing(tmp_path: Path, monkeypatch):
    steam = tmp_path / "Steam"
    steam.mkdir()
    monkeypatch.setattr(paths, "STEAM_ROOTS", (steam,))
    monkeypatch.setattr(paths, "_registry_steam_roots", lambda: [])

    assert paths.find_dota_dir() is None


def test_steamid_from_url():
    assert (
        paths.steamid_from_url("https://steamcommunity.com/profiles/76561198349584413/")
        == "76561198349584413"
    )
    assert paths.steamid_from_url("76561198349584413") == "76561198349584413"
    assert paths.steamid_from_url("https://steamcommunity.com/id/bflyone/") is None
