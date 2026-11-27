"""Original, generated garden/lab PDFs; no external document fixtures."""

from hashlib import sha256
from io import BytesIO

import pdfplumber
import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from pdfgyeol import PageKind, extract_pdf


@pytest.fixture
def garden_pdf(tmp_path):
    path = tmp_path / "garden.pdf"
    canvas = Canvas(str(path), pagesize=(400, 500), invariant=1)
    canvas.setFont("Helvetica-Bold", 18)
    canvas.setFillColorRGB(0.1, 0.2, 0.3)
    canvas.drawString(30, 460, "Garden notebook")
    canvas.setFont("Helvetica", 12)
    canvas.setFillColorRGB(0, 0, 0)
    # Deliberately emit right before left: extraction must not infer reading order.
    canvas.drawString(240, 420, "Right bed")
    canvas.drawString(30, 420, "Left bed")
    canvas.drawString(30, 400, "Repeated  seeds")
    canvas.drawString(30, 400, "Repeated  seeds")  # Real overprint must survive.
    canvas.setLineWidth(0.75)
    for y in (360, 340, 320):
        canvas.line(30, y, 180, y)
    for x in (30, 100, 180):
        canvas.line(x, 320, x, 360)
    canvas.drawString(35, 345, "Peas")
    canvas.drawString(105, 345, "12")
    canvas.drawString(35, 325, "Beans")
    canvas.drawString(105, 325, "8")
    canvas.rect(220, 320, 80, 40, stroke=0, fill=1)
    canvas.setDash(3, 2)
    path_shape = canvas.beginPath()
    path_shape.moveTo(220, 280)
    path_shape.curveTo(240, 300, 260, 260, 280, 280)
    canvas.drawPath(path_shape, stroke=1)
    canvas.showPage()
    canvas.showPage()  # Blank page, distinct from page containing raster.
    image = ImageReader(Image.new("RGB", (8, 6), "green"))
    canvas.drawImage(image, 20, 100, width=80, height=60)
    canvas.rect(18, 98, 84, 64)  # Decorative border must not turn this into blank/text.
    canvas.showPage()
    canvas.line(10, 10, 30, 30)
    canvas.showPage()
    canvas.drawImage(image, 20, 100, width=80, height=60)
    canvas.drawString(30, 300, "Lab scan with caption")
    canvas.showPage()
    canvas.save()
    return path


def test_repeatable_ids_order_and_path_independence(garden_pdf, tmp_path):
    first = extract_pdf(garden_pdf)
    assert extract_pdf(garden_pdf) == first
    copy = tmp_path / "renamed.pdf"
    copy.write_bytes(garden_pdf.read_bytes())
    assert extract_pdf(copy) == first
    assert first.sha256 == sha256(garden_pdf.read_bytes()).hexdigest()
    assert first.schema_version == "1"
    ids = [first.id]
    for page in first.pages:
        ids.append(page.id)
        for objects in (page.fragments, page.vectors, page.images):
            for index, obj in enumerate(objects):
                ids.append(obj.id)
                assert obj.page_id == page.id
                assert obj.page_number == page.page_number
                if objects is page.fragments:
                    assert obj.source_index == index
    assert len(ids) == len(set(ids))
    text = "".join(fragment.text for fragment in first.pages[0].fragments)
    assert text == "Garden notebookRight bedLeft bedRepeated  seedsRepeated  seedsPeas12Beans8"
    # Change only container bytes: all source identities change, even same layout.
    copy.write_bytes(garden_pdf.read_bytes() + b"\n% second revision\n")
    changed = extract_pdf(copy)
    assert changed.id != first.id
    assert changed.pages[0].fragments[0].id != first.pages[0].fragments[0].id


def test_each_native_character_once_with_style_and_coordinates(garden_pdf):
    document = extract_pdf(garden_pdf)
    with pdfplumber.open(garden_pdf) as raw:
        for page, source in zip(document.pages, raw.pages, strict=True):
            assert [fragment.text for fragment in page.fragments] == [c["text"] for c in source.chars]
            assert len(page.fragments) == len(source.chars)
            for fragment, char in zip(page.fragments, source.chars, strict=True):
                assert fragment.bbox.x0 == pytest.approx(char["x0"])
                assert fragment.bbox.top == pytest.approx(char["top"])
                assert fragment.bbox.x1 == pytest.approx(char["x1"])
                assert fragment.bbox.bottom == pytest.approx(char["bottom"])
    title = document.pages[0].fragments[0]
    assert title.bbox.x0 == 30
    assert title.bbox.top == pytest.approx(25.726)
    assert title.style.font_name == "Helvetica-Bold"
    assert title.style.size == 18
    assert title.style.non_stroking_color == (0.1, 0.2, 0.3)
    assert title.style.upright is True
    assert title.source_matrix == (1, 0, 0, 1, 30, 460)
    assert title.advance > 0


def test_raw_table_rulings_shapes_and_bezier_not_duplicated(garden_pdf):
    page = extract_pdf(garden_pdf).pages[0]
    assert [vector.kind for vector in page.vectors] == ["line"] * 6 + ["rect", "curve"]
    horizontal = page.vectors[0]
    assert horizontal.bbox.x0 == 30
    assert horizontal.bbox.top == 140
    assert horizontal.bbox.x1 == 180
    assert horizontal.bbox.bottom == 140
    assert horizontal.line_width == 0.75
    assert horizontal.stroke and not horizontal.fill
    assert horizontal.points == ((30, 140), (180, 140))
    assert page.vectors[6].fill and not page.vectors[6].stroke
    curve = page.vectors[7]
    assert curve.dash == ((3, 2), 0)
    assert curve.path[1].operator == "c"
    assert curve.path[1].points == ((240, 200), (260, 240), (280, 220))
    assert "".join(f.text for f in page.fragments).count("Peas") == 1


def test_blank_image_only_vector_only_and_mixed_pages(garden_pdf):
    pages = extract_pdf(garden_pdf).pages
    assert [page.page_number for page in pages] == [1, 2, 3, 4, 5]
    assert [page.kind for page in pages] == [
        PageKind.TEXT, PageKind.BLANK, PageKind.IMAGE_ONLY, PageKind.VECTOR_ONLY, PageKind.TEXT,
    ]
    assert not pages[1].fragments and not pages[1].images and not pages[1].vectors
    assert not pages[2].fragments
    assert len(pages[2].vectors) == 1
    image = pages[2].images[0]
    assert image.source_size == (8, 6)
    assert (image.bbox.x0, image.bbox.top, image.bbox.x1, image.bbox.bottom) == (20, 340, 100, 400)
    assert image.bits == 8
    assert not image.image_mask
    assert len(pages[4].images) == 1
    assert "".join(f.text for f in pages[4].fragments) == "Lab scan with caption"


def offset_pdf(path, rotation):
    """Hand-built original PDF: offset MediaBox, smaller CropBox, text + line."""
    content = b"BT /F1 12 Tf 50 80 Td (Botany) Tj ET 40 60 m 140 60 l S"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [20 30 320 230] "
         f"/CropBox [40 50 280 210] /Rotate {rotation} "
         f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    pdf = BytesIO(b"%PDF-1.4\n")
    pdf.seek(0, 2)
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(pdf.tell())
        pdf.write(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = pdf.tell()
    pdf.write(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        pdf.write(f"{offset:010d} 00000 n \n".encode())
    pdf.write(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(pdf.getvalue())


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotation_offset_mediabox_and_cropbox_are_page_local(tmp_path, rotation):
    path = tmp_path / "offset.pdf"
    offset_pdf(path, rotation)
    page = extract_pdf(path).pages[0]
    assert page.rotation == rotation
    assert (page.width, page.height) == ((300, 200) if rotation in (0, 180) else (200, 300))
    assert "".join(f.text for f in page.fragments) == "Botany"
    # Anchor coordinates computed directly from PDF coordinates and MediaBox.
    expected_line = {
        0: ((20, 170), (120, 170)),
        90: ((30, 20), (30, 120)),
        180: ((280, 30), (180, 30)),
        270: ((170, 280), (170, 180)),
    }
    assert page.vectors[0].points == expected_line[rotation]
    for fragment in page.fragments:
        assert 0 <= fragment.bbox.x0 <= fragment.bbox.x1 <= page.width
        assert 0 <= fragment.bbox.top <= fragment.bbox.bottom <= page.height
    assert extract_pdf(path) == extract_pdf(path)


def test_io_and_parse_failures_are_not_reported_as_blank(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_pdf(tmp_path / "missing.pdf")
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"not a PDF")
    with pytest.raises(pdfplumber.utils.exceptions.PdfminerException):
        extract_pdf(path)


def test_password_is_forwarded_and_wrong_password_fails(tmp_path):
    path = tmp_path / "private_lab.pdf"
    canvas = Canvas(str(path), encrypt="garden-key", invariant=1)
    canvas.drawString(30, 300, "Seed experiment")
    canvas.save()
    with pytest.raises(pdfplumber.utils.exceptions.PdfminerException):
        extract_pdf(path, password="wrong-key")
    document = extract_pdf(path, password="garden-key")
    assert "".join(f.text for f in document.pages[0].fragments) == "Seed experiment"
