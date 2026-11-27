"""Evaluation integrity checks; no credentials or network."""

import json

import pytest

from tools import decision_evaluation as evaluation


class Client:
    model = "offline-test"

    def __init__(self, error=False):
        self.calls = 0
        self.error = error

    def score(self, state, questions):
        self.calls += 1
        if self.error:
            raise ValueError("private upstream error")
        return {"candidate_0": .2, "candidate_1": .45}


@pytest.mark.parametrize("failure", [False, True])
def test_shared_answer_replays_one_attempt_including_failure(failure):
    client = Client(error=failure)
    provider = evaluation.SharedAnswer(client)
    for _ in range(2):
        if failure:
            with pytest.raises(RuntimeError, match="^Evaluation provider failure$"):
                provider.score("state", {"candidate_0": "question"})
        else:
            assert provider.score("state", {"candidate_0": "question"}) == {
                "candidate_0": .2, "candidate_1": .45}
    assert client.calls == provider.calls == 1
    with pytest.raises(AssertionError):
        provider.score("changed state", {"candidate_0": "question"})
    provider.calls = 12
    provider.start_item()
    with pytest.raises(RuntimeError, match="budget exhausted"):
        provider.score("state", {"candidate_0": "question"})
    assert client.calls == 1


def test_frozen_fixture_gold_and_paired_policy_metrics(tmp_path):
    protocol = json.loads(evaluation.PROTOCOL.read_text())
    fixtures = evaluation.preflight(protocol["items"], tmp_path)

    class BalancedClient(Client):
        def score(self, state, questions):
            index = self.calls % 6
            self.calls += 1
            # Row corrections; continuous prose; uncertainty on opaque items.
            scores = (.2, .45) if index < 2 else (.45, .2) if index < 4 else (.30, .31)
            return dict(zip(("candidate_0", "candidate_1"), scores))

    client = BalancedClient()
    provider = evaluation.SharedAnswer(client)
    rows = [evaluation.evaluate_item(item, fixtures[item["id"]], provider, protocol["policies"])
            for item in protocol["items"]]
    assert client.calls == provider.calls == 12
    assert evaluation.passes(rows[:6]) and evaluation.passes(rows[6:])
    summary = evaluation.summarize(rows[6:])
    assert summary["rows"]["baseline"]["successes"] == 0
    assert summary["rows"]["challenger"]["corrections"] == 2
    assert summary["prose"]["challenger"]["regressions"] == 0
    assert summary["low_evidence"]["challenger"]["abstains"] == 2
    # A single unjustified low-evidence selection must reject the challenger.
    rows[-1]["challenger"].update(success=False, outcome="applied")
    assert not evaluation.passes(rows[6:])


def test_missing_response_cannot_satisfy_acceptance(tmp_path):
    protocol = json.loads(evaluation.PROTOCOL.read_text())
    fixtures = evaluation.preflight(protocol["items"], tmp_path)
    provider = evaluation.SharedAnswer(Client(error=True))
    rows = [evaluation.evaluate_item(item, fixtures[item["id"]], provider, protocol["policies"])
            for item in protocol["items"][:6]]
    assert provider.calls == 6
    assert not any(row["valid_response"] for row in rows)
    assert not evaluation.passes(rows)
    assert all(row[policy]["reason"] == "provider_failure"
               for row in rows for policy in ("baseline", "challenger"))
