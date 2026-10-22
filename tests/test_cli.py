import json
from pathlib import Path

from bot import build_parser


def test_config_before_subcommand_is_kept():
    args = build_parser().parse_args(["--config", "custom.json", "status"])

    assert args.config == "custom.json"
    assert args.command == "status"


def test_config_after_subcommand_is_kept():
    args = build_parser().parse_args(["status", "--config", "custom.json"])

    assert args.config == "custom.json"


def test_config_is_optional():
    args = build_parser().parse_args(["status"])

    assert getattr(args, "config", None) is None


def test_default_command_is_run():
    args = build_parser().parse_args([])

    assert not hasattr(args, "func")


def test_bom_config_is_readable(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"telegram": {"bot_token": "a:b", "chat_id": "1"}}), encoding="utf-8-sig"
    )

    from dota_bot.config import load_config

    config = load_config(path)

    assert config.telegram.bot_token == "a:b"
