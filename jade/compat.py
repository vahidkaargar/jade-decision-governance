"""`jev` compatibility shim over `jade ask` (ADR-03): same subcommands, flags and output line as the
original script, with journaling, redaction, dedup and the new exit codes underneath."""

from __future__ import annotations

import sys

from .cli import ask_main


def main(argv: list[str] | None = None) -> int:
    return ask_main(sys.argv[1:] if argv is None else argv, prog="jev")


if __name__ == "__main__":
    sys.exit(main())
