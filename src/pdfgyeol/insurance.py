"""Offline, line-level structure annotations for Korean insurance terms."""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .layout_models import Region, TextLine
from .models import BBox, TextFragment

if TYPE_CHECKING:
    from .api import ParsedPage


@dataclass(frozen=True, slots=True)
class InsuranceSpan:
    """A consecutive run of resolved lines within one existing region."""

    page_id: str
    region_id: str
    line_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InsuranceSection:
    """A heading and its direct body spans in resolved document order."""

    kind: Literal["article", "rider", "appendix"]
    heading: InsuranceSpan
    rider_heading_line_id: str | None
    spans: tuple[InsuranceSpan, ...]


@dataclass(frozen=True, slots=True)
class InsuranceAnnotations:
    sections: tuple[InsuranceSection, ...]


_ARTICLE = re.compile(r"제([0-9]{1,4})조(?:의([0-9]{1,4}))?(\(.+\))")
_APPENDIX = re.compile(r"(?:(?P<open>[\[【(〈])\s*)?별\s*표\s*[0-9]{1,4}(?:\s*[-의]\s*[0-9]{1,4})?")
_APPENDIX_REFERENCE = re.compile(r"(?:에\s*따라|에서\s*정한|참조|(?:의|을|를|에|이|가|은|는|와|과)(?:\s|$))")
_CLOSE = {"[": "]", "【": "】", "(": ")", "〈": "〉"}
_RIDER_END = re.compile(r"(?:특별약관|특약)(?:\([^()]+\))?$")
_RIDER_START = re.compile(r"^(?:[0-9]+(?:[-.][0-9]+)*\.?\s*|[①-⑳]\s*)")
_RIDER_REFERENCE = re.compile(
    r"^(?:(?:이|본|해당|다른|위|아래)(?:특별약관|특약)|(?:회사|계약자|피보험자|보험수익자)는)"
)
_POLICY_CONTEXT = re.compile(
    r"보험(?:금|료|수익자|증권|계약|기간)|피보험자|보통약관|"
    r"(?:손해|생명|상해|화재|종합|운전자|실손|치아)보험"
)
_CONTENTS = re.compile(r"(?:목차|차례|약관목차|약관차례)")
_LEADER = re.compile(r"(?:[.·…⋯ㆍ‧]\s*){3,}(?:[0-9]+\s*)?$")
_LOCATOR = re.compile(r"\s{2,}[0-9]+$")
_INSET = re.compile(r"(?:예시(?:안내)?|사례|용어해설|관련법규)")
_GROUP = re.compile(r"제[0-9]{1,4}(?:관|장|절).+")
_PAGE_NUMBER = re.compile(r"(?:[-–]\s*)?[0-9]+(?:\s*(?:/\s*[0-9]+|[-–]))?")
_RUNNING_LABEL = re.compile(r"(?:특별|보통)?약관[0-9]*")


@dataclass(frozen=True, slots=True)
class _Line:
    line: TextLine
    text: str
    region: Region
    root: bool


@dataclass(slots=True)
class _Section:
    kind: Literal["article", "rider", "appendix"]
    heading: InsuranceSpan
    rider_heading_line_id: str | None = None
    article_number: tuple[int, int] | None = None
    # Each item retains its position so separated runs in one core region stay separate.
    body: list[tuple[str, int, str, str]] = field(default_factory=list)

    def freeze(self) -> InsuranceSection:
        spans: list[InsuranceSpan] = []
        line_ids: list[str] = []
        page_id, region_id, previous = "", "", -1
        for page, position, region, line_id in self.body:
            if line_ids and (page != page_id or region != region_id or position != previous + 1):
                spans.append(InsuranceSpan(page_id, region_id, tuple(line_ids)))
                line_ids.clear()
            page_id, region_id, previous = page, region, position
            line_ids.append(line_id)
        if line_ids:
            spans.append(InsuranceSpan(page_id, region_id, tuple(line_ids)))
        return InsuranceSection(self.kind, self.heading, self.rider_heading_line_id, tuple(spans))


def _compact(text: str) -> str:
    return "".join(text.split())


def _label(text: str) -> str:
    return _compact(text).strip("[]【】()〈〉<>:：")


def _article_number(text: str) -> tuple[int, int] | None:
    match = _ARTICLE.fullmatch(_compact(text))
    if match is None:
        return None
    # The outer parentheses must enclose the entire title. Allow nested ones.
    depth = 0
    title = match[3]
    for char in title[:-1]:
        depth += (char == "(") - (char == ")")
        if depth <= 0:
            return None
    return (int(match[1]), int(match[2] or 0)) if depth == 1 else None


def _appendix_label(text: str) -> re.Match[str] | None:
    match = _APPENDIX.match(text)
    if match is None:
        return None
    tail = text[match.end():]
    opener = match["open"]
    if opener is not None:
        tail = tail.lstrip()
        if not tail.startswith(_CLOSE[opener]):
            return None
        tail = tail[1:]
    elif tail and not tail[0].isspace():
        return None
    title = tail.strip()
    if (_APPENDIX_REFERENCE.match(title)
            or re.search(r"[.!?。“”‘’]|(?:합니다|따릅니다|같습니다|입니다|있습니다|됩니다)$", title)):
        return None
    return match


def _rider_title(text: str) -> bool:
    name = _RIDER_START.sub("", text).strip("[]【】").strip()
    compact = _compact(name)
    match = _RIDER_END.search(compact)
    return bool(
        match and 2 <= match.start() <= 70 and re.search(r"[가-힣]", compact[:match.start()])
        and not _RIDER_REFERENCE.match(compact)
        and not re.search(r"[.!?。;:“”‘’/]|(?:에따른|에서정한|에정한|에서말하는)", compact[:match.start()])
    )


def _lines(page: "ParsedPage") -> list[_Line]:
    owners = {line_id: region for region in page.layout.regions for line_id in region.line_ids}
    roots = set(page.layout.reading_order)
    return [
        _Line(line, unicodedata.normalize("NFC", text.replace("\x00", " ")).strip(),
              owners[line.id], owners[line.id].id in roots)
        for line, text in page._ordered_lines()
    ]


def _has_policy_context(pages: tuple["ParsedPage", ...]) -> bool:
    weak_signal = rider_title = False
    for page in pages:
        for item in _lines(page):
            if not item.root:
                continue
            compact = _compact(item.text)
            for cue in _POLICY_CONTEXT.finditer(compact):
                # A generic article can mention a premium without being policy text.
                if cue[0] not in {"보험금", "보험료"} or _article_number(item.text) is not None:
                    return True
                weak_signal = True
            rider_title = rider_title or _rider_title(item.text)
            if weak_signal and rider_title:
                return True
    return False


def _contains(outer: BBox, inner: BBox, tolerance: float = 0) -> bool:
    return (outer.x0 - tolerance <= inner.x0 and inner.x1 <= outer.x1 + tolerance
            and outer.top - tolerance <= inner.top and inner.bottom <= outer.bottom + tolerance)


def _inside_inset(box: BBox, item: _Line, fragments: dict[str, TextFragment]) -> bool:
    if _contains(box, item.line.bbox, 2):
        return True
    if item.line.bbox.top < box.top - 2 or item.line.bbox.bottom > box.bottom + 2:
        return False
    # Indentation spaces may begin outside the border while all printed glyphs
    # are inside it. Keep the original line ID and use ink only for this check.
    return all(_contains(box, fragment.bbox, 2) for fragment_id in item.line.fragment_ids
               if (fragment := fragments[fragment_id]).text.replace("\x00", "").strip())


def _inset_box(page: "ParsedPage", marker: _Line) -> BBox | None:
    bounds = marker.line.bbox
    size = marker.line.font_size
    shapes = [vector for vector in page.source.vectors if vector.stroke or vector.fill]
    boxes = [
        vector.bbox for vector in shapes
        if (vector.kind == "rect" or (vector.kind == "curve" and vector.path
                                     and vector.path[-1].operator == "h"))
        and _contains(vector.bbox, bounds, size / 4)
        and vector.bbox.bottom - bounds.bottom > size * 2
    ]
    # A label can sit in an opening in the top border. Use the two sides and
    # their shared bottom rule to recover the inset without changing core layout.
    sides = {
        vector.bbox for vector in shapes
        if vector.kind == "line" and vector.bbox.x1 - vector.bbox.x0 <= 1
        and bounds.top - size / 2 <= vector.bbox.top <= bounds.bottom
        and vector.bbox.bottom - bounds.bottom > size * 2
    }
    for vector in shapes:
        border = vector.bbox
        if (vector.kind != "line" or border.bottom - border.top > 1
                or border.top - bounds.bottom <= size * 2
                or border.x0 > bounds.x0 or border.x1 < bounds.x1):
            continue
        left = BBox(border.x0 - 2, bounds.top - size / 2, border.x0 + 2, border.bottom + 2)
        right = BBox(border.x1 - 2, bounds.top - size / 2, border.x1 + 2, border.bottom + 2)
        if (any(_contains(left, side) and abs(side.bottom - border.bottom) <= 2 for side in sides)
                and any(_contains(right, side) and abs(side.bottom - border.bottom) <= 2 for side in sides)):
            boxes.append(BBox(border.x0, bounds.top, border.x1, border.bottom))
    return min(boxes, key=lambda box: (box.x1 - box.x0) * (box.bottom - box.top), default=None)


def _peripheral(page: "ParsedPage", item: _Line,
                previous: _Line | None, following: _Line | None) -> bool:
    box = item.line.bbox
    text = _compact(item.text)
    if not item.root:
        return False
    if box.top >= page.source.height * .88:
        # Page numbers and short product furniture, occasionally on separate lines.
        return bool(_PAGE_NUMBER.fullmatch(item.text) or _RUNNING_LABEL.fullmatch(text) or (
            _POLICY_CONTEXT.search(text) and len(item.region.line_ids) == 1
            and box.x1 - box.x0 < page.source.width * .4 and not re.search(r"[.!?。]", item.text)
            and (previous is None or previous.region.column_path != item.region.column_path
                 or box.top - previous.line.bbox.bottom >= item.line.font_size)
        ))
    return bool(
        box.bottom <= page.source.height * .08 and len(item.region.line_ids) == 1
        and box.x1 - box.x0 < page.source.width * .4 and following is not None
        and following.line.bbox.top - box.bottom >= item.line.font_size
        and (_POLICY_CONTEXT.search(text) or _PAGE_NUMBER.fullmatch(item.text))
    )


def _visually_separate(item: _Line, previous: _Line | None, following: _Line | None,
                       fragments: dict[str, TextFragment], inset_boxes: list[BBox]) -> bool:
    if item.region.kind == "heading":
        return True
    box, size = item.line.bbox, item.line.font_size
    if previous is not None and previous.region.column_path == item.region.column_path:
        if box.top - previous.line.bbox.bottom >= size * .9:
            return True
    else:
        center = (box.x0 + box.x1) / 2
        if any(inset.x0 <= center <= inset.x1 and box.top - inset.bottom >= size * .9
               for inset in inset_boxes):
            return True
    if following is None or following.region.column_path != item.region.column_path:
        return False
    gap = following.line.bbox.top - box.bottom
    if gap < -size * .2:
        return False
    if gap >= size * .9:
        return True

    def font(line: TextLine) -> str:
        return fragments[line.fragment_ids[0]].style.font_name

    return font(item.line) != font(following.line)


def annotate_insurance(pages: tuple["ParsedPage", ...]) -> InsuranceAnnotations:
    """Read resolved lines and attach section references without modifying the pages."""
    if not _has_policy_context(pages):
        return InsuranceAnnotations(())

    sections: list[_Section] = []
    active: _Section | None = None
    rider: _Section | None = None
    last_article: tuple[int, int] | None = None
    for page in pages:
        meaningful = [(index, line) for index, line in enumerate(_lines(page)) if line.text]
        if (not meaningful or any(_CONTENTS.fullmatch(_label(line.text)) for _, line in meaningful[:8])
                or sum(bool(_LEADER.search(line.text)) for _, line in meaningful if line.root) >= 2):
            active = rider = None
            last_article = None
            continue
        fragments = {fragment.id: fragment for fragment in page.source.fragments}
        inset_markers = {item.line.id: _inset_box(page, item) for _, item in meaningful
                         if _INSET.fullmatch(_label(item.text))}
        inset_boxes = [box for box in inset_markers.values() if box is not None]
        # Filled/stroked tabs that run off the page are outside the body.
        width = page.source.width
        margin_tabs = [
            v.bbox for v in page.source.vectors if v.kind == "rect" and (v.fill or v.stroke)
            and (v.bbox.x0 >= width * .88 and v.bbox.x1 >= width
                 or v.bbox.x1 <= width * .12 and v.bbox.x0 <= 0)
        ]
        # Unbounded examples leave the remaining column uncertain. A known
        # containing box supplies a precise end even across multiple regions.
        unbounded_columns: set[tuple[int, ...]] = set()
        previous: _Line | None = None
        for index, (position, item) in enumerate(meaningful):
            text = item.text
            column = item.region.column_path
            if item.line.id in inset_markers:
                if inset_markers[item.line.id] is None and item.root:
                    unbounded_columns.add(column)
                    active = rider = None
                    last_article = None
                continue
            if (column in unbounded_columns or (not item.root and unbounded_columns)
                    or any(_inside_inset(box, item, fragments) for box in inset_boxes)
                    or any(_contains(box, item.line.bbox, 2) for box in margin_tabs)
                    or (item.root and _LEADER.search(text))):
                continue
            following = meaningful[index + 1][1] if index + 1 < len(meaningful) else None
            if item.root:
                # Plain contents entries can end in a page locator without leaders.
                if (locator := _LOCATOR.search(text)) and (
                    _article_number(text[:locator.start()]) is not None
                    or _appendix_label(text[:locator.start()]) is not None
                    or _rider_title(text[:locator.start()])
                ):
                    continue
                article = _article_number(text)
                appendix = _appendix_label(text)
                rider_title = _rider_title(text)
                if (article is None and appendix is None and not rider_title
                        and _peripheral(page, item, previous, following)):
                    continue
                starts_page = previous is None
                separate = _visually_separate(item, previous, following, fragments, inset_boxes)
                previous = item
                heading = InsuranceSpan(page.source.id, item.region.id, (item.line.id,))
                if appendix is not None:
                    active = rider = None
                    last_article = None
                    if separate or (appendix["open"] and starts_page
                                    and item.line.bbox.top <= page.source.height * .18):
                        active = _Section("appendix", heading)
                        sections.append(active)
                    continue
                if rider_title or (separate and _label(text).endswith("보통약관")):
                    active = rider = None
                    last_article = None
                    if (rider_title and separate
                            and (following is None or not _rider_title(following.text))):
                        active = rider = _Section("rider", heading)
                        sections.append(active)
                    continue
                if separate and _GROUP.fullmatch(_compact(text)):
                    active = rider
                    continue
                if article is not None and active is not None and active.kind == "appendix":
                    if separate:
                        active = None
                    continue
                if article is not None:
                    active = None
                    if separate:
                        if last_article is not None and article <= last_article:
                            rider = None
                        last_article = article
                        rider_id = rider.heading.line_ids[0] if rider else None
                        active = _Section("article", heading, rider_id)
                        sections.append(active)
                    continue
            if active is not None:
                active.body.append((page.source.id, position, item.region.id, item.line.id))

    return InsuranceAnnotations(tuple(section.freeze() for section in sections))
