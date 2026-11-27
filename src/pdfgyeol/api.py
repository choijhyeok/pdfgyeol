"""Local PDF -> source-preserving layout and versioned JSON, offline by default."""

import json
import math
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

from .decision import DecisionClient, DecisionConfig, DecisionProvider, DecisionRecord, resolve_page
from .extraction import extract_pdf
from .insurance import InsuranceAnnotations, annotate_insurance
from .layout import infer_page
from .layout_models import LayoutConfig, PageLayout, TextLine, _ordered_line_ids
from .models import Page, TextFragment

PARSE_SCHEMA_VERSION = "pdfgyeol.parse/1"


def _line_text(fragments: Iterable[TextFragment]) -> str:
    parts: list[str] = []
    right = float("-inf")
    for fragment in fragments:
        if (fragment.style.upright and fragment.bbox.x0 - right > 3
                and parts and not parts[-1][-1:].isspace()
                and fragment.text and not fragment.text[0].isspace()):
            parts.append(" ")
        parts.append(fragment.text)
        right = max(right, fragment.bbox.x1)
    return "".join(parts)


def _render_lines(
    line_ids: Iterable[str], lines: dict[str, TextLine], fragments: dict[str, TextFragment],
) -> Iterator[tuple[TextLine, str]]:
    for line_id in line_ids:
        line = lines[line_id]
        yield line, _line_text(fragments[fragment_id] for fragment_id in line.fragment_ids)


class _CellTextCache:
    # Keep the lazy lookup outside the dataclass's value and serialized state.
    __slots__ = ("_cell_text",)
    _cell_text: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class ParsedPage(_CellTextCache):
    """Source geometry once, layout references, decisions and ready-to-use text."""

    source: Page
    layout: PageLayout
    decisions: tuple[DecisionRecord, ...]

    def ordered_fragments(self) -> tuple[TextFragment, ...]:
        fragments = {fragment.id: fragment for fragment in self.source.fragments}
        return tuple(fragments[fragment_id] for fragment_id in self.layout.ordered_fragment_ids())

    def _ordered_lines(
        self, region_ids: tuple[str, ...] | None = None,
    ) -> Iterator[tuple[TextLine, str]]:
        fragments = {fragment.id: fragment for fragment in self.source.fragments}
        lines = {line.id: line for line in self.layout.lines}
        yield from _render_lines(self.layout.ordered_line_ids(region_ids), lines, fragments)

    def _text(self, region_ids: tuple[str, ...] | None = None) -> str:
        return "\n".join(text for _, text in self._ordered_lines(region_ids))

    @property
    def text(self) -> str:
        """Source characters in resolved order with inferred spaces and line breaks."""
        return self._text()

    def region_text(self, region_id: str) -> str:
        """Text under a region, including nested tables; unknown ID raises KeyError."""
        return self._text((region_id,))

    def cell_text(self, cell_id: str) -> str:
        """Text under a cell in resolved order; unknown ID raises KeyError."""
        lookup = getattr(self, "_cell_text", None)
        if lookup is None:
            lookup = self._index_cell_text()
            object.__setattr__(self, "_cell_text", lookup)
        return lookup(cell_id)

    def _index_cell_text(self) -> Callable[[str], str]:
        fragments = {fragment.id: fragment for fragment in self.source.fragments}
        lines = {line.id: line for line in self.layout.lines}
        regions = {region.id: region for region in self.layout.regions}
        tables = {table.id: table for table in self.layout.tables}
        cells: dict[str, tuple[str, ...]] = {}
        for table in self.layout.tables:
            for row in table.rows:
                for cell in row.cells:
                    cells.setdefault(cell.id, cell.region_ids)

        def lookup(cell_id: str) -> str:
            line_ids = _ordered_line_ids(cells[cell_id], regions, tables)
            return "\n".join(text for _, text in _render_lines(line_ids, lines, fragments))

        return lookup


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    id: str
    sha256: str
    source_schema_version: str
    extractor_version: str
    decision_enabled: bool
    pages: tuple[ParsedPage, ...]
    schema_version: str = PARSE_SCHEMA_VERSION
    insurance: InsuranceAnnotations | None = None

    @property
    def text(self) -> str:
        """Page text joined with newline, form-feed, newline."""
        return "\n\f\n".join(page.text for page in self.pages)

    def to_dict(self) -> dict[str, Any]:
        """Independent JSON-native tree with the same content as to_json()."""
        return {key: _json_native(value) for key, value in _json_fields(self).items()}

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize as Unicode JSON with a final newline."""
        if indent is not None and sys.version_info < (3, 13):
            # Older CPython encodes indented JSON in Python. Materialize the tree
            # once to avoid a recursive encoder callback for every record.
            data = asdict(self)
            if self.insurance is None:
                del data["insurance"]
            for page_data, page in zip(data["pages"], self.pages):
                page_data["text"] = page.text
            return json.dumps(data, ensure_ascii=False, allow_nan=False, indent=indent) + "\n"
        return json.dumps(self, default=_json_fields, ensure_ascii=False,
                          allow_nan=False, indent=indent) + "\n"


@cache
def _field_names(cls: type[Any]) -> tuple[str, ...]:
    if not is_dataclass(cls):
        raise TypeError(f"Object of type {cls.__name__} is not JSON serializable")
    return tuple(field.name for field in fields(cls))


def _json_fields(record: Any) -> dict[str, Any]:
    """Expose one record at a time in schema field order."""
    cls: Any = type(record)
    data = {name: getattr(record, name) for name in _field_names(cls)}
    if isinstance(record, ParsedPage):
        data["text"] = record.text
    if isinstance(record, ParsedDocument) and record.insurance is None:
        del data["insurance"]
    return data


def _json_native(value: Any) -> Any:
    kind = type(value)
    if kind is str or kind is int or kind is bool or value is None:
        return value
    if kind is float and math.isfinite(value):
        return value
    if isinstance(value, (tuple, list)):
        return [_json_native(item) for item in value]
    if isinstance(value, str):
        return str.__str__(value)  # PageKind and other string subclasses become plain strings.
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _json_native(item) for key, item in _json_fields(value).items()}
    return json.loads(json.dumps(value, default=_json_fields, allow_nan=False))


def parse_pdf(path: str | Path, *, password: str | None = None,
              layout_config: LayoutConfig | None = None,
              decision_config: DecisionConfig | None = None,
              provider: DecisionProvider | None = None,
              insurance: bool = False) -> ParsedDocument:
    """Parse a local PDF and optionally annotate Korean insurance sections.

    Set pdfgyeol.host or pass DecisionConfig(enabled=True) to enable Decision.
    A per-call provider overrides the module host. An explicit disabled config
    skips Decision. Invalid module host settings raise before scoring starts.
    Insurance annotation runs locally after the final reading order is resolved.
    I/O/password/parser errors propagate; no output files or directories created.
    """
    if decision_config is None or decision_config.enabled:
        import pdfgyeol

        host = pdfgyeol.host
        if host is not None:
            decision_config = decision_config or DecisionConfig(enabled=True)
            if provider is None:
                provider = DecisionClient.from_host(
                    host, endpoint=pdfgyeol.endpoint, apikey=pdfgyeol.apikey, model=pdfgyeol.model,
                )
    config = decision_config or DecisionConfig()
    document = extract_pdf(path, password=password)
    pages = []
    for page in document.pages:
        layout = infer_page(page, config=layout_config)
        decisions: tuple[DecisionRecord, ...] = ()
        if config.enabled:
            result = resolve_page(page, layout, config=config, provider=provider)
            layout, decisions = result.layout, result.decisions
        pages.append(ParsedPage(page, layout, decisions))
    parsed = ParsedDocument(document.id, document.sha256, document.schema_version,
                            document.extractor_version, config.enabled, tuple(pages))
    if insurance:
        parsed = replace(parsed, insurance=annotate_insurance(parsed.pages))
    return parsed
