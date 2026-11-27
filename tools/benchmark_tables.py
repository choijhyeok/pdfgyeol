"""Offline structural comparison on two public PDFs."""

import argparse
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import pdfplumber
import pymupdf

from pdfgyeol import parse_pdf


Grid = dict[tuple[int, int], str]


@dataclass(frozen=True)
class Target:
    filename: str
    sha256: str
    page: int
    checks: tuple[tuple[str, str, str], ...]


# Transcribed from rendered pages before inspecting any extractor output.
# Each check is (row label, column header, expected cell text).
TARGETS = (
    Target(
        filename="caltrain.pdf",
        sha256="dd01a3c645da952dd94678bfe0e52fad0135202ef91723f3d30308bc23694ddc",
        page=1,
        checks=(
            ("San Jose Diridon", "101", "4:43a"),
            ("San Jose Diridon", "401", "5:43a"),
            ("San Jose Diridon", "901", "9:13a"),
            ("Palo Alto", "101", "5:10a"),
            ("Palo Alto", "401", "6:10a"),
            ("Palo Alto", "901", "9:40a"),
            ("Millbrae", "101", "5:39a"),
            ("Millbrae", "401", "6:33a"),
            ("Millbrae", "901", "10:09a"),
            ("San Francisco", "101", "6:01a"),
            ("San Francisco", "401", "6:53a"),
            ("San Francisco", "901", "10:31a"),
        ),
    ),
    Target(
        filename="attention.pdf",
        sha256="bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
        page=9,
        checks=(
            ("base", "train steps", "100K"),
            ("base", "PPL (dev)", "4.92"),
            ("base", "BLEU (dev)", "25.8"),
            ("base", "params ×10⁶", "65"),
            ("(E)", "PPL (dev)", "4.92"),
            ("(E)", "BLEU (dev)", "25.7"),
            ("big", "train steps", "300K"),
            ("big", "PPL (dev)", "4.33"),
            ("big", "BLEU (dev)", "26.4"),
            ("big", "params ×10⁶", "213"),
        ),
    ),
)


def pdfgyeol_grids(path: Path, page_number: int) -> list[Grid]:
    page = parse_pdf(path).pages[page_number - 1]
    return [
        {
            (cell.row_index, cell.column_index): page.cell_text(cell.id)
            for row in table.rows
            for cell in row.cells
        }
        for table in page.layout.tables
    ]


def matrix_grid(rows: list[list[str | None]]) -> Grid:
    return {
        (row_index, column_index): value or ""
        for row_index, row in enumerate(rows)
        for column_index, value in enumerate(row)
    }


def pdfplumber_grids(path: Path, page_number: int) -> list[Grid]:
    with pdfplumber.open(path) as pdf:
        return [matrix_grid(table) for table in pdf.pages[page_number - 1].extract_tables()]


def pymupdf_grids(path: Path, page_number: int) -> list[Grid]:
    with pymupdf.open(path) as pdf:
        return [matrix_grid(table.extract()) for table in pdf[page_number - 1].find_tables().tables]


def normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def score(grids: list[Grid], target: Target) -> dict[str, object]:
    normalized_grids = [
        {position: normalize(value) for position, value in grid.items()}
        for grid in grids
    ]
    checks = []
    for name, metric, expected in target.checks:
        row_label = normalize(name)
        column_label = normalize(metric)
        matches = []
        for grid, normalized in zip(grids, normalized_grids):
            rows = [position for position, value in normalized.items() if value == row_label]
            headers = [position for position, value in normalized.items() if value == column_label]
            intersections = {
                (row, column)
                for row, label_column in rows
                for header_row, column in headers
                if header_row < row and label_column < column
            }
            matches.extend(grid.get(position, "") for position in sorted(intersections))
        checks.append({
            "row": name,
            "column": metric,
            "expected": expected,
            "found": matches,
            "correct": len(matches) == 1 and normalize(matches[0]) == normalize(expected),
        })
    return {
        "table_shapes": [
            [
                max((row + 1 for row, _ in grid), default=0),
                max((column + 1 for _, column in grid), default=0),
            ]
            for grid in grids
        ],
        "correct": sum(check["correct"] for check in checks),
        "total": len(checks),
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="directory containing both frozen source PDFs")
    args = parser.parse_args()
    for target in TARGETS:
        path = args.source_dir / target.filename
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            parser.error(str(error))
        if digest != target.sha256:
            parser.error(f"{path} has SHA-256 {digest}, expected {target.sha256}")

    extractors = {
        "pdfgyeol": pdfgyeol_grids,
        "pdfplumber": pdfplumber_grids,
        "PyMuPDF": pymupdf_grids,
    }
    report = {
        "versions": {name: version(name) for name in (*extractors, "pdfminer.six")},
        "documents": [
            {
                "file": target.filename,
                "sha256": target.sha256,
                "page": target.page,
                "extractors": {
                    name: score(extract(args.source_dir / target.filename, target.page), target)
                    for name, extract in extractors.items()
                },
            }
            for target in TARGETS
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
