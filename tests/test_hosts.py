"""Provider HTTP contracts using fake credentials and responses."""

from io import BytesIO
import json
from traceback import format_exception
from urllib.error import HTTPError
from urllib.request import ProxyHandler

import pytest

from pdfgyeol import DecisionClient
import pdfgyeol.decision as decision


QUESTIONS = {"candidate_0": "first", "candidate_1": "second"}
PREDICATES = [
    {"name": "candidate_1", "type": "predicate", "probability": .9},
    {"name": "candidate_0", "type": "predicate", "probability": .1},
]
NOULS = {"candidate_1": {"type": "noul", "noul": .9}, "candidate_0": {"noul": .1}}


@pytest.fixture
def mock_http(monkeypatch):
    def install(body):
        calls = []

        class Response(BytesIO):
            def read(self, size=-1):
                assert size == 65537
                return super().read(size)

        class Opener:
            def __init__(self, *handlers):
                self.handlers = handlers

            def open(self, request, timeout):
                calls.append((request, timeout, self.handlers))
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                return Response(payload)

        monkeypatch.setattr(decision, "build_opener", Opener)
        return calls

    return install


@pytest.mark.parametrize("host, options, url, model, auth, wire", [
    ("jev", {"apikey": "jev-backend-key"}, "https://api.typesafe.ai/v1/systemone",
     "jev-latest", {"Authorization": "Bearer jev-backend-key"}, "noul"),
    ("openai", {"apikey": "openai-backend-key"}, "https://api.openai.com/v1/decisions",
     "gpt-6-luna", {"Authorization": "Bearer openai-backend-key"}, "predicate"),
    ("ms", {"endpoint": "https://azure.invalid/score", "apikey": "azure-backend-key"},
     "https://azure.invalid/score", "Decision-1.1", {"Api-key": "azure-backend-key"}, "noul"),
    ("ms", {"endpoint": "https://azure.invalid/api/score?version=1", "apikey": "ms-key",
            "model": "foundry-deployment"}, "https://azure.invalid/api/score?version=1",
     "foundry-deployment", {"Api-key": "ms-key"}, "noul"),
    ("custom", {"endpoint": "http://127.0.0.3:8765/local/systemone"},
     "http://127.0.0.3:8765/local/systemone", "jev-latest", {}, "noul"),
    ("custom", {"endpoint": "https://custom.invalid/score", "apikey": "custom-key",
                "model": "custom-model"}, "https://custom.invalid/score", "custom-model",
     {"Authorization": "Bearer custom-key"}, "noul"),
    ("local", {"endpoint": "http://[::1]:8765/v1/decisions", "apikey": "local-key",
               "model": "local-predicate-model"}, "http://[::1]:8765/v1/decisions",
     "local-predicate-model", {"Authorization": "Bearer local-key"}, "predicate"),
    ("custom", {"endpoint": "https://custom.invalid/v1/decisions/?version=1"},
     "https://custom.invalid/v1/decisions/?version=1", "gpt-6-luna", {}, "predicate"),
    ("openai", {"endpoint": "https://custom.invalid/score", "apikey": "alternate-key",
                "model": "alternate-model"}, "https://custom.invalid/score", "alternate-model",
     {"Authorization": "Bearer alternate-key"}, "predicate"),
])
def test_host_wire_protocol(host, options, url, model, auth, wire, mock_http):
    calls = mock_http({"answers": PREDICATES if wire == "predicate" else NOULS})
    client = DecisionClient.from_host(host, **options)
    assert client.score("bounded state", QUESTIONS) == {"candidate_0": .1, "candidate_1": .9}
    request, timeout, handlers = calls[0]
    assert len(calls) == 1 and timeout == 20.0
    assert request.get_method() == "POST" and request.full_url == url
    assert dict(request.header_items()) == {"Content-type": "application/json", **auth}
    assert isinstance(handlers[0], decision._NoRedirect)
    if url.startswith("http://"):
        assert isinstance(handlers[1], ProxyHandler) and handlers[1].proxies == {}
    provenance_host = "custom" if host == "local" else host
    assert decision._provenance(client) == (provenance_host, model)
    if wire == "predicate":
        assert json.loads(request.data) == {"model": model, "input": "bounded state", "questions": [
            {"name": name, "type": "predicate", "instructions": instructions}
            for name, instructions in QUESTIONS.items()
        ]}
    else:
        assert json.loads(request.data) == {"model": model, "state": "bounded state", "questions": {
            name: {"type": "noul", "instructions": instructions}
            for name, instructions in QUESTIONS.items()
        }}


def test_selected_host_never_uses_environment_credentials(monkeypatch, mock_http):
    for name, value in {
        "AZURE_OPENAI_ENDPOINT": "https://azure.invalid/score",
        "AZURE_OPENAI_API_KEY": "azure-env-key",
        "DECISION_MODEL_DEPLOYMENT": "azure-env-deployment",
        "OPENAI_API_KEY": "openai-env-key",
        "TYPESAFE_API_KEY": "typesafe-env-key",
    }.items():
        monkeypatch.setenv(name, value)

    for host in ("jev", "openai", "ms"):
        with pytest.raises(ValueError, match="API key"):
            DecisionClient.from_host(host, endpoint="https://provided.invalid/score")
    with pytest.raises(ValueError, match="explicit endpoint"):
        DecisionClient.from_host("ms", apikey="supplied-key")

    calls = mock_http({"answers": NOULS})
    custom = DecisionClient.from_host("local", endpoint="http://localhost:8765/systemone")
    custom.score("bounded state", QUESTIONS)
    request, _, _ = calls[0]
    assert request.get_header("Authorization") is None
    assert request.get_header("Api-key") is None
    assert json.loads(request.data)["model"] == "jev-latest"

    legacy = DecisionClient.from_env()
    assert decision._provenance(legacy) == ("DecisionClient", "azure-env-deployment")
    legacy.score("bounded state", QUESTIONS)
    request, _, _ = calls[1]
    assert request.full_url == "https://azure.invalid/score"
    assert request.get_header("Api-key") == "azure-env-key"


@pytest.mark.parametrize("host, endpoint", [
    ("openai", "http://127.0.0.1/v1/decisions"),
    ("jev", "http://localhost/v1/systemone"),
    ("ms", "http://[::1]/score"),
    ("custom", "http://remote-secret.invalid/score"),
    ("local", "http://127.0.0.1.remote-secret.invalid/score"),
    ("local", "http://2130706433/score"),
    ("local", "http://127.1/score"),
    ("custom", "https://username:private-secret@remote-secret.invalid/score"),
    ("custom", "https://@remote-secret.invalid/score"),
    ("custom", "https://remote-secret.invalid:70000/score"),
    ("custom", "https://remote-secret.invalid/score#private-secret"),
    ("custom", "https://remote-secret.invalid\n/score"),
    ("custom", "remote-secret.invalid/score"),
    ("custom", "https://[private-secret/score"),
])
def test_declined_endpoint_reports_safe_configuration_error(host, endpoint):
    key = "private-secret"
    with pytest.raises(ValueError) as error:
        DecisionClient.from_host(host, endpoint=endpoint, apikey=key)
    trace = "".join(format_exception(error.value))
    assert "remote-secret" not in trace and key not in trace


@pytest.mark.parametrize("host, options", [
    ("private-host-name", {}),
    ("jev", {"apikey": "private\nheader"}),
    ("jev", {"apikey": "supplied-key", "model": " "}),
    ("custom", {}),
])
def test_invalid_host_options_raise_value_error(host, options):
    with pytest.raises(ValueError):
        DecisionClient.from_host(host, **options)


@pytest.mark.parametrize("body", [
    {"answers": NOULS},
    {"answers": PREDICATES[:1]},
    {"answers": PREDICATES + [PREDICATES[1]]},
    {"answers": [*PREDICATES, {"name": "unexpected", "type": "predicate", "probability": .5}]},
    {"answers": [PREDICATES[0], {**PREDICATES[1], "name": []}]},
    {"answers": [PREDICATES[0], {**PREDICATES[1], "type": "refusal"}]},
    {"answers": [PREDICATES[0], {**PREDICATES[1], "type": "noul"}]},
    {"answers": [PREDICATES[0], {**PREDICATES[1], "type": None}]},
    {"answers": [PREDICATES[0], {**PREDICATES[1], "probability": True}]},
    {"answers": [PREDICATES[0], {**PREDICATES[1], "probability": "0.1"}]},
    {"answers": [PREDICATES[0], {**PREDICATES[1], "probability": float("nan")}]},
    {"answers": [PREDICATES[0], {**PREDICATES[1], "probability": 1.1}]},
    {"answers": PREDICATES, "refusal": "private-refusal-detail"},
    b'{"answers":[],"answers":[]}',
    b" " * 65536 + b"{}",
])
def test_openai_rejects_malformed_or_refused_answers_without_details(body, mock_http):
    calls = mock_http(body)
    client = DecisionClient.from_host("openai", apikey="private-test-key")
    with pytest.raises(ValueError, match="^Malformed Decision answer$") as error:
        client.score("bounded state", QUESTIONS)
    assert len(calls) == 1
    assert "private-refusal-detail" not in "".join(format_exception(error.value))


def test_provenance_and_transport_failure_hide_configuration(monkeypatch):
    endpoint = "https://custom.invalid/score?route=private-route"
    key = "private-test-key"

    class FailedOpener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 503, key, {}, BytesIO())

    monkeypatch.setattr(decision, "build_opener", lambda handler: FailedOpener())
    for model in (key, endpoint):
        client = DecisionClient.from_host("custom", endpoint=endpoint, apikey=key, model=model)
        assert key not in repr(client) and endpoint not in repr(client)
        assert decision._provenance(client) == ("custom", None)
        with pytest.raises(OSError, match="^Decision request failed$") as error:
            client.score("bounded state", QUESTIONS)
        trace = "".join(format_exception(error.value))
        assert key not in trace and endpoint not in trace
