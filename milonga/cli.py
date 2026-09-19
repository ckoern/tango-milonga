"""Command line entry point."""

import argparse
import sys
from collections.abc import Sequence

from milonga.ui.app import AppOptions, run
from milonga.ui.navigator import Scope
from milonga.ui.theme import Theme


def parse_args(argv: Sequence[str] | None = None) -> AppOptions:
    parser = argparse.ArgumentParser(
        prog="milonga", description="Administration console for Tango Controls"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        default=True,
        help="use the in-memory demo control system (default while no PyTango backend exists)",
    )
    parser.add_argument(
        "--tango-host", default=None, help="control system to connect to, host:port"
    )
    parser.add_argument("--read-only", action="store_true", help="refuse every write")
    parser.add_argument(
        "--theme", choices=[theme.value for theme in Theme], default=Theme.DARK.value
    )
    parser.add_argument(
        "--scope",
        choices=[scope.name.lower() for scope in Scope],
        default=Scope.DEVICES.name.lower(),
        help="navigator scope to start in",
    )
    parser.add_argument("open", nargs="*", help="devices or servers to open at startup")
    namespace = parser.parse_args(argv)
    return AppOptions(
        demo=namespace.demo or namespace.tango_host is None,
        tango_host=namespace.tango_host,
        read_only=namespace.read_only,
        theme=Theme(namespace.theme),
        scope=Scope[namespace.scope.upper()],
        open=tuple(namespace.open),
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
