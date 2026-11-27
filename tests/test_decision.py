"""Synthetic local scopes and fake providers; never uses credentials or network."""

from collections import Counter
from dataclasses import asdict, replace
from io import BytesIO
import json
from urllib.error import HTTPError

import pytest
from reportlab.pdfgen.canvas import Canvas

from pdfgyeol import BBox, DecisionClient, DecisionConfig, extract_pdf, infer_page, resolve_page
import pdfgyeol.decision as decision


class FakeProvider:
    model = "fake-local-test"

    def __init__(self, answer=None, error=None):
        self.answer = {"candidate_0": .1, "candidate_1": .9} if answer is None else answer
        self.error = error
        self.calls = []

    def score(self, state, questions):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        return self.answer


def sample(tmp_path, *, nested=False, multiple=False, clear=False):
    path = tmp_path / "decision.pdf"
    c = Canvas(str(path), pagesize=(600, 800), invariant=1)
    c.setFont("Helvetica-Bold", 14)
    if not multiple:
        c.drawString(40, 765, "PRIVATE BEFORE: outside active region, never send this text")
    c.drawString(40, 90, "PRIVATE AFTER: outside active region, never send this text")
    c.setFont("Helvetica", 10)
    if nested:
        for xs, ys in [([40, 330, 560], [350, 730]), ([55, 285, 320], [450, 710])]:
            for x in xs:
                c.line(x, ys[0], x, ys[-1])
            for y in ys:
                c.line(xs[0], y, xs[-1], y)
        c.drawString(340, 680, "PRIVATE OTHER OUTER CELL")
        c.setFont("Helvetica", 4)
        c.drawString(290, 680, "PRIVATE")
        c.setFont("Helvetica", 10)
        c.drawString(50, 370, "PRIVATE PARENT CELL TAIL")
    x0, x1 = (65, 200) if nested else (40, 340)
    pairs = [("Seed", "Cost"), ("Peas", "12")]
    for j, (left, right) in enumerate(pairs):
        c.drawString(x0, 680 - 30 * j, left)
        c.drawString(x1, 680 - 30 * j, right)
    if multiple:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, 540, "PRIVATE SEPARATOR across both columns of the page")
        c.setFont("Helvetica", 10)
        for j, (left, right) in enumerate([("Length", "Unit"), ("Height", "cm")]):
            c.drawString(40, 490 - 30 * j, left)
            c.drawString(340, 490 - 30 * j, right)
    c.save()
    page = extract_pdf(path).pages[0]
    layout = infer_page(page)
    if clear:
        layout = replace(layout, alternatives=())
    return page, layout


def source_text(page, layout):
    source = {f.id: f.text for f in page.fragments}
    return "".join(source[i] for i in layout.ordered_fragment_ids())


def assert_conserved(page, before, after):
    assert Counter(after.ordered_fragment_ids()) == Counter(f.id for f in page.fragments)
    assert before.lines == after.lines
    assert before.regions == after.regions
    assert before.alternatives == after.alternatives
    assert before.warnings == after.warnings
    assert len(after.ordered_fragment_ids()) == len(set(after.ordered_fragment_ids()))


def test_default_disabled_and_no_alternatives_never_read_env_or_call(tmp_path, monkeypatch):
    page, layout = sample(tmp_path)
    provider = FakeProvider(error=AssertionError("must not call"))

    def forbidden(*args, **kwargs):
        pytest.fail("must not read credentials or construct client")

    monkeypatch.setattr(DecisionClient, "from_env", forbidden)
    result = resolve_page(page, layout, provider=provider)
    assert result.layout is layout
    assert result.decisions[0].outcome == "disabled"
    assert result.decisions[0].provider is None
    assert resolve_page(page, layout).layout is layout
    clear = replace(layout, alternatives=())
    assert resolve_page(page, clear, config=DecisionConfig(enabled=True)).decisions == ()
    assert resolve_page(page, clear, config=DecisionConfig(enabled=True), provider=provider).layout is clear
    assert not provider.calls


def test_root_selection_only_changes_active_order_and_records_provenance(tmp_path):
    page, layout = sample(tmp_path)
    source_before = asdict(page)
    assert len(layout.alternatives) == 1
    provider = FakeProvider()
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
    assert "SeedPeasCost12" in source_text(page, layout)
    assert "SeedCostPeas12" in source_text(page, result.layout)
    assert result.layout.reading_order[0] == layout.reading_order[0]
    assert result.layout.reading_order[-1] == layout.reading_order[-1]
    assert result.layout.tables == layout.tables
    assert asdict(page) == source_before
    record, = result.decisions
    assert record.outcome == "applied" and record.selected_index == 1
    assert record.scores == (.1, .9)
    assert record.model == "fake-local-test" and record.provider == "FakeProvider"
    assert record.scope_id == page.id and record.default_index == 0
    assert record.candidate_names == ("columns", "rows")
    assert len(record.state_sha256) == 64
    state, questions = provider.calls[0]
    assert "PRIVATE" not in state and set(questions) == {"candidate_0", "candidate_1"}
    assert json.loads(state)["regions"]
    assert "excerpt" not in json.dumps(asdict(record))
    assert_conserved(page, layout, result.layout)
    assert resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=FakeProvider()) == result


def test_nested_table_cell_selection_preserves_every_other_scope(tmp_path):
    page, layout = sample(tmp_path, nested=True)
    assert len(layout.tables) == 2 and len(layout.alternatives) == 1
    alternative = layout.alternatives[0]
    provider = FakeProvider()
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
    assert result.decisions[0].outcome == "applied"
    assert "SeedCostPeas12" in source_text(page, result.layout)
    assert result.layout.reading_order == layout.reading_order
    before_cells = {cell.id: cell for t in layout.tables for row in t.rows for cell in row.cells}
    after_cells = {cell.id: cell for t in result.layout.tables for row in t.rows for cell in row.cells}
    assert before_cells[alternative.scope_id].region_ids != after_cells[alternative.scope_id].region_ids
    for id, cell in before_cells.items():
        if id != alternative.scope_id:
            assert cell == after_cells[id]
    assert "PRIVATE" not in provider.calls[0][0]
    assert_conserved(page, layout, result.layout)


def test_multiple_disjoint_candidates_apply_independently(tmp_path):
    page, layout = sample(tmp_path, multiple=True)
    assert len(layout.alternatives) == 2
    provider = FakeProvider()
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
    assert len(provider.calls) == 2 and all(r.outcome == "applied" for r in result.decisions)
    assert "SeedCostPeas12" in source_text(page, result.layout)
    assert "LengthUnitHeightcm" in source_text(page, result.layout)
    assert "Length" not in provider.calls[0][0] and "Seed" not in provider.calls[1][0]
    assert "PRIVATE" not in "".join(state for state, _ in provider.calls)
    assert_conserved(page, layout, result.layout)


def test_failure_in_one_region_does_not_block_other_regions(tmp_path):
    page, layout = sample(tmp_path, multiple=True)

    class FirstFails(FakeProvider):
        def score(self, state, questions):
            super().score(state, questions)
            if len(self.calls) == 1:
                raise TimeoutError("transient")
            return self.answer

    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=FirstFails())
    assert [r.reason for r in result.decisions] == ["provider_failure", "scored_alternative"]
    assert "SeedPeasCost12" in source_text(page, result.layout)
    assert "LengthUnitHeightcm" in source_text(page, result.layout)
    assert_conserved(page, layout, result.layout)


@pytest.mark.parametrize("answer", [
    {}, {"candidate_0": .1}, {"candidate_0": .1, "candidate_1": .9, "injected": .8},
    {"candidate_0": .1, "candidate_1": "rows"}, {"candidate_0": False, "candidate_1": .9},
    {"candidate_0": .1, "candidate_1": float("nan")},
    {"candidate_0": .1, "candidate_1": float("inf")},
    {"candidate_0": -.1, "candidate_1": .9}, {"candidate_0": .1, "candidate_1": 1.1},
    {"candidate_0": .1, "candidate_1": 10 ** 400},
    {"candidate_0": .1, "candidate_1": {"region_ids": ["invented"]}}, [0, 1], None,
])
def test_malformed_scores_fall_back_without_text_rewrite(tmp_path, answer):
    page, layout = sample(tmp_path)
    provider = FakeProvider()
    provider.answer = answer
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
    assert result.layout is layout and result.decisions[0].reason == "invalid_answer"
    assert result.decisions[0].selected_index == 0 and not result.decisions[0].scores


@pytest.mark.parametrize("scores", [(.5, .5), (.50, .55), (.1, .2), (1., 1.), (.7, .8)])
def test_ambiguous_or_low_confidence_scores_defer(tmp_path, scores):
    page, layout = sample(tmp_path)
    provider = FakeProvider(dict(zip(("candidate_0", "candidate_1"), scores)))
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
    assert result.layout is layout and result.decisions[0].reason == "ambiguous_scores"
    assert result.decisions[0].scores == scores


def test_zero_margin_still_defers_exact_ties_and_nonzero_default_index_works(tmp_path):
    page, layout = sample(tmp_path)
    alternative = layout.alternatives[0]
    alternative = replace(alternative, candidates=tuple(reversed(alternative.candidates)), default_index=1)
    layout = replace(layout, alternatives=(alternative,))
    tied = resolve_page(page, layout, config=DecisionConfig(enabled=True, min_margin=0),
                        provider=FakeProvider({"candidate_0": .9, "candidate_1": .9}))
    assert tied.layout is layout and tied.decisions[0].reason == "ambiguous_scores"
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True),
                          provider=FakeProvider({"candidate_0": .9, "candidate_1": .1}))
    assert "SeedCostPeas12" in source_text(page, result.layout)
    assert result.decisions[0].default_index == 1 and result.decisions[0].selected_index == 0


def test_clear_default_choice_recorded(tmp_path):
    page, layout = sample(tmp_path)
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True),
                          provider=FakeProvider({"candidate_0": .9, "candidate_1": .1}))
    assert result.layout is layout
    assert result.decisions[0].outcome == "kept_default" and result.decisions[0].reason == "scored_default"


@pytest.mark.parametrize("error", [TimeoutError("SECRET endpoint payload"),
                                      HTTPError("https://secret.invalid", 429, "SECRET", {}, None),
                                      HTTPError("https://secret.invalid", 503, "SECRET", {}, None)])
def test_transient_failure_defers_once_and_redacts_errors(tmp_path, error):
    page, layout = sample(tmp_path)
    provider = FakeProvider(error=error)
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
    assert result.layout is layout and result.decisions[0].reason == "provider_failure"
    assert len(provider.calls) == 1 and "SECRET" not in repr(result.decisions)


@pytest.mark.parametrize("mutation", ["foreign_scope", "foreign_region", "duplicate", "missing",
                                         "not_contiguous", "wrong_default", "bounds", "overlap", "default_index"])
def test_invalid_scope_or_permutation_never_calls(tmp_path, mutation):
    page, layout = sample(tmp_path)
    alternative = layout.alternatives[0]
    default, rows = alternative.candidates
    if mutation == "foreign_scope":
        alternative = replace(alternative, scope_id="other-page")
    elif mutation == "foreign_region":
        rows = replace(rows, region_ids=rows.region_ids[:-1] + (layout.reading_order[-1],))
    elif mutation == "duplicate":
        rows = replace(rows, region_ids=rows.region_ids[:-1] + rows.region_ids[:1])
    elif mutation == "missing":
        rows = replace(rows, region_ids=rows.region_ids[:-1])
    elif mutation == "not_contiguous":
        default = replace(default, region_ids=(layout.reading_order[0],) + default.region_ids[1:])
        rows = replace(rows, region_ids=tuple(reversed(default.region_ids)))
    elif mutation == "wrong_default":
        default = replace(default, region_ids=tuple(reversed(default.region_ids)))
    elif mutation == "bounds":
        alternative = replace(alternative, bbox=BBox(0, 0, 1, 1))
    elif mutation == "default_index":
        alternative = replace(alternative, default_index=-1)
    alternative = replace(alternative, candidates=(default, rows))
    alternatives = (alternative, replace(alternative, id="overlapping")) if mutation == "overlap" else (alternative,)
    layout = replace(layout, alternatives=alternatives)
    provider = FakeProvider()
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
    assert result.layout is layout and not provider.calls
    assert all(r.reason == "invalid_scope" for r in result.decisions)


def test_cross_cell_candidate_cannot_move_a_region(tmp_path):
    page, layout = sample(tmp_path, nested=True)
    alternative = layout.alternatives[0]
    foreign = next(cell.region_ids[0] for table in layout.tables for row in table.rows for cell in row.cells
                   if cell.id != alternative.scope_id and cell.region_ids
                   and cell.region_ids[0] not in alternative.candidates[0].region_ids)
    alternative = replace(alternative, candidates=tuple(
        replace(c, region_ids=c.region_ids[:-1] + (foreign,)) for c in alternative.candidates))
    layout = replace(layout, alternatives=(alternative,))
    provider = FakeProvider()
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
    assert result.layout is layout and not provider.calls and result.decisions[0].reason == "invalid_scope"


def test_identical_candidates_and_budget_exhaustion_skip_client(tmp_path, monkeypatch):
    page, layout = sample(tmp_path)
    provider = FakeProvider()
    alternative = layout.alternatives[0]
    identical = replace(alternative, candidates=(alternative.candidates[0],) * 2)
    result = resolve_page(page, replace(layout, alternatives=(identical,)),
                          config=DecisionConfig(enabled=True), provider=provider)
    assert result.decisions[0].reason == "no_distinct_candidates"
    for config in [DecisionConfig(enabled=True, max_regions=1), DecisionConfig(enabled=True, max_lines=1),
                   DecisionConfig(enabled=True, max_state_bytes=1)]:
        result = resolve_page(page, layout, config=config, provider=provider)
        assert result.layout is layout and result.decisions[0].reason == "payload_budget_or_bounds"
    assert not provider.calls


def test_excerpt_limits_and_no_unrelated_regions(tmp_path):
    page, layout = sample(tmp_path)
    provider = FakeProvider()
    resolve_page(page, layout, config=DecisionConfig(enabled=True, max_text_chars=5, max_line_chars=3),
                 provider=provider)
    raw = provider.calls[0][0]
    state = json.loads(raw)
    excerpts = [line for region in state["regions"] for line in region["lines"]]
    assert sum(len(line["excerpt"]) for line in excerpts) == 5
    assert all(len(line["excerpt"]) <= 3 for line in excerpts)
    assert any(line["truncated"] for line in excerpts)
    assert "PRIVATE" not in raw and len(raw.encode()) <= 32768
    assert {region["id"] for region in state["regions"]} == set(layout.alternatives[0].candidates[0].region_ids)


def test_invalid_base_ownership_rejected_before_provider(tmp_path):
    page, layout = sample(tmp_path)
    provider = FakeProvider()
    bad_layouts = [replace(layout, page_id="foreign"),
                   replace(layout, reading_order=layout.reading_order + layout.reading_order[:1]),
                   replace(layout, regions=layout.regions + layout.regions[:1]),
                   replace(layout, lines=layout.lines[1:]),
                   replace(layout, lines=(replace(layout.lines[0], fragment_ids=("invented",)),) + layout.lines[1:])]
    for bad in bad_layouts:
        with pytest.raises(ValueError, match="Decision"):
            resolve_page(page, bad, config=DecisionConfig(enabled=True), provider=provider)
    assert not provider.calls


def test_nested_table_ownership_cycle_rejected_before_provider(tmp_path):
    page, layout = sample(tmp_path, nested=True)
    alternative = layout.alternatives[0]
    root_table_region = next(r.id for r in layout.regions if r.table_id and r.id in layout.reading_order)
    tables = tuple(replace(table, rows=tuple(replace(row, cells=tuple(
        replace(cell, region_ids=cell.region_ids + (root_table_region,)) if cell.id == alternative.scope_id
        else cell for cell in row.cells)) for row in table.rows)) for table in layout.tables)
    provider = FakeProvider()
    with pytest.raises(ValueError, match="ownership"):
        resolve_page(page, replace(layout, tables=tables), config=DecisionConfig(enabled=True), provider=provider)
    assert not provider.calls


def test_enabled_without_credentials_defers(tmp_path, monkeypatch):
    page, layout = sample(tmp_path)
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "DECISION_MODEL_DEPLOYMENT"):
        monkeypatch.delenv(key, raising=False)
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True))
    assert result.layout is layout and result.decisions[0].reason == "not_configured"


@pytest.mark.parametrize("kwargs", [{"min_score": float("nan")}, {"min_margin": -1},
                                       {"min_score": True}, {"max_text_chars": 0}, {"max_regions": 1.5},
                                       {"enabled": "false"}, {"enabled": 1}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        DecisionConfig(**kwargs)


@pytest.mark.parametrize("endpoint", ["http://example.invalid", "https://user:secret@example.invalid", ""])
def test_client_rejects_unsafe_endpoints(endpoint):
    with pytest.raises(ValueError):
        DecisionClient(endpoint, "fake-key")


def test_client_exact_noul_wire_protocol_and_secret_free_repr(monkeypatch):
    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return BytesIO(b'{"answers":{"candidate_0":{"noul":0.9},"candidate_1":{"noul":0.1}}}')

    monkeypatch.setattr(decision, "build_opener", lambda handler: Opener())
    client = DecisionClient("https://endpoint.invalid/score", "fake-key", "Decision-1.1", timeout=3)
    assert "fake-key" not in repr(client) and "endpoint.invalid" not in repr(client)
    assert client.score("bounded state", {"candidate_0": "first", "candidate_1": "second"}) == {
        "candidate_0": .9, "candidate_1": .1,
    }
    request, timeout = seen[0]
    assert timeout == 3 and request.get_header("Api-key") == "fake-key"
    assert json.loads(request.data) == {
        "model": "Decision-1.1", "state": "bounded state",
        "questions": {"candidate_0": {"type": "noul", "instructions": "first"},
                      "candidate_1": {"type": "noul", "instructions": "second"}},
    }
    assert decision._NoRedirect().redirect_request(None, None, 302, "", {}, "https://else.invalid") is None


@pytest.mark.parametrize("body", [b'not json', b'{}', b'{"answers":null}',
                                    b'{"answers":{"candidate_0":{"text":"rewrite"}}}',
                                    b'{"answers":{},"answers":{"candidate_0":{"noul":0.1},"candidate_1":{"noul":0.9}}}',
                                    b'{"answers":{"injected":{"noul":1}}}', b'x' * 65537])
def test_client_malformed_body_falls_back(tmp_path, monkeypatch, body):
    class Opener:
        def open(self, request, timeout):
            return BytesIO(body)

    monkeypatch.setattr(decision, "build_opener", lambda handler: Opener())
    page, layout = sample(tmp_path)
    result = resolve_page(page, layout, config=DecisionConfig(enabled=True),
                          provider=DecisionClient("https://endpoint.invalid", "fake-key"))
    assert result.layout is layout and result.decisions[0].reason == "invalid_answer"


def test_live_probe_fixtures_have_predeclared_scopes_and_ground_truth(tmp_path):
    from pathlib import Path
    import runpy

    probe = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools" / "decision_live_probe.py"))
    for kind in ("measurements_4", "measurements_8", "prose_columns", "nested_pairs", "no_ambiguity"):
        page, layout, expected, _, _ = probe["fixture"](tmp_path / f"{kind}.pdf", kind)
        assert len(layout.alternatives) == (0 if kind == "no_ambiguity" else 1)
        rows = kind.startswith(("measurements", "nested"))
        provider = FakeProvider({"candidate_0": .1 if rows else .9, "candidate_1": .9 if rows else .1})
        result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
        assert source_text(page, result.layout) == expected
        assert_conserved(page, layout, result.layout)
        assert probe["unchanged_outside"](layout, result.layout)
