from bot import _resolve_steam_profile


def test_resolve_profile_url():
    steamid, account_id = _resolve_steam_profile(
        "https://steamcommunity.com/profiles/76561198349584413/", "", ""
    )
    assert steamid == "76561198349584413"
    assert account_id == 389318685


def test_resolve_account_id():
    steamid, account_id = _resolve_steam_profile("389318685", "", "")
    assert steamid == "76561198349584413"
    assert account_id == 389318685


def test_resolve_empty_input():
    assert _resolve_steam_profile("", "", "") == (None, None)
