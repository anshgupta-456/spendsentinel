"""Command-line entry point: `spendsentinel <step>`."""

from __future__ import annotations

import argparse
import sys

from .config import load_settings


def _fetch(_: argparse.Namespace) -> None:
    from . import fetch

    fetch.run(load_settings())


def _build(_: argparse.Namespace) -> None:
    from . import build

    build.run()


def _audit(_: argparse.Namespace) -> None:
    from . import audit

    audit.run()


def _all(args: argparse.Namespace) -> None:
    _fetch(args)
    _build(args)
    _audit(args)


def main() -> None:
    parser = argparse.ArgumentParser(prog="spendsentinel", description=__doc__)
    steps = parser.add_subparsers(dest="step", required=True)
    steps.add_parser("fetch", help="download monthly spend files from GOV.UK").set_defaults(func=_fetch)
    steps.add_parser("build", help="combine, clean and validate the files into DuckDB").set_defaults(func=_build)
    steps.add_parser("audit", help="run the SQL audit tests and write Tableau exports").set_defaults(func=_audit)
    steps.add_parser("all", help="fetch, build and audit").set_defaults(func=_all)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # print £ correctly on Windows consoles
    args.func(args)


if __name__ == "__main__":
    main()
