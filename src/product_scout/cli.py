"""CLI entrypoint (spec docs/handoff.md §16; build order step 3 adds only
the `config` subcommand — `research`/`rescore`/`history`/`eval` are added by
their own later build steps, not stubbed here).

    scout config set location.country DE

Wired as the `scout` console script via pyproject.toml's [project.scripts].
"""

from __future__ import annotations

import argparse
import sys

from product_scout.settings import SettingsError, load, save, set_value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scout")
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser(
        "config", help="Read or change ~/.product-scout/config.toml"
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True
    )

    set_parser = config_subparsers.add_parser(
        "set", help="Set a settings key, e.g. location.country DE"
    )
    set_parser.add_argument("key", help="Dotted key, e.g. location.country")
    set_parser.add_argument("value", help="New value")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "config" and args.config_command == "set":
        return _handle_config_set(args.key, args.value)

    # Unreachable: required=True on both subparser levels means argparse
    # itself exits (SystemExit(2)) before main() ever sees an incomplete
    # command line.
    return 1


def _handle_config_set(key: str, value: str) -> int:
    try:
        settings = load()
        updated = set_value(settings, key, value)
        save(updated)
    except SettingsError as exc:
        print(f"scout config set: {exc}", file=sys.stderr)
        return 1
    print(f"Set {key} = {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
