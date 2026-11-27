"""Exercise the public parsing pipeline with generated local PDFs."""

import json
import os
import subprocess
import sys
from collections import Counter

import pytest
from reportlab.pdfgen.canvas import Canvas

from pdfgyeol import PARSE_SCHEMA_VERSION, DecisionConfig, extract_pdf, infer_page, parse_pdf
from pdfgyeol import cli, decision


@pytest.fixture
def source_pdf(tmp_path):
    path = tmp_path / "field notes.pdf"
    canvas = Canvas(str(path), pagesize=(600, 800), invariant=1)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(40, 745, "Café")
    canvas.drawString(68, 745, "notes  today")  # Positioned word, followed by explicit spaces.
    for x in (40, 130, 250):
        canvas.line(x, 620, x, 670)
    for y in (620, 645, 670):
        canvas.line(40, y, 250, y)
    for x, y, text in [(45, 653, "Peas"), (135, 653, "12"), (45, 628, "Beans")]:
        canvas.drawString(x, y, text)
    canvas.drawString(40, 595, "After table")
    canvas.showPage()
    canvas.setFont("Helvetica", 10)
    for x, y, text in [(40, 700, "Seed"), (340, 700, "Cost"), (40, 670, "Peas"), (340, 670, "12")]:
        canvas.drawString(x, y, text)
    canvas.showPage()
    canvas.showPage()
    canvas.save()
    return path


def unavailable(*args, **kwargs):
    pytest.fail("offline parse accessed the Decision client or network")


def test_api_retains_core_layout_and_serializes_references(source_pdf, monkeypatch):
    monkeypatch.setattr(decision.DecisionClient, "from_env", unavailable)
    monkeypatch.setattr(decision, "build_opener", unavailable)
    document = parse_pdf(source_pdf, provider=object())
    source = extract_pdf(source_pdf)
    assert document.schema_version == PARSE_SCHEMA_VERSION == "pdfgyeol.parse/1"
    assert document.id == source.id and document.sha256 == source.sha256
    assert not document.decision_enabled
    assert document.text == "Café notes  today\nPeas\n12\nBeans\nAfter table\n\f\nSeed\nPeas\nCost\n12\n\f\n"
    assert len(document.pages) == len(source.pages) == 3
    for page, original in zip(document.pages, source.pages, strict=True):
        assert page.source == original
        assert page.layout == infer_page(original)
        assert Counter(page.ordered_fragments()) == Counter(original.fragments)
        assert page.decisions == ()

    table_page = document.pages[0]
    table, = table_page.layout.tables
    table_region = next(region for region in table_page.layout.regions if region.table_id == table.id)
    assert table_page.region_text(table_region.id) == "Peas\n12\nBeans"
    assert [[table_page.cell_text(cell.id) for cell in row.cells] for row in table.rows] == [
        ["Peas", "12"], ["Beans", ""],
    ]
    for lookup in (table_page.region_text, table_page.cell_text):
        with pytest.raises(KeyError):
            lookup("unknown")

    data = document.to_dict()
    assert data == json.loads(document.to_json())
    first = data["pages"][0]
    assert first["source"]["source_bbox"] == {"x0": 0, "top": 0, "x1": 600, "bottom": 800}
    assert first["layout"]["page_id"] == first["source"]["id"]
    assert first["layout"]["reading_order"] == list(table_page.layout.reading_order)
    assert first["layout"]["tables"][0]["id"] == table_region.table_id
    assert data["pages"][2]["source"]["kind"] == "blank"
    assert data["pages"][2]["text"] == ""
    assert str(source_pdf) not in document.to_json()


def test_cli_matches_api_on_stdout_file_and_module_under_ascii_locale(source_pdf, tmp_path, capsysbinary):
    expected = parse_pdf(source_pdf).to_json().encode("utf-8")
    args = [str(source_pdf)]
    assert cli.main(args) == 0
    stdout, stderr = capsysbinary.readouterr()
    assert stdout == expected and "Café".encode() in stdout and not stderr
    output = tmp_path / "결과.json"
    assert cli.main([*args, "--output", str(output)]) == 0
    assert capsysbinary.readouterr() == (b"", b"")
    assert output.read_bytes() == expected

    result = subprocess.run([sys.executable, "-m", "pdfgyeol", *args], capture_output=True,
                            env={**os.environ, "PYTHONIOENCODING": "ascii"}, check=True)
    assert result.stdout == expected and not result.stderr


@pytest.mark.parametrize("failure", [False, True])
def test_decision_provenance_and_fallback(source_pdf, monkeypatch, capsysbinary, failure):
    class Provider:
        model = "offline test"
        calls = 0

        def score(self, state, questions):
            self.calls += 1
            assert len(state.encode()) < DecisionConfig().max_state_bytes
            assert "Café" not in state and "After table" not in state
            if failure:
                raise OSError("private provider error")
            return {"candidate_0": 0.1, "candidate_1": 0.9}

    provider = Provider()
    local = parse_pdf(source_pdf)
    parsed = parse_pdf(source_pdf, decision_config=DecisionConfig(enabled=True), provider=provider)
    assert parsed.decision_enabled and provider.calls == 1
    baseline, result = local.pages[1], parsed.pages[1]
    record, = result.decisions
    assert record.scope_id == result.source.id
    assert record.alternative_id == result.layout.alternatives[0].id
    assert record.outcome == ("deferred" if failure else "applied")
    assert result.text == (baseline.text if failure else "Seed\nCost\nPeas\n12")
    assert result.source == baseline.source
    assert Counter(result.ordered_fragments()) == Counter(baseline.ordered_fragments())

    monkeypatch.setattr(decision.DecisionClient, "from_env", lambda: provider)
    assert cli.main([str(source_pdf), "--decision"]) == 0
    stdout, stderr = capsysbinary.readouterr()
    assert json.loads(stdout) == parsed.to_dict()
    assert provider.calls == 2
    assert stderr == (b"pdfgyeol: Decision deferred: provider_failure\n" if failure else b"")
    assert b"private provider error" not in stdout


def test_opt_in_with_no_credentials_defers(source_pdf, monkeypatch, capsysbinary):
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "DECISION_MODEL_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(decision, "build_opener", unavailable)
    assert cli.main([str(source_pdf), "--decision"]) == 0
    stdout, stderr = capsysbinary.readouterr()
    assert json.loads(stdout)["pages"][1]["decisions"][0]["reason"] == "not_configured"
    assert b"not_configured" in stderr


@pytest.mark.parametrize("kind, message", [
    ("missing", "existing local PDF"), ("malformed", "could not parse"), ("encrypted", "password argument"),
])
def test_cli_bad_input_has_no_partial_output(tmp_path, capsys, kind, message):
    source = tmp_path / "bad.pdf"
    destination = tmp_path / "result.json"
    if kind == "malformed":
        source.write_bytes(b"broken document")
    if kind == "encrypted":
        canvas = Canvas(str(source), encrypt="synthetic-test-password", invariant=1)
        canvas.drawString(30, 300, "Private lab")
        canvas.save()
        assert parse_pdf(source, password="synthetic-test-password").text == "Private lab"

    for args in ([str(source)], [str(source), "-o", str(destination)]):
        assert cli.main(args) == 1
        stdout, stderr = capsys.readouterr()
        assert not stdout and message in stderr
    assert not destination.exists() and not list(tmp_path.glob(".pdfgyeol-*"))


def test_cli_rejects_input_aliases_and_existing_destinations(source_pdf, tmp_path, capsys):
    before = source_pdf.read_bytes()
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(source_pdf)
    hardlink = tmp_path / "hardlink.json"
    os.link(source_pdf, hardlink)
    for destination in (source_pdf, symlink, hardlink):
        assert cli.main([str(source_pdf), "-o", str(destination)]) == 1
        assert "output must differ from input" in capsys.readouterr().err
        assert destination.read_bytes() == before

    existing = tmp_path / "existing.json"
    existing.write_text("keep")
    dangling = tmp_path / "dangling.json"
    dangling.symlink_to(tmp_path / "absent.json")
    for destination in (existing, dangling, tmp_path):
        assert cli.main([str(source_pdf), "-o", str(destination)]) == 1
        assert "output already exists" in capsys.readouterr().err
    assert existing.read_text() == "keep" and dangling.is_symlink()
    assert cli.main([str(source_pdf), "-o", str(tmp_path / "absent" / "result.json")]) == 1
    assert "parent directory must exist" in capsys.readouterr().err


def test_atomic_output_cleans_temporary_file_after_io_failure(source_pdf, tmp_path, monkeypatch, capsys):
    def fail_sync(fd):
        raise OSError("private filesystem detail")

    monkeypatch.setattr(cli.os, "fsync", fail_sync)
    destination = tmp_path / "result.json"
    assert cli.main([str(source_pdf), "-o", str(destination)]) == 1
    stdout, stderr = capsys.readouterr()
    assert not stdout and "OSError" in stderr and "private filesystem detail" not in stderr
    assert not destination.exists() and not list(tmp_path.glob(".pdfgyeol-*"))


def test_atomic_output_refuses_hardlink_created_during_write(source_pdf, tmp_path, monkeypatch, capsys):
    before = source_pdf.read_bytes()
    link = os.link

    def create_competing_link(temporary, destination):
        link(source_pdf, destination)
        link(temporary, destination)

    monkeypatch.setattr(cli.os, "link", create_competing_link)
    destination = tmp_path / "result.json"
    assert cli.main([str(source_pdf), "-o", str(destination)]) == 1
    assert "output already exists" in capsys.readouterr().err
    assert os.path.samefile(destination, source_pdf) and source_pdf.read_bytes() == before
    assert not list(tmp_path.glob(".pdfgyeol-*"))
