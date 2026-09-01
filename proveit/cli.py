"""Command-line entry point for prove-it."""

import argparse
import json
import sys
from pathlib import Path

from .parse import ParseError
from .runner import RunResult, verify_text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prove-it",
        description="Hold completion claims against real state.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="verify a YAML claims file")
    verify.add_argument("file", help="claims YAML path, or - for stdin")
    verify.add_argument("--json", action="store_true", dest="as_json",
                        help="emit one machine-readable JSON document")
    return parser


def _read(path: str) -> tuple[str | None, list[ParseError]]:
    if path == "-":
        return sys.stdin.read(), []
    try:
        return Path(path).read_text(encoding="utf-8"), []
    except (OSError, UnicodeError) as exc:
        return None, [ParseError(f"could not read claims file: {exc}")]


def _render_human(result: RunResult, source: str) -> str:
    if result.errors:
        return "\n".join(error.render(source) for error in result.errors)
    return "\n".join(
        f"{item.verdict.status} {item.claim.label()} -- "
        f"{item.verdict.evidence}"
        for item in result.results
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "verify":
        return 2

    text, read_errors = _read(args.file)
    result = RunResult([], read_errors) if read_errors else verify_text(text or "")

    if args.as_json:
        print(json.dumps(result.as_detail(args.file), indent=2,
                         ensure_ascii=False, default=str))
    else:
        stream = sys.stderr if result.exit_code == 2 else sys.stdout
        print(_render_human(result, args.file), file=stream)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
