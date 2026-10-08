#!/usr/bin/env python3
"""Add or remove this project's PF entries, preserving unrelated file contents."""
import argparse
from pathlib import Path
import re

ANCHOR = "io.github.valdemar-yu.killswitch"
MARKER = "# killswitch managed anchor"
OWN_ENTRY = re.compile(
    r'^[ \t]*(?:anchor[ \t]+"' + re.escape(ANCHOR)
    + r'"(?:[ \t]+quick)?|load[ \t]+anchor[ \t]+"' + re.escape(ANCHOR)
    + r'"[ \t]+from[ \t]+"[^"]+")[ \t]*$'
)


def strip(text):
    # Keep line endings and unrelated blank lines, including files with no
    # final newline. Repeated rollback must not overwrite later admin changes.
    return "".join(
        line for line in text.splitlines(keepends=True)
        if line.strip() != MARKER and not OWN_ENTRY.fullmatch(line.rstrip("\r\n"))
    )


def install(text, seed):
    if not seed.startswith("/") or any(c in seed for c in '\r\n"\0'):
        raise ValueError("invalid seed rules path")
    text = strip(text)
    lines = text.splitlines(keepends=True)
    position = next(
        (i for i, line in enumerate(lines)
         if re.match(r"^[ \t]*(?:anchor|pass|block|antispoof)\b", line)),
        len(lines),
    )
    prefix = "".join(lines[:position])
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += "\n"
    entries = (
        MARKER + "\n"
        + f'anchor "{ANCHOR}" quick\n'
        + f'load anchor "{ANCHOR}" from "{seed}"\n'
    )
    return prefix + entries + "".join(lines[position:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "rollback"))
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--seed")
    args = parser.parse_args()
    text = Path(args.source).read_text(encoding="utf-8")
    if args.action == "install":
        if not args.seed:
            parser.error("--seed is required when installing")
        text = install(text, args.seed)
    else:
        text = strip(text)
    Path(args.target).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
