from __future__ import annotations

GAME_MODES = {
    1: "All Pick",
    2: "Captains Mode",
    3: "Random Draft",
    4: "Single Draft",
    5: "All Random",
    16: "Captains Draft",
    18: "Ability Draft",
    22: "All Draft",
    23: "Turbo",
}

LOBBY_TYPES = {
    0: "Normal",
    1: "Practice",
    2: "Tournament",
    4: "Co-op vs Bots",
    5: "Team Match",
    6: "Solo Queue",
    7: "Ranked",
    8: "1v1 Mid",
    9: "Battle Cup",
}

REGIONS = {
    1: "Запад США",
    2: "Восток США",
    3: "Европа",
    5: "Сингапур",
    6: "Дубай",
    7: "Австралия",
    8: "Стокгольм",
    9: "Австрия",
    10: "Бразилия",
    11: "ЮАР",
    12: "Шанхай",
    13: "Китай (Unicom)",
    14: "Чили",
    15: "Перу",
    16: "Индия",
    19: "Япония",
    24: "Гонконг",
    27: "Центр США",
    28: "Варшава",
    31: "Юг США",
    37: "Тайвань",
    38: "Аргентина",
}


def game_mode_label(value: int | None) -> str | None:
    if value is None:
        return None
    return GAME_MODES.get(int(value), f"Режим #{value}")


def lobby_type_label(value: int | None) -> str | None:
    if value is None:
        return None
    return LOBBY_TYPES.get(int(value), f"Лобби #{value}")


def region_label(value: int | None) -> str | None:
    if value is None:
        return None
    return REGIONS.get(int(value), f"Регион #{value}")
