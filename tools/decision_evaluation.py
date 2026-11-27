"""One bounded opt-in evaluation; generated items only, credentials stay in memory.

Preflight without credentials, then pass its frozen_sha256 with --run-live.
Neither raw responses, excerpts, credential paths nor endpoints enter reports.
"""

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from reportlab.pdfgen.canvas import Canvas

from pdfgyeol import DecisionClient, DecisionConfig, extract_pdf, infer_page, resolve_page


PROTOCOL = Path(__file__).with_name("decision_evaluation_protocol.json")
ROOT = Path(__file__).resolve().parents[1]


def frozen_digest():
    paths = (PROTOCOL, Path(__file__), ROOT / "src/pdfgyeol/decision.py",
             ROOT / "src/pdfgyeol/layout.py")
    return sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()


def make_fixture(path, item):
    heading = "Generated evaluation sheet: outside active region"
    footer = "End of generated evaluation sheet: outside active region"
    canvas = Canvas(str(path), pagesize=(600, 800), invariant=1)
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(40, 765, heading)
    canvas.drawString(40, 90, footer)
    canvas.setFont("Helvetica", 10)
    for index, (left, right) in enumerate(item["pairs"]):
        canvas.drawString(40, 680 - index * 30, left)
        canvas.drawString(340, 680 - index * 30, right)
    canvas.save()
    page = extract_pdf(path).pages[0]
    layout = infer_page(page)
    pairs = item["pairs"]
    expected = {
        "rows": heading + "".join(a + b for a, b in pairs) + footer,
        "columns": heading + "".join(a for a, _ in pairs) + "".join(b for _, b in pairs) + footer,
    }
    return page, layout, expected


def text_order(page, layout):
    text = {fragment.id: fragment.text for fragment in page.fragments}
    return "".join(text[i] for i in layout.ordered_fragment_ids())


def check_invariants(page, before, result, source):
    assert asdict(page) == source
    assert Counter(result.layout.ordered_fragment_ids()) == Counter(f.id for f in page.fragments)
    assert result.layout.lines == before.lines and result.layout.regions == before.regions
    assert result.layout.tables == before.tables
    assert result.layout.alternatives == before.alternatives
    assert result.layout.warnings == before.warnings
    active = set(before.alternatives[0].candidates[0].region_ids)
    assert tuple(r for r in before.reading_order if r not in active) == tuple(
        r for r in result.layout.reading_order if r not in active)


class FixedProvider:
    model = "offline-preflight"

    def __init__(self, winner):
        self.winner = winner

    def score(self, state, questions):
        # Outside-region sentinels must never reach the provider.
        assert "Generated evaluation sheet" not in state
        assert "End of generated" not in state
        return {key: .9 if key == f"candidate_{self.winner}" else .1 for key in questions}


def preflight(items, temp):
    assert len(items) == 12 and len({i["id"] for i in items}) == 12
    assert Counter((i["split"], i["class"]) for i in items) == {
        (split, cls): 2 for split in ("development", "holdout")
        for cls in ("rows", "prose", "low_evidence")}
    fixtures = {}
    for item in items:
        assert item["gold"] == {"rows": "rows", "prose": "columns", "low_evidence": "abstain"}[item["class"]]
        page, layout, expected = make_fixture(Path(temp) / (item["id"] + ".pdf"), item)
        assert len(layout.alternatives) == 1, item["id"]
        alternative = layout.alternatives[0]
        assert tuple(c.name for c in alternative.candidates) == ("columns", "rows")
        assert text_order(page, layout) == expected["columns"]
        for winner, name in enumerate(("columns", "rows")):
            result = resolve_page(page, layout, config=DecisionConfig(enabled=True),
                                  provider=FixedProvider(winner))
            assert text_order(page, result.layout) == expected[name], item["id"]
            check_invariants(page, layout, result, asdict(page))
        fixtures[item["id"]] = (page, layout, expected)
    return fixtures


class SharedAnswer:
    """One live attempt; replay exact same state/questions/answer for challenger."""

    def __init__(self, client):
        self.client = client
        self.model = client.model
        self.calls = 0
        self.max_bytes = 0
        self.cache = None

    def start_item(self):
        self.cache = None

    def score(self, state, questions):
        if self.cache is not None:
            old_state, old_questions, answer = self.cache
            assert state == old_state and questions == old_questions
        else:
            if self.calls >= 12:
                raise RuntimeError("Evaluation call budget exhausted")
            self.calls += 1
            self.max_bytes = max(self.max_bytes, len(state.encode("utf-8")))
            try:
                answer = self.client.score(state, questions)
            except Exception:
                # Preserve failure for replay, never retain exception text.
                self.cache = (state, questions.copy(), None)
                raise RuntimeError("Evaluation provider failure") from None
            self.cache = (state, questions.copy(), answer)
        if answer is None:
            raise RuntimeError("Evaluation provider failure")
        return answer


def success(item, page, result, expected):
    if item["gold"] == "abstain":
        return result.decisions[0].outcome == "deferred"
    return text_order(page, result.layout) == expected[item["gold"]]


def evaluate_item(item, fixture, provider, policies):
    page, layout, expected = fixture
    source = asdict(page)
    provider.start_item()
    before_calls = provider.calls
    disabled = resolve_page(page, layout, provider=provider)
    assert disabled.layout is layout and provider.calls == before_calls
    row = {"id": item["id"], "class": item["class"], "gold": item["gold"],
           "geometry_success": False if item["gold"] == "abstain" else
           text_order(page, layout) == expected[item["gold"]]}
    for name, thresholds in policies.items():
        result = resolve_page(page, layout, config=DecisionConfig(enabled=True, **thresholds), provider=provider)
        check_invariants(page, layout, result, source)
        record = result.decisions[0]
        row[name] = {"success": success(item, page, result, expected),
                     "outcome": record.outcome, "reason": record.reason,
                     "selected": record.candidate_names[record.selected_index],
                     "scores": list(record.scores)}
    assert provider.calls == before_calls + 1
    assert row["baseline"]["scores"] == row["challenger"]["scores"]
    row["valid_response"] = bool(row["baseline"]["scores"])
    return row


def summarize(rows):
    summary = {}
    for cls in ("rows", "prose", "low_evidence"):
        group = [r for r in rows if r["class"] == cls]
        entry = {"items": len(group), "geometry_successes": sum(r["geometry_success"] for r in group)}
        for policy in ("baseline", "challenger"):
            entry[policy] = {
                "successes": sum(r[policy]["success"] for r in group),
                "abstains": sum(r[policy]["outcome"] == "deferred" for r in group),
                "selections": sum(r[policy]["outcome"] != "deferred" for r in group),
                "corrections": sum(not r["baseline"]["success"] and r[policy]["success"] for r in group),
                "regressions": sum(r["baseline"]["success"] and not r[policy]["success"] for r in group),
            }
        summary[cls] = entry
    return summary


def passes(rows):
    summary = summarize(rows)
    return (len(rows) == 6 and all(r["valid_response"] for r in rows)
            and summary["rows"]["challenger"]["corrections"] >= 1
            and all(entry["challenger"]["regressions"] == 0 for entry in summary.values())
            and summary["low_evidence"]["challenger"]["abstains"] == 2)


def read_client(path):
    allowed = {"AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "DECISION_MODEL_DEPLOYMENT"}
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            if key.strip() in allowed:
                values[key.strip()] = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip('"').strip("'")
        if values.get("DECISION_MODEL_DEPLOYMENT", "Decision-1.1") != "Decision-1.1":
            raise ValueError("Unexpected deployment")
        return DecisionClient(values.get("AZURE_OPENAI_ENDPOINT", ""),
                              values.get("AZURE_OPENAI_API_KEY", ""), "Decision-1.1")
    except (OSError, ValueError):
        raise SystemExit("Authorized configuration unavailable; no requests sent") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--frozen-sha256")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    # Evaluation rule implementation assumes these two exact policies and their order.
    assert protocol["policies"] == {"baseline": {"min_score": .55, "min_margin": .10},
                                    "challenger": {"min_score": .35, "min_margin": .10}}
    digest = frozen_digest()
    if args.run_live and (args.frozen_sha256 != digest or args.env_file is None):
        raise SystemExit("Live run requires preflight frozen hash and explicit authorized env file")
    with TemporaryDirectory(prefix="pdfgyeol-new-synthetic-") as temp:
        fixtures = preflight(protocol["items"], temp)
        report = {"protocol": protocol["id"], "frozen_sha256": digest, "preflight_items": len(fixtures)}
        if args.run_live:
            provider = SharedAnswer(read_client(args.env_file))
            development = [evaluate_item(i, fixtures[i["id"]], provider, protocol["policies"])
                           for i in protocol["items"] if i["split"] == "development"]
            # The only challenger was nominated before calls; eligibility is decided on development only.
            eligible = passes(development)
            print(json.dumps({"development_eligible_before_holdout": eligible}), flush=True)
            holdout = [evaluate_item(i, fixtures[i["id"]], provider, protocol["policies"])
                       for i in protocol["items"] if i["split"] == "holdout"]
            report.update({"calls": provider.calls, "max_state_bytes": provider.max_bytes,
                           "development_eligible_before_holdout": eligible,
                           "holdout_acceptance": eligible and passes(holdout),
                           "defaults_retained": True, "invariants_passed": True,
                           "development": {"classes": summarize(development), "items": development},
                           "holdout": {"classes": summarize(holdout), "items": holdout}})
    serialized = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
