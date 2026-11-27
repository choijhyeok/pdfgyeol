"""Read an external PDF in place. Print counts only; never copy its content."""

import argparse
from collections import Counter
import json
from pathlib import Path

import pdfplumber

from pdfgyeol import extract_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    document = extract_pdf(args.pdf)
    assert extract_pdf(args.pdf) == document, "Repeated extraction changed records"
    counts = Counter(pages=len(document.pages))
    ids = {document.id}
    with pdfplumber.open(args.pdf) as source:
        assert len(source.pages) == len(document.pages)
        for page, raw in zip(document.pages, source.pages, strict=True):
            assert [f.text for f in page.fragments] == [c["text"] for c in raw.chars]
            assert len(page.vectors) == len(raw.lines) + len(raw.rects) + len(raw.curves)
            assert len(page.images) == len(raw.images)
            page_ids = [page.id] + [
                obj.id for objects in (page.fragments, page.vectors, page.images) for obj in objects
            ]
            assert len(page_ids) == len(set(page_ids))
            assert not ids.intersection(page_ids)
            ids.update(page_ids)
            counts.update(
                fragments=len(page.fragments), vectors=len(page.vectors), images=len(page.images),
            )
            counts[page.kind.value + "_pages"] += 1
            raw.close()
    print(json.dumps({
        "file": args.pdf.name, "extractor": document.extractor_version,
        "counts": dict(counts), "repeat_equal": True, "native_evidence_equal": True,
        "unique_ids": len(ids),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
