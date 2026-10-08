#!/usr/bin/env python3
"""Validate local installation settings without evaluating shell code."""
import argparse
import json
import math
import os
from pathlib import Path
import sys

DEFAULT_CORE = "/Library/Application Support/clash-verge-service/cores/verge-mihomo"


def validate(config):
    if not isinstance(config, dict):
        raise ValueError("configuration must be a JSON object")
    for key in ("target_uid", "core_uid"):
        value = config.get(key)
        if type(value) is not int or value < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    if config["target_uid"] == 0:
        raise ValueError("target_uid must identify the desktop user, not root")
    for key in ("core_path", "api_socket"):
        value = config.get(key)
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError(f"{key} must be an absolute path")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError(f"{key} cannot contain control characters")
    for key in ("poll_interval", "api_timeout"):
        value = config.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0.05 <= value <= 30:
            raise ValueError(f"{key} must be between 0.05 and 30 seconds")
    return config


def read(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("path")
    create = commands.add_parser("create")
    create.add_argument("path")
    create.add_argument("--reuse")
    create.add_argument("--uid", type=int)
    create.add_argument("--core-uid", type=int)
    create.add_argument("--core-path")
    create.add_argument("--api-socket")
    create.add_argument("--poll-interval", type=float)
    create.add_argument("--api-timeout", type=float)
    args = parser.parse_args()
    if args.command == "export":
        config = read(args.path)
        print("\t".join(str(config[key]) for key in
                        ("core_path", "api_socket", "core_uid", "poll_interval", "api_timeout")))
        return
    config = read(args.reuse) if args.reuse and Path(args.reuse).is_file() else {}
    uid = args.uid if args.uid is not None else config.get(
        "target_uid", int(os.environ.get("SUDO_UID", os.getuid()))
    )
    if uid <= 0:
        raise ValueError("cannot identify desktop user; pass --uid with that user's numeric UID")
    config.setdefault("target_uid", uid)
    config.setdefault("core_uid", 0)
    config.setdefault("core_path", DEFAULT_CORE)
    config.setdefault("poll_interval", 0.5)
    config.setdefault("api_timeout", 1.0)
    if args.uid is not None or "api_socket" not in config:
        config["api_socket"] = f"/var/run/clash-verge-service/users/{uid}/verge-mihomo.sock"
    config["target_uid"] = uid
    for key in ("core_uid", "core_path", "api_socket", "poll_interval", "api_timeout"):
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    validate(config)
    Path(args.path).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        sys.exit(2)
