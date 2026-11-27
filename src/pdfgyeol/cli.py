"""Installed pdfgyeol command; atomic file output, never overwrite existing paths."""

import argparse
import os
from pathlib import Path
import sys
import tempfile
from collections.abc import Sequence

from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from .api import parse_pdf
from .decision import DecisionConfig


class _OutputError(ValueError):
    """A static output-path diagnostic safe to print."""


def _check_output(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise _OutputError("output must differ from input")
    if destination.exists() and os.path.samefile(source, destination):
        raise _OutputError("output must differ from input")
    # Reject even dangling symlinks, directories and existing empty files.
    if os.path.lexists(destination):
        raise _OutputError("output already exists. Choose a new path.")
    if not destination.parent.is_dir():
        raise _OutputError("output parent directory must exist")


def _write_output(destination: Path, data: str) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=destination.parent, prefix=".pdfgyeol-",
                                         delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic create-if-absent. Unlike replace(), this cannot clobber a file
        # created during parsing, including a new symlink/hardlink to the input.
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise _OutputError("output already exists. Choose a new path.") from None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdfgyeol", description="Parse a local PDF into versioned JSON.")
    parser.add_argument("input", type=Path, help="local PDF path")
    parser.add_argument("-o", "--output", type=Path, help="new JSON file; parent must exist (default: stdout)")
    parser.add_argument("--decision", action="store_true", help="opt in to bounded remote Decision scoring")
    parser.add_argument("--insurance", action="store_true", help="annotate Korean insurance sections locally")
    args = parser.parse_args(argv)
    try:
        if not args.input.is_file():
            print("pdfgyeol: input must be an existing local PDF file", file=sys.stderr)
            return 1
        if args.output is not None:
            _check_output(args.input, args.output)
        document = parse_pdf(args.input, decision_config=DecisionConfig(enabled=args.decision),
                             insurance=args.insurance)
        data = document.to_json()
        if args.output is None:
            # Serialize fully before writing. Explicit UTF-8 even under ASCII locale.
            sys.stdout.buffer.write(data.encode("utf-8"))
            sys.stdout.buffer.flush()
        else:
            _write_output(args.output, data)
        reasons = sorted({record.reason for page in document.pages for record in page.decisions
                          if record.reason in {"not_configured", "provider_failure", "invalid_answer"}})
        if reasons:
            print("pdfgyeol: Decision deferred: " + ", ".join(reasons), file=sys.stderr)
        return 0
    except BrokenPipeError:
        return 1
    except _OutputError as exc:
        print(f"pdfgyeol: {exc}", file=sys.stderr)
        return 1
    except PdfminerException as exc:
        if exc.args and isinstance(exc.args[0], PDFPasswordIncorrect):
            print("pdfgyeol: encrypted PDF requires the Python API password argument", file=sys.stderr)
        else:
            print("pdfgyeol: could not parse the input PDF", file=sys.stderr)
        return 1
    except Exception as exc:
        # Parser/provider errors may contain content or credentials. Never echo them.
        print(f"pdfgyeol: failed ({type(exc).__name__}); check input, password and output path", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
