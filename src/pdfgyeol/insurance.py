"""Offline, line-level structure annotations for Korean insurance terms."""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .layout_models import Region, TextLine
from .models import BBox, TextFragment, VectorEvidence

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


_ARTICLE = re.compile(r"제\s*([0-9]{1,4})\s*조(?:\s*의\s*([0-9]{1,4}))?\s*([（(\[【〔])")
_CITATION = re.compile(
    r"^(?:[에의와과을를은는](?:\s|$)|에서|에는|에도|으로|참조|및\s|또는\s|"
    r"제\s*[0-9]+\s*(?:조|항|호|목))"
)
_APPENDIX = re.compile(
    r"(?:(?P<open>[\[【(〈（〔])\s*)?(?:(?:별|부)\s*표"
    r"(?:\s*(?P<number>[0-9]{1,4}|[①-⑳])(?:\s*[-의]\s*[0-9]{1,4})?)?"
    r"|(?P<appendix>부\s*록)(?=\s|[\]】)〉）〕]|$))"
)
_APPENDIX_REFERENCE = re.compile(r"(?:에\s*따라|에서\s*정한|참조|(?:의|을|를|에|이|가|은|는|와|과)(?:\s|$))")
_CLOSE = {"[": "]", "【": "】", "(": ")", "〈": "〉", "（": "）", "〔": "〕"}
_RIDER_END = re.compile(r"(?:특별약관|특약)(?:\([^()]+\))?$")
_RIDER_CODE = re.compile(r"^([0-9]{1,4}(?:[-.][0-9]{1,4})*)(?:[.)]\s*|\s+)")
_RIDER_START = re.compile(r"^(?:[0-9]+(?:[-.][0-9]+)*[.)]?\s*|[①-⑳]\s*)")
_RIDER_REFERENCE = re.compile(
    r"^(?:(?:이|본|해당|다른|위|아래)(?:특별약관|특약)|(?:회사|계약자|피보험자|보험수익자)는)"
)
_POLICY_CONTEXT = re.compile(
    r"보험(?:금|료|수익자|증권|계약|기간)|피보험자|보통약관|"
    r"(?:손해|생명|상해|화재|종합|운전자|실손|치아)보험"
)
_CONTENTS = re.compile(r"(?:(?:.{0,60}약관)?(?:상세)?(?:목차|차례)|특약목록(?:\(가나다순\))?)")
_LEADER = re.compile(r"(?:[.·…⋯ㆍ‧]\s*){3,}(?:[0-9]+\s*)?$")
_LOCATOR = re.compile(r"\s{2,}[0-9]+$")
_INSET = re.compile(r"(?:예시(?:안내)?|사례|용어해설|관련법규)")
_BOX_LABEL = re.compile(r"(?:[【\[〔].{1,80}[】\]〕]|[“‘].{1,60}[”’]\s*(?:이?란)\?)")
_STATUTE_LABEL = re.compile(r"[【\[〔].{1,70}(?:법|법률|령|규정|규칙|조례)[】\]〕]")
_GROUP = re.compile(r"제\s*([0-9]{1,4})\s*([편장절관])(?:\s*\S.*)?")
_SENTENCE_END = re.compile(r"[.!?。]|(?:합니다|따릅니다|같습니다|입니다|있습니다|됩니다)$")
_PAGE_NUMBER = re.compile(r"(?:[-–]\s*)?[0-9]{1,6}(?:\s*(?:/\s*[0-9]{1,6}|[-–]))?")
_NUMBER = re.compile(r"[0-9]+")
_CONTROLS = str.maketrans({code: " " for code in (*range(32), *range(127, 160), 0x200B, 0xFEFF)})
_FONT_SUBSET = re.compile(r"^[A-Z]{6}\+")


@dataclass(frozen=True, slots=True)
class _Line:
    line: TextLine
    text: str
    region: Region
    root: bool


@dataclass(frozen=True, slots=True)
class _Article:
    number: tuple[int, int]
    bracket: str
    inline: bool


_Style = tuple[str, bool, str, int]
_Code = tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    article: _Article
    style: _Style
    separate: bool
    aligned: bool


@dataclass(slots=True)
class _Page:
    page: "ParsedPage"
    lines: list[tuple[int, _Line]]
    candidates: dict[str, _Candidate] = field(default_factory=dict)
    boundaries: set[str] = field(default_factory=set)
    uncertain_insets: set[str] = field(default_factory=set)
    contents: bool = False


@dataclass(slots=True)
class _Section:
    kind: Literal["article", "rider", "appendix"]
    heading: InsuranceSpan
    rider_heading_line_id: str | None = None
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
    return _compact(text).strip("[]【】()〈〉（）〔〕<>:：")


def _article(text: str) -> _Article | None:
    match = _ARTICLE.match(text)
    if match is None:
        return None
    opening = match[3]
    closing = _CLOSE[opening]
    depth = 1
    for end in range(match.end(), len(text)):
        char = text[end]
        depth += (char == opening) - (char == closing)
        if depth == 0:
            tail = text[end + 1:]
            # The particle in a mid-paragraph reference may follow a space
            # produced by the PDF. A body on the same line needs a separator.
            if (not text[match.end():end].strip()
                    or tail and (not tail[0].isspace() or _CITATION.match(tail.lstrip()))):
                return None
            return _Article((int(match[1]), int(match[2] or 0)), opening, bool(tail.strip()))
    return None


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
    elif ((match["number"] is None and match["appendix"] is None)
          or tail and not tail[0].isspace()):
        return None
    title = tail.strip()
    if _APPENDIX_REFERENCE.match(title) or _SENTENCE_END.search(title):
        return None
    return match


def _rider_title(text: str) -> bool:
    if _ARTICLE.match(text):
        return False
    name = _RIDER_START.sub("", text).strip("[]【】").strip()
    compact = _compact(name)
    match = _RIDER_END.search(compact)
    return bool(
        match and 2 <= match.start() <= 70 and re.search(r"[가-힣]", compact[:match.start()])
        and name.count("(") == name.count(")")
        and not _RIDER_REFERENCE.match(compact)
        and not re.search(r"[.!?。;:“”‘’/]|(?:에따른|에서정한|에정한|에서말하는)", compact[:match.start()])
    )


def _rider_code(text: str) -> _Code:
    match = _RIDER_CODE.match(text)
    return tuple(int(part) for part in re.split(r"[-.]", match[1])) if match else ()


def _descends(code: _Code, parent: _Code) -> bool:
    return bool(parent) and len(code) > len(parent) and code[:len(parent)] == parent


def _lines(page: "ParsedPage") -> list[tuple[int, _Line]]:
    owners = {line_id: region for region in page.layout.regions for line_id in region.line_ids}
    roots = set(page.layout.reading_order)
    return [
        (position, _Line(line, clean, owners[line.id], owners[line.id].id in roots))
        for position, (line, text) in enumerate(page._ordered_lines())
        if (clean := unicodedata.normalize("NFC", text.translate(_CONTROLS)).strip())
    ]


def _has_policy_context(pages: list[_Page]) -> bool:
    weak_signal = rider_title = False
    for page in pages:
        for _, item in page.lines:
            if not item.root:
                continue
            compact = _compact(item.text)
            for cue in _POLICY_CONTEXT.finditer(compact):
                # A generic article can mention a premium without being policy text.
                if cue[0] not in {"보험금", "보험료"} or _article(item.text) is not None:
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
               if (fragment := fragments[fragment_id]).text.translate(_CONTROLS).strip())


def _inset_box(shapes: list[VectorEvidence], marker: _Line) -> BBox | None:
    bounds = marker.line.bbox
    size = marker.line.font_size
    boxes = [
        vector.bbox for vector in shapes
        if (vector.kind == "rect" or (vector.kind == "curve" and vector.path
                                     and vector.path[-1].operator == "h"))
        and _contains(vector.bbox, bounds, size / 4)
        and bounds.top - vector.bbox.top <= size * 3
        and vector.bbox.bottom - bounds.bottom > size * 2
    ]
    # A label can sit just below the top rule or in an opening in it.
    # The two sides and their shared bottom rule bound either version.
    top = bounds.top - size
    sides = {
        vector.bbox for vector in shapes
        if vector.kind == "line" and vector.bbox.x1 - vector.bbox.x0 <= 1
        and top <= vector.bbox.top <= bounds.bottom
        and vector.bbox.bottom - bounds.bottom > size * 2
    }
    for vector in shapes:
        border = vector.bbox
        if (vector.kind != "line" or border.bottom - border.top > 1
                or border.top - bounds.bottom <= size * 2
                or border.x0 > bounds.x0 or border.x1 < bounds.x1):
            continue
        left = BBox(border.x0 - 2, top, border.x0 + 2, border.bottom + 2)
        right = BBox(border.x1 - 2, top, border.x1 + 2, border.bottom + 2)
        if (any(_contains(left, side) and abs(side.bottom - border.bottom) <= 2 for side in sides)
                and any(_contains(right, side) and abs(side.bottom - border.bottom) <= 2 for side in sides)):
            boxes.append(BBox(border.x0, bounds.top, border.x1, border.bottom))
    return min(boxes, key=lambda box: (box.x1 - box.x0) * (box.bottom - box.top), default=None)


def _running_margins(pages: list[_Page]) -> set[str]:
    groups: dict[tuple[str, int, int], dict[int, list[str]]] = {}
    for data in pages:
        source = data.page.source
        if source.width <= 0 or source.height <= 0:
            continue
        for _, item in data.lines:
            box = item.line.bbox
            if not item.root:
                continue
            width = box.x1 - box.x0
            side = width < source.width * .07 and (box.x0 >= source.width * .88 or box.x1 <= source.width * .12)
            horizontal = box.bottom <= source.height * .08 or box.top >= source.height * .88
            if horizontal and _PAGE_NUMBER.fullmatch(item.text):
                number = _NUMBER.search(item.text)
                assert number is not None
                signature = str(int(number[0]) - source.page_number)
            elif horizontal and width <= source.width * .5 and len(re.findall(r"[가-힣A-Za-z]", item.text)) >= 4:
                signature = _NUMBER.sub("#", _compact(item.text))
            elif side and re.fullmatch(r"[가-힣·ㆍ ]{1,8}", item.text):
                signature = _compact(item.text)
            else:
                continue
            key = (signature, round((box.x0 + box.x1) / source.width * 12), round(box.top / source.height * 50))
            groups.setdefault(key, {}).setdefault(source.page_number, []).append(item.line.id)
    # Running furniture returns at the same position on adjacent pages or on
    # alternating sides. Equal number offsets alone can also describe amounts.
    return {
        line_id for group in groups.values() for number, line_ids in group.items()
        if any(number + shift in group for shift in (-2, -1, 1, 2))
        for line_id in line_ids
    }


def _font(item: _Line, fragments: dict[str, TextFragment]) -> str:
    font = next((fragment.style.font_name for fragment_id in item.line.fragment_ids
                 if (fragment := fragments[fragment_id]).text.translate(_CONTROLS).strip()), "")
    return _FONT_SUBSET.sub("", font)


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

    return (abs(size - following.line.font_size) >= .5
            or _font(item, fragments) != _font(following, fragments))


def _listed_heading(text: str) -> bool:
    if _LEADER.search(text):
        return True
    locator = _LOCATOR.search(text)
    if locator is None:
        return False
    title = text[:locator.start()]
    article = _article(title)
    return bool((article and not article.inline) or _appendix_label(title) or _rider_title(title))


def _statute_quote(lines: list[tuple[int, _Line]], index: int) -> set[str]:
    marker = lines[index][1]
    if not _STATUTE_LABEL.fullmatch(marker.text) or index + 1 == len(lines):
        return set()
    following = lines[index + 1][1]
    # Some quoted laws are set off by indentation or by image backgrounds.
    # Require a provision immediately below the legal label in the same column.
    left = marker.line.bbox.x0 - marker.line.font_size * .45
    column = marker.region.column_path
    if (following.region.column_path != column or following.line.bbox.x0 < left
            or _article(following.text) is None):
        return set()
    quoted = {marker.line.id}
    for _, item in lines[index + 1:]:
        if item.region.column_path != column or item.line.bbox.x0 < left:
            break
        quoted.add(item.line.id)
    return quoted


def _prepare_page(data: _Page, margins: set[str], styles: Counter[_Style]) -> None:
    page, lines = data.page, data.lines
    if (not lines or any(_CONTENTS.fullmatch(_label(line.text)) for _, line in lines[:8])
            or sum(bool(_LEADER.search(line.text)) for _, line in lines if line.root) >= 2):
        data.contents = True
        return

    fragments = {fragment.id: fragment for fragment in page.source.fragments}
    shapes = [vector for vector in page.source.vectors if vector.stroke or vector.fill]
    inset_boxes: list[BBox] = []
    quoted: set[str] = set()
    for index, (_, item) in enumerate(lines):
        explicit = _INSET.fullmatch(_label(item.text))
        if not explicit and not (item.root and _BOX_LABEL.fullmatch(item.text)
                                 and _appendix_label(item.text) is None):
            continue
        box = _inset_box(shapes, item)
        if box is not None:
            inset_boxes.append(box)
        elif explicit and item.root:
            data.uncertain_insets.add(item.line.id)
        else:
            quoted.update(_statute_quote(lines, index))

    width = page.source.width
    margin_tabs = [
        v.bbox for v in shapes if v.kind == "rect"
        and (v.bbox.x0 >= width * .88 and v.bbox.x1 >= width
             or v.bbox.x1 <= width * .12 and v.bbox.x0 <= 0)
    ]
    data.lines = [
        (position, item) for position, item in lines
        if item.line.id not in margins
        and item.line.id not in quoted
        and not any(_inside_inset(box, item, fragments) for box in inset_boxes)
        and not any(_contains(box, item.line.bbox, 2) for box in margin_tabs)
        and not (item.root and _listed_heading(item.text))
    ]
    previous: _Line | None = None
    blocked_columns: set[tuple[int, ...]] = set()
    for index, (_, item) in enumerate(data.lines):
        column = item.region.column_path
        if item.line.id in data.uncertain_insets:
            blocked_columns.add(column)
        if not item.root or column in blocked_columns:
            continue
        following = data.lines[index + 1][1] if index + 1 < len(data.lines) else None
        line_id = item.line.id
        article = _article(item.text)
        appendix = _appendix_label(item.text)
        separate = False
        if article or appendix or _structural_title(item.text):
            separate = _visually_separate(item, previous, following, fragments, inset_boxes)
            appendix_at_top = (appendix is not None and previous is None
                               and bool(appendix["open"]) and item.line.bbox.top <= page.source.height * .18)
            if separate or appendix_at_top:
                data.boundaries.add(line_id)
        if article is not None:
            box, size = item.line.bbox, item.line.font_size
            neighbors = [
                neighbor for neighbor in (previous, following)
                if neighbor is not None and neighbor.root and neighbor.region.column_path == column
            ]
            hanging = any(neighbor.line.bbox.x0 - box.x0 >= size * .5 for neighbor in neighbors)
            aligned = any(box.x0 <= neighbor.line.bbox.x0 + size * .4
                          and abs(neighbor.line.bbox.top - box.top) >= size * .8
                          for neighbor in neighbors)
            font = _font(item, fragments)
            distinctive = any(font != _font(neighbor, fragments)
                              or abs(size - neighbor.line.font_size) >= .5 for neighbor in neighbors)
            strong = separate if not article.inline else hanging or (separate and distinctive)
            style = (article.bracket, article.inline, font, round(size * 2))
            data.candidates[line_id] = _Candidate(article, style, strong, aligned)
            if strong and (hanging or distinctive):
                styles[style] += 1
        previous = item


def _structural_title(text: str) -> bool:
    return bool(_rider_title(text) or _RIDER_CODE.match(text) or _GROUP.fullmatch(text)
                or _label(text).endswith("보통약관"))


def _numbered_dividers(data: _Page) -> set[str]:
    """Find chapter-like titles that introduce a smaller, numbered rider."""
    dividers: set[str] = set()
    following: tuple[_Line, _Code] | None = None
    for _, item in reversed(data.lines):
        line_id = item.line.id
        if line_id in data.candidates or line_id in data.uncertain_insets:
            following = None
        if line_id not in data.boundaries or not _rider_title(item.text):
            continue
        code = _rider_code(item.text)
        if following is not None:
            child, child_code = following
            if (_descends(child_code, code) and item.region.column_path == child.region.column_path
                    and item.line.font_size >= child.line.font_size * 1.2):
                dividers.add(line_id)
        following = (item, code)
    return dividers


def _first_article_follows(data: _Page, index: int, styles: Counter[_Style]) -> bool:
    for _, item in data.lines[index + 1:index + 4]:
        candidate = data.candidates.get(item.line.id)
        if candidate is not None:
            return candidate.article.number == (1, 0) and styles[candidate.style] >= 2
    return False


def annotate_insurance(pages: tuple["ParsedPage", ...]) -> InsuranceAnnotations:
    """Profile the resolved document and attach section references to its existing lines."""
    document = [_Page(page, _lines(page)) for page in pages]
    if not _has_policy_context(document):
        return InsuranceAnnotations(())

    margins = _running_margins(document)
    styles: Counter[_Style] = Counter()
    for data in document:
        _prepare_page(data, margins, styles)

    sections: list[_Section] = []
    active: _Section | None = None
    rider: _Section | None = None
    rider_code: _Code = ()
    last_article: tuple[int, int] | None = None
    for data in document:
        if data.contents:
            active = rider = None
            rider_code = ()
            last_article = None
            continue
        page_id = data.page.source.id
        blocked_columns: set[tuple[int, ...]] = set()
        dividers = _numbered_dividers(data)
        for index, (position, item) in enumerate(data.lines):
            line_id, text, column = item.line.id, item.text, item.region.column_path
            if line_id in data.uncertain_insets:
                blocked_columns.add(column)
                active = None
                # A late inset can continue into the next column or page.
                # Wait for the next heading before assigning any body there.
                # Keep its numbered rider only if article numbering continues.
                if not rider_code or last_article is None or item.line.bbox.top < data.page.source.height * .8:
                    rider = None
                    rider_code = ()
                    last_article = None
            if column in blocked_columns or (not item.root and blocked_columns):
                continue

            if line_id in data.boundaries:
                code = _rider_code(text)
                appendix = _appendix_label(text)
                group = _GROUP.fullmatch(text)
                if appendix is not None:
                    active = _Section("appendix", InsuranceSpan(page_id, item.region.id, (line_id,)))
                    sections.append(active)
                    rider = None
                    rider_code = ()
                    last_article = None
                    continue
                if (line_id in dividers or _label(text).endswith("보통약관")
                        or (group and group[2] == "편" and not _SENTENCE_END.search(text))):
                    active = rider = None
                    rider_code = ()
                    last_article = None
                    continue
                if _descends(code, rider_code) and not _SENTENCE_END.search(_RIDER_START.sub("", text)):
                    # Coverage divisions can restart article numbering. Keep
                    # the actual enclosing rider heading through each child.
                    active = rider
                    last_article = None
                    continue
                if _rider_title(text):
                    active = rider = _Section("rider", InsuranceSpan(page_id, item.region.id, (line_id,)))
                    sections.append(active)
                    rider_code = code
                    last_article = None
                    continue
                if (group and not _SENTENCE_END.search(text) and
                        (active is None or active.kind != "appendix"
                         or (group[1] == "1" and group[2] == "관"
                             and _first_article_follows(data, index, styles)))):
                    active = rider
                    last_article = None
                    continue

            candidate = data.candidates.get(line_id)
            if candidate is not None and (active is None or active.kind != "appendix"):
                article = candidate.article
                if candidate.separate or (candidate.aligned and styles[candidate.style] >= 2):
                    if last_article is not None and article.number <= last_article:
                        rider = None
                        rider_code = ()
                    last_article = article.number
                    rider_id = rider.heading.line_ids[0] if rider else None
                    active = _Section("article", InsuranceSpan(page_id, item.region.id, (line_id,)), rider_id)
                    sections.append(active)
                    continue
                if not article.inline:
                    # An ambiguous isolated title leaves following ownership uncertain.
                    active = None
                    continue
            if active is not None:
                active.body.append((page_id, position, item.region.id, line_id))

    return InsuranceAnnotations(tuple(section.freeze() for section in sections))
