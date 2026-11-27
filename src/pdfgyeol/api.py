"""Local PDF -> source-preserving layout and versioned JSON, offline by default."""

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .decision import DecisionConfig, DecisionProvider, DecisionRecord, resolve_page
from .extraction import extract_pdf
from .layout import infer_page
from .layout_models import LayoutConfig, PageLayout
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


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """Source geometry once, layout references, decisions and ready-to-use text."""

    source: Page
    layout: PageLayout
    decisions: tuple[DecisionRecord, ...]

    def ordered_fragments(self) -> tuple[TextFragment, ...]:
        fragments = {fragment.id: fragment for fragment in self.source.fragments}
        return tuple(fragments[fragment_id] for fragment_id in self.layout.ordered_fragment_ids())

    def _text(self, region_ids: tuple[str, ...] | None = None) -> str:
        fragments = {fragment.id: fragment for fragment in self.source.fragments}
        lines = {line.id: line for line in self.layout.lines}
        return "\n".join(
            _line_text(fragments[fragment_id] for fragment_id in lines[line_id].fragment_ids)
            for line_id in self.layout.ordered_line_ids(region_ids)
        )

    @property
    def text(self) -> str:
        """Source characters in resolved order with inferred spaces and line breaks."""
        return self._text()

    def region_text(self, region_id: str) -> str:
        """Text under a region, including nested tables; unknown ID raises KeyError."""
        return self._text((region_id,))

    def cell_text(self, cell_id: str) -> str:
        """Text under a cell in resolved order; unknown ID raises KeyError."""
        for table in self.layout.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.id == cell_id:
                        return self._text(cell.region_ids)
        raise KeyError(cell_id)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    id: str
    sha256: str
    source_schema_version: str
    extractor_version: str
    decision_enabled: bool
    pages: tuple[ParsedPage, ...]
    schema_version: str = PARSE_SCHEMA_VERSION

    @property
    def text(self) -> str:
        """Page text joined with newline, form-feed, newline."""
        return "\n\f\n".join(page.text for page in self.pages)

    def to_dict(self) -> dict[str, Any]:
        """Independent JSON-native tree with the same content as to_json()."""
        result: dict[str, Any] = json.loads(self.to_json(indent=None))
        return result

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize as Unicode JSON with a final newline."""
        data = asdict(self)
        for page_data, page in zip(data["pages"], self.pages):
            page_data["text"] = page.text
        return json.dumps(data, ensure_ascii=False, allow_nan=False, indent=indent) + "\n"


def parse_pdf(path: str | Path, *, password: str | None = None,
              layout_config: LayoutConfig | None = None,
              decision_config: DecisionConfig | None = None,
              provider: DecisionProvider | None = None) -> ParsedDocument:
    """Parse a local PDF using extraction -> layout -> opt-in Decision.

    Supplying a provider alone does not enable Decision. No environment access or
    provider call without DecisionConfig(enabled=True) and an eligible candidate.
    I/O/password/parser errors propagate; no output files or directories created.
    """
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
    return ParsedDocument(document.id, document.sha256, document.schema_version,
                          document.extractor_version, config.enabled, tuple(pages))
