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


def _companies(_: argparse.Namespace) -> None:
    from . import companies

    companies.run()


def _match(_: argparse.Namespace) -> None:
    from . import match

    match.run()


def _evaluate(_: argparse.Namespace) -> None:
    from . import match

    match.evaluate()


def _brief(args: argparse.Namespace) -> None:
    from . import briefing

    briefing.run(month=args.month, all_months=args.all_months, model=args.model)


def _all(args: argparse.Namespace) -> None:
    _fetch(args)
    _build(args)
    _audit(args)
    _match(args)


def main() -> None:
    parser = argparse.ArgumentParser(prog="spendsentinel", description=__doc__)
    steps = parser.add_subparsers(dest="step", required=True)
    steps.add_parser("fetch", help="download monthly spend files from GOV.UK").set_defaults(func=_fetch)
    steps.add_parser("build", help="combine, clean and validate the files into DuckDB").set_defaults(func=_build)
    steps.add_parser("audit", help="run the SQL audit tests and write Tableau exports").set_defaults(func=_audit)
    steps.add_parser("companies", help="load the Companies House snapshot into its own DuckDB file").set_defaults(func=_companies)
    steps.add_parser("match", help="match suppliers to Companies House and flag supplier risk").set_defaults(func=_match)
    steps.add_parser("evaluate", help="measure match precision from the hand-checked review sample").set_defaults(func=_evaluate)
    brief = steps.add_parser("brief", help="write a monthly briefing with a local LLM and check every figure")
    brief.add_argument("--month", help="YYYY-MM (default: latest month every department has published)")
    brief.add_argument("--all-months", action="store_true", help="brief every month with full coverage")
    brief.add_argument("--model", default="llama3.2:3b", help="Ollama model name")
    brief.set_defaults(func=_brief)
    steps.add_parser("all", help="fetch, build, audit and match").set_defaults(func=_all)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # print £ correctly on Windows consoles
    args.func(args)


if __name__ == "__main__":
    main()
