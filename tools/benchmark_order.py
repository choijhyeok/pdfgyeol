"""Score anchored reading order on the frozen local PDFs and emit JSON to stdout.

Run with an environment containing installed pdfgyeol, pdfplumber, and PyMuPDF.
The input directory must contain each manifest entry as <id>.pdf.
"""

import argparse
import json
import platform
import unicodedata
from hashlib import file_digest, sha256
from importlib.metadata import version
from itertools import combinations
from pathlib import Path

import pdfplumber
import pymupdf
from pdfgyeol import parse_pdf


MANIFEST = Path(__file__).with_name("reading_order_cases.json")
METHODS = (
    "pdfgyeol",
    "pdfplumber_default",
    "pdfplumber_text_flow",
    "pymupdf_default",
    "pymupdf_sort",
)
NORMALIZATION = (
    "Unicode NFKC and casefold, then remove whitespace and Unicode control and "
    "format characters. Punctuation is unchanged beyond NFKC."
)
MATCHING = (
    "Literal normalized substring matches. Anchor indices are 1-based in visual "
    "order. Zero matches are missing. Repeated matches and overlapping unique "
    "matches are ambiguous. Score a pair only when both anchors are unambiguous."
)


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        character for character in folded
        if not character.isspace() and unicodedata.category(character) not in {"Cc", "Cf"}
    )


def score_page(text: str, anchors: list[str]) -> dict:
    normalized = normalize(text)
    unique = {}
    missing = []
    ambiguous = set()
    for index, anchor in enumerate(anchors, start=1):
        start = normalized.find(anchor)
        if start == -1:
            missing.append(index)
        elif normalized.find(anchor, start + 1) != -1:
            ambiguous.add(index)
        else:
            unique[index] = (start, start + len(anchor))

    for (first, first_span), (second, second_span) in combinations(unique.items(), 2):
        if max(first_span[0], second_span[0]) < min(first_span[1], second_span[1]):
            ambiguous.update((first, second))

    positions = [span[0] for index, span in unique.items() if index not in ambiguous]
    pairs = list(combinations(positions, 2))
    possible = len(anchors) * (len(anchors) - 1) // 2
    correct = sum(first < second for first, second in pairs)
    return {
        "possible_pairs": possible,
        "scored_pairs": len(pairs),
        "correct_pairs": correct,
        "incorrect_pairs": len(pairs) - correct,
        "unscored_pairs": possible - len(pairs),
        "missing_anchor_indices": missing,
        "ambiguous_anchor_indices": sorted(ambiguous),
    }


def extract_texts(path: Path, page_numbers: list[int]) -> dict[str, dict[int, str]]:
    # The public default runs the full parse with Decision disabled.
    parsed = parse_pdf(path)
    if parsed.decision_enabled:
        raise ValueError("pdfgyeol default unexpectedly enabled Decision")
    parsed_pages = {page.source.page_number: page for page in parsed.pages}
    texts: dict[str, dict[int, str]] = {method: {} for method in METHODS}
    with pdfplumber.open(path) as plumber, pymupdf.open(path) as mupdf:
        for number in page_numbers:
            if number not in parsed_pages or number > min(len(plumber.pages), len(mupdf)):
                raise ValueError(f"{path.name} is missing manifest page {number}")
            plumber_page = plumber.pages[number - 1]
            mupdf_page = mupdf[number - 1]
            texts["pdfgyeol"][number] = parsed_pages[number].text
            texts["pdfplumber_default"][number] = plumber_page.extract_text()
            texts["pdfplumber_text_flow"][number] = plumber_page.extract_text(use_text_flow=True)
            texts["pymupdf_default"][number] = mupdf_page.get_text()
            texts["pymupdf_sort"][number] = mupdf_page.get_text(sort=True)
    return texts


def benchmark(directory: Path) -> dict:
    manifest_bytes = MANIFEST.read_bytes()
    cases = json.loads(manifest_bytes)
    inputs = {}
    for name, case in cases.items():
        path = directory / f"{name}.pdf"
        with path.open("rb") as source:
            digest = file_digest(source, "sha256").hexdigest()
        if digest != case["sha256"]:
            raise ValueError(f"{path.name} SHA-256 mismatch: expected {case['sha256']}, got {digest}")
        inputs[name] = path

    totals = {
        method: dict.fromkeys(
            ("possible_pairs", "scored_pairs", "correct_pairs", "incorrect_pairs", "unscored_pairs"), 0,
        ) for method in METHODS
    }
    documents = {}
    for name, case in cases.items():
        anchors_by_page = {
            int(number): [normalize(anchor) for anchor in anchors]
            for number, anchors in case["pages"].items()
        }
        for number, anchors in anchors_by_page.items():
            if number < 1 or len(anchors) != 5 or len(set(anchors)) != 5 or "" in anchors:
                raise ValueError(f"{name} page {number} must have five distinct nonempty anchors")
        extracted = extract_texts(inputs[name], sorted(anchors_by_page))
        pages = {}
        for number, anchors in sorted(anchors_by_page.items()):
            pages[number] = {}
            for method in METHODS:
                score = score_page(extracted[method][number], anchors)
                pages[number][method] = score
                for count in totals[method]:
                    totals[method][count] += score[count]
        documents[name] = {"sha256": case["sha256"], "pages": pages}

    return {
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "versions": {
            "python": platform.python_version(),
            "unicode": unicodedata.unidata_version,
            "pdfgyeol": version("pdfgyeol"),
            "pdfplumber": version("pdfplumber"),
            "pdfminer.six": version("pdfminer.six"),
            "PyMuPDF": version("PyMuPDF"),
            "MuPDF": pymupdf.VersionFitz,
        },
        "normalization": NORMALIZATION,
        "matching": MATCHING,
        "pdfgyeol_decision_enabled": False,
        "documents": documents,
        "totals": totals,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing the SHA-256-pinned PDFs")
    args = parser.parse_args()
    try:
        report = benchmark(args.directory)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
