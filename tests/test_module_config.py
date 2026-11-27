"""Public module configuration with synthetic PDFs and no network."""

import os

import pytest
from reportlab.pdfgen.canvas import Canvas

import pdfgyeol
from pdfgyeol.api import parse_pdf


@pytest.fixture
def column_pdf(tmp_path):
    path = tmp_path / "columns.pdf"
    canvas = Canvas(str(path), pagesize=(600, 800), invariant=1)
    for _ in range(2):
        canvas.setFont("Helvetica", 10)
        canvas.drawString(40, 700, "Seed")
        canvas.drawString(340, 700, "Cost")
        canvas.drawString(40, 670, "Peas")
        canvas.drawString(340, 670, "12")
        canvas.showPage()
    canvas.save()
    return path


def forbidden(*args, **kwargs):
    pytest.fail("unexpected Decision provider selection")


def test_module_config_applies_once_per_document(column_pdf, monkeypatch):
    monkeypatch.setattr(pdfgyeol, "host", "custom")
    monkeypatch.setattr(pdfgyeol, "endpoint", "https://first.invalid/score")
    monkeypatch.setattr(pdfgyeol, "apikey", "first-test-key")
    monkeypatch.setattr(pdfgyeol, "model", "first-model")
    monkeypatch.setattr(pdfgyeol.DecisionClient, "from_env", forbidden)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://env.invalid/score")
    env = dict(os.environ)
    seen = []

    def score(client, state, questions):
        seen.append(client)
        if len(seen) == 1:
            monkeypatch.setattr(pdfgyeol, "endpoint", "https://second.invalid/score")
            monkeypatch.setattr(pdfgyeol, "apikey", "second-test-key")
            monkeypatch.setattr(pdfgyeol, "model", "second-model")
        return {"candidate_0": 0.1, "candidate_1": 0.9}

    monkeypatch.setattr(pdfgyeol.DecisionClient, "score", score)
    first = pdfgyeol.parse_pdf(column_pdf)
    assert first.decision_enabled
    assert all(page.text == "Seed\nCost\nPeas\n12" for page in first.pages)
    assert len(seen) == 2 and seen[0] is seen[1]
    assert seen[0].endpoint == "https://first.invalid/score"
    assert seen[0].api_key == "first-test-key"
    assert [page.decisions[0].model for page in first.pages] == ["first-model"] * 2

    second = parse_pdf(column_pdf)
    assert len(seen) == 4 and seen[2] is seen[3]
    assert seen[2].endpoint == "https://second.invalid/score"
    assert seen[2].api_key == "second-test-key"
    assert [page.decisions[0].model for page in second.pages] == ["second-model"] * 2
    assert dict(os.environ) == env


def test_explicit_disabled_config_skips_module_settings(column_pdf, monkeypatch):
    monkeypatch.setattr(pdfgyeol, "host", "invalid")
    monkeypatch.delattr(pdfgyeol, "apikey")
    monkeypatch.setattr(pdfgyeol.DecisionClient, "from_host", forbidden)
    monkeypatch.setattr(pdfgyeol.DecisionClient, "from_env", forbidden)
    document = parse_pdf(column_pdf, decision_config=pdfgyeol.DecisionConfig(enabled=False))
    assert not document.decision_enabled
    assert all(page.decisions == () for page in document.pages)


def test_per_call_provider_overrides_module_host(column_pdf, monkeypatch):
    class Provider:
        model = "override"

        def score(self, state, questions):
            return {"candidate_0": 0.1, "candidate_1": 0.9}

    monkeypatch.setattr(pdfgyeol, "host", "custom")
    monkeypatch.delattr(pdfgyeol, "endpoint")
    monkeypatch.setattr(pdfgyeol.DecisionClient, "from_host", forbidden)
    monkeypatch.setattr(pdfgyeol.DecisionClient, "from_env", forbidden)
    provider = Provider()

    enabled = parse_pdf(column_pdf, provider=provider)
    assert enabled.decision_enabled
    assert [page.decisions[0].model for page in enabled.pages] == ["override"] * 2

    config = pdfgyeol.DecisionConfig(enabled=True, min_score=0.95)
    higher_threshold = parse_pdf(column_pdf, provider=provider, decision_config=config)
    assert all(page.decisions[0].reason == "ambiguous_scores" for page in higher_threshold.pages)


def test_without_host_module_values_and_provider_do_not_enable(column_pdf, monkeypatch):
    monkeypatch.setattr(pdfgyeol, "endpoint", "https://ignored.invalid/score")
    monkeypatch.setattr(pdfgyeol, "apikey", "ignored-test-key")
    monkeypatch.setattr(pdfgyeol, "model", "ignored-model")
    monkeypatch.setattr(pdfgyeol.DecisionClient, "from_host", forbidden)
    monkeypatch.setattr(pdfgyeol.DecisionClient, "from_env", forbidden)
    document = parse_pdf(column_pdf, provider=object())
    assert not document.decision_enabled
    assert all(page.decisions == () for page in document.pages)


@pytest.mark.parametrize(("host", "message"), [("custom", "endpoint"), ("invalid", "host")])
def test_invalid_module_host_config_raises_without_env_fallback(column_pdf, monkeypatch, host, message):
    monkeypatch.setattr(pdfgyeol, "host", host)
    monkeypatch.setattr(pdfgyeol.DecisionClient, "from_env", forbidden)
    with pytest.raises(ValueError, match=message):
        parse_pdf(column_pdf)
