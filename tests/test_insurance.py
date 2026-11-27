"""Insurance annotations over generated PDFs via the public API and CLI."""

from collections import Counter

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from pdfgyeol import DecisionConfig, cli, decision, parse_pdf

_FONT = "HYSMyeongJo-Medium"  # Predefined PDF CID font. No external font fixture.


def _pdf(path):
    pdfmetrics.registerFont(UnicodeCIDFont(_FONT))
    return Canvas(str(path), pagesize=(600, 800), invariant=1)


def _put(canvas, y, text, *, x=40, size=11):
    canvas.setFont(_FONT, size)
    canvas.drawString(x, y, text)


def _references(document):
    """Resolve every annotation and check ownership and order against the core."""
    assert document.insurance is not None
    text = {}
    order = {}
    owners = {}
    for page in document.pages:
        for line, value in page._ordered_lines():
            order[line.id] = len(order)
            text[line.id] = value.strip()
        for region in page.layout.regions:
            owners.update({line_id: (page.source.id, region.id) for line_id in region.line_ids})
        assert Counter(page.ordered_fragments()) == Counter(page.source.fragments)

    seen: set[str] = set()
    sections = []
    rider_ids: set[str] = set()
    heading_positions = []
    for section in document.insurance.sections:
        (key,) = section.heading.line_ids
        heading_positions.append(order[key])
        if section.rider_heading_line_id:
            assert section.kind == "article"
            assert section.rider_heading_line_id in rider_ids
        if section.kind == "rider":
            rider_ids.add(key)
        body = []
        last_position = order[key]
        for span in (section.heading, *section.spans):
            assert span.line_ids and not seen.intersection(span.line_ids)
            seen.update(span.line_ids)
            for line_id in span.line_ids:
                assert owners[line_id] == (span.page_id, span.region_id)
                if span is not section.heading:
                    assert order[line_id] > last_position
                    last_position = order[line_id]
                    body.append(text[line_id])
        sections.append((section.kind, text[key], text.get(section.rider_heading_line_id), tuple(body)))
    assert heading_positions == sorted(heading_positions)
    return sections


def test_riders_articles_appendices_and_insets_reuse_core_lines(tmp_path, monkeypatch, capsysbinary):
    path = tmp_path / "신규 약관.pdf"
    canvas = _pdf(path)
    _put(canvas, 730, "목 차", size=18)
    _put(canvas, 680, "교통안전 특별약관 ········ 2")
    _put(canvas, 655, "제1조(보험금의 지급)")
    _put(canvas, 630, "【별표 1】 표 목록")
    canvas.showPage()

    _put(canvas, 700, "교통안전 특별약관", size=16)
    _put(canvas, 670, "제1조(보험금의 지급)")
    _put(canvas, 656, "보험계약에 관한 기록을 작성합니다.")
    _put(canvas, 642, "제9조(기록 확인)에 따라 서식을 작성합니다.")
    _put(canvas, 628, "[별표 3]에 따릅니다.")
    _put(canvas, 614, "회사는 다음 특별약관")
    canvas.showPage()

    canvas.roundRect(46, 645, 260, 105, 5)
    _put(canvas, 730, "【사례】", x=58)
    _put(canvas, 709, "제2조(모의 사례)", x=58, size=16)
    _put(canvas, 687, "가상 특별약관", x=58, size=16)
    _put(canvas, 665, "【별표 9】 예시 표", x=58, size=16)
    _put(canvas, 626, "② 앞 조항의 보충 자료를 첨부합니다.")
    _put(canvas, 593, "① 추가기록 특별약관", size=16)
    _put(canvas, 568, "별도의 보충 자료를 첨부합니다.")
    _put(canvas, 530, "2. 운송기록 특별약관", size=16)
    _put(canvas, 501, "제 1 조 의 2 (기록의 방법(공통))")
    _put(canvas, 487, "① 운송 기록을 작성합니다.")
    _put(canvas, 444, "【 별표 1 】 분류표", size=16)
    _put(canvas, 417, "분류 기준을 따릅니다.")
    canvas.showPage()
    _put(canvas, 710, "세부 항목", x=45)
    for x in (40, 150, 280):
        canvas.line(x, 655, x, 692)
    for y in (655, 692):
        canvas.line(40, y, 280, y)
    _put(canvas, 670, "분류", x=48)
    _put(canvas, 670, "기록", x=159)
    canvas.save()

    def unavailable(*args, **kwargs):
        raise AssertionError("insurance option used the Decision client")

    monkeypatch.setattr(decision.DecisionClient, "from_env", unavailable)
    monkeypatch.setattr(decision, "build_opener", unavailable)
    plain = parse_pdf(path)
    document = parse_pdf(path, insurance=True, provider=object())
    assert document.pages == plain.pages and document.text == plain.text
    assert not document.decision_enabled
    assert plain.insurance is None
    data = document.to_dict()
    assert data.pop("insurance")["sections"]
    assert data == plain.to_dict()
    assert list(plain.to_dict()) == [
        "id", "sha256", "source_schema_version", "extractor_version", "decision_enabled", "pages",
        "schema_version",
    ]
    assert _references(document) == [
        ("rider", "교통안전 특별약관", None, ()),
        ("article", "제1조(보험금의 지급)", "교통안전 특별약관", (
            "보험계약에 관한 기록을 작성합니다.", "제9조(기록 확인)에 따라 서식을 작성합니다.",
            "[별표 3]에 따릅니다.", "회사는 다음 특별약관", "② 앞 조항의 보충 자료를 첨부합니다.",
        )),
        ("rider", "① 추가기록 특별약관", None, ("별도의 보충 자료를 첨부합니다.",)),
        ("rider", "2. 운송기록 특별약관", None, ()),
        ("article", "제 1 조 의 2 (기록의 방법(공통))", "2. 운송기록 특별약관", ("① 운송 기록을 작성합니다.",)),
        ("appendix", "【 별표 1 】 분류표", None, ("분류 기준을 따릅니다.", "세부 항목", "분류", "기록")),
    ]
    article = document.insurance.sections[1]
    assert article.heading.region_id == article.spans[0].region_id
    assert article.heading.page_id != article.spans[-1].page_id
    assert document.pages[3].layout.tables
    assert cli.main([str(path), "--insurance"]) == 0
    assert capsysbinary.readouterr() == (document.to_json().encode("utf-8"), b"")


def test_ambiguous_headings_and_unbounded_insets_leave_text_unassigned(tmp_path):
    path = tmp_path / "uncertain.pdf"
    canvas = _pdf(path)
    _put(canvas, 725, "가상운송 특별약관", size=16)
    _put(canvas, 690, "제1조(보험금의 지급)")
    _put(canvas, 676, "기록을 작성합니다.")
    _put(canvas, 662, "제2조(참고)")
    _put(canvas, 648, "이 줄의 소속은 불명확합니다.")
    _put(canvas, 607, "제3조(서식)")
    _put(canvas, 593, "서식을 작성합니다.")
    _put(canvas, 568, "【사례】")
    _put(canvas, 537, "제4조(예시)")
    _put(canvas, 523, "사례 속 문장입니다.")
    canvas.showPage()
    _put(canvas, 725, "앞쪽을 이어 쓰는 이 줄은 보류합니다.")
    _put(canvas, 670, "제5조(별도 서식)")
    _put(canvas, 656, "별도의 서식을 작성합니다.")
    canvas.save()

    assert _references(parse_pdf(path, insurance=True)) == [
        ("rider", "가상운송 특별약관", None, ()),
        ("article", "제1조(보험금의 지급)", "가상운송 특별약관", ("기록을 작성합니다.",)),
        ("article", "제3조(서식)", "가상운송 특별약관", ("서식을 작성합니다.",)),
        ("article", "제5조(별도 서식)", None, ("별도의 서식을 작성합니다.",)),
    ]


def test_first_article_below_bounded_example_uses_inset_boundary(tmp_path):
    path = tmp_path / "inset-first.pdf"
    canvas = _pdf(path)
    canvas.roundRect(48, 660, 245, 85, 5)
    _put(canvas, 723, "【예시안내】", x=60)
    _put(canvas, 700, "제1조(가상 예시)", x=60)
    _put(canvas, 676, "참고용 문구입니다.", x=60)
    _put(canvas, 631, "제2조(보험계약 기록)")
    _put(canvas, 617, "이 자료를 별도로 보관합니다.")
    canvas.save()
    assert _references(parse_pdf(path, insurance=True)) == [
        ("article", "제2조(보험계약 기록)", None, ("이 자료를 별도로 보관합니다.",)),
    ]


def test_generic_korean_document_has_no_insurance_annotations(tmp_path):
    path = tmp_path / "minutes.pdf"
    canvas = _pdf(path)
    _put(canvas, 730, "동아리 회칙", size=16)
    _put(canvas, 685, "제1조(목적)", size=14)
    _put(canvas, 664, "모임 보험료는 운영비에 포함합니다.")
    _put(canvas, 618, "【별표 1】 활동분류", size=14)
    _put(canvas, 597, "모임과 현장 조사")
    canvas.save()
    parsed = parse_pdf(path, insurance=True)
    assert _references(parsed) == []
    assert parsed.pages == parse_pdf(path).pages
    assert parsed.to_dict()["insurance"] == {"sections": []}


def test_decision_resolved_order_precedes_insurance(tmp_path, monkeypatch, capsysbinary):
    path = tmp_path / "resolved.pdf"
    canvas = _pdf(path)
    _put(canvas, 730, "분류기록 특별약관", size=16)
    _put(canvas, 690, "제1조(보험금의 지급)")
    _put(canvas, 676, "분류 자료는 다음 쪽에 있습니다.")
    canvas.showPage()
    for x, y, text in [(40, 700, "분류"), (340, 700, "설명"), (40, 670, "항목"), (340, 670, "기록")]:
        _put(canvas, y, text, x=x)
    canvas.save()

    class Provider:
        model = "offline test"

        def __init__(self):
            self.requests = []

        def score(self, state, questions):
            self.requests.append((state, questions))
            return {"candidate_0": 0.1, "candidate_1": 0.9}

    provider = Provider()
    config = DecisionConfig(enabled=True)
    local = parse_pdf(path, insurance=True)
    plain = parse_pdf(path, decision_config=config, provider=provider)
    annotated = parse_pdf(path, insurance=True, decision_config=config, provider=provider)
    assert len(provider.requests) == 2 and provider.requests[0] == provider.requests[1]
    assert plain.pages == annotated.pages
    assert annotated.pages[1].decisions[0].outcome == "applied"
    assert _references(local)[1][3][-4:] == ("분류", "항목", "설명", "기록")
    assert _references(annotated)[1][3][-4:] == ("분류", "설명", "항목", "기록")
    monkeypatch.setattr(decision.DecisionClient, "from_env", lambda: provider)
    assert cli.main([str(path), "--insurance", "--decision"]) == 0
    assert capsysbinary.readouterr() == (annotated.to_json().encode("utf-8"), b"")
    assert provider.requests[0] == provider.requests[2]
