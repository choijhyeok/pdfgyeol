"""Write an original one-page PDF using only the Python standard library."""

import argparse
from pathlib import Path


def text(x: int, y: int, value: str, size: int = 11) -> str:
    escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    font = "F2" if size > 11 else "F1"
    return f"BT /{font} {size} Tf {x} {y} Td ({escaped}) Tj ET"


def make_pdf() -> bytes:
    left = [
        "Morning fog covered the west bank.",
        "The footpath opened after sunrise.",
        "Two herons stood near the reeds.",
        "The west team finished at nine.",
    ]
    right = [
        "Wind picked up on the east bank.",
        "The team walked past the bridge.",
        "They noted ripples by the sluice.",
        "The east team finished at ten.",
    ]
    commands = [text(40, 452, "Riverside field notes", 20)]
    # Draw the right column first so content-stream order differs from reading order.
    for x, column in ((330, right), (40, left)):
        commands.extend(text(x, 406 - 18 * index, line) for index, line in enumerate(column))

    for x in (40, 330, 560):
        commands.append(f"{x} 188 m {x} 263 l S")
    for y in (188, 213, 238, 263):
        commands.append(f"40 {y} m 560 {y} l S")
    for index, (bank, depth) in enumerate((("Bank", "Depth (cm)"), ("West", "42"), ("East", "36"))):
        y = 247 - 25 * index
        commands.extend((text(48, y, bank), text(338, y, depth)))

    stream = "\n".join(commands).encode("ascii") + b"\n"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 500] /Contents 6 0 R "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
    )
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, content in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(b"%d 0 obj\n%s\nendobj\n" % (index, content))
    start_xref = len(pdf)
    pdf.extend(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        pdf.extend(b"%010d 00000 n \n" % offset)
    pdf.extend(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
               % (len(objects) + 1, start_xref))
    return bytes(pdf)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="PDF path to create")
    with parser.parse_args().output.open("xb") as output:
        output.write(make_pdf())
