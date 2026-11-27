"""Opt-in live check on generated public-domain text, never real user documents.

Run from an installed/editable checkout with the test extra:
python tools/decision_live_probe.py --env-file /authorized/path/.env --run-live
Only outcome aggregates leave this script on stdout. Credentials stay in memory.
"""

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from reportlab.pdfgen.canvas import Canvas

from pdfgyeol import DecisionClient, DecisionConfig, extract_pdf, infer_page, resolve_page


def fixture(path: Path, kind: str):
    c = Canvas(str(path), pagesize=(600, 800), invariant=1)
    c.setFont("Helvetica-Bold", 14)
    heading = "Observation notebook for the North Ridge field research station"
    footer = "End of observations for the North Ridge field research station"
    c.drawString(40, 765, heading)
    c.drawString(40, 90, footer)
    c.setFont("Helvetica", 10)
    if kind == "nested_pairs":
        for xs, ys in [([40, 330, 560], [350, 730]), ([55, 285, 320], [450, 710])]:
            for x in xs:
                c.line(x, ys[0], x, ys[-1])
            for y in ys:
                c.line(xs[0], y, xs[-1], y)
        c.drawString(340, 680, "Other outer cell")
        c.drawString(290, 680, "X")
        c.drawString(50, 370, "Parent tail")
        pairs = [("Seed", "Cost"), ("Peas", "12")]
        xs = (65, 200)
    elif kind in ("measurements_4", "measurements_8"):
        pairs = [("Temperature", "18 C"), ("Humidity", "45 %"), ("Pressure", "1012 hPa"),
                 ("Wind", "3 m/s"), ("Rain", "0 mm"), ("Dew point", "9 C"),
                 ("Visibility", "12 km"), ("Elevation", "140 m")]
        pairs = pairs[:4] if kind.endswith("4") else pairs
        xs = (40, 340)
    else:
        pairs = [("The gardener watered", "The observer checked"),
                 ("the seedlings at dawn.", "the instruments at dusk.")]
        if kind == "no_ambiguity":
            pairs += [("The plants grew steadily.", "The air stayed cool."),
                      ("The notes were filed away.", "The report was complete.")]
        xs = (40, 340)
    pitch = 13 if kind == "no_ambiguity" else 30
    for j, (left, right) in enumerate(pairs):
        c.drawString(xs[0], 680 - pitch * j, left)
        c.drawString(xs[1], 680 - pitch * j, right)
    c.save()
    page = extract_pdf(path).pages[0]
    layout = infer_page(page)
    region_text = ("".join(a + b for a, b in pairs) if kind.startswith(("measurements", "nested"))
                   else "".join(a for a, _ in pairs) + "".join(b for _, b in pairs))
    expected = heading + region_text + footer
    if kind == "nested_pairs":
        expected = heading + region_text + "XParent tailOther outer cell" + footer
    return page, layout, expected, heading, footer


class CountedProvider:
    def __init__(self, client):
        self.client = client
        self.model = client.model
        self.calls = 0
        self.request_bytes = []

    def score(self, state, questions):
        self.calls += 1
        self.request_bytes.append(len(state.encode("utf-8")))
        return self.client.score(state, questions)


def unchanged_outside(before, after):
    active = {r for a in before.alternatives for c in a.candidates for r in c.region_ids}
    before_scopes = {before.page_id: before.reading_order}
    after_scopes = {after.page_id: after.reading_order}
    for layout, scopes in ((before, before_scopes), (after, after_scopes)):
        scopes.update({cell.id: cell.region_ids for table in layout.tables for row in table.rows for cell in row.cells})
    return (before.lines == after.lines and before.regions == after.regions
            and before.alternatives == after.alternatives and before.warnings == after.warnings
            and before_scopes.keys() == after_scopes.keys()
            and all(tuple(r for r in ids if r not in active)
                    == tuple(r for r in after_scopes[key] if r not in active)
                    for key, ids in before_scopes.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    # Explicit file chosen by operator; never discover, copy or print its content.
    allowed = {"AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "DECISION_MODEL_DEPLOYMENT"}
    values = {}
    for line in args.env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        if key.strip() in allowed:
            values[key.strip()] = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip('"').strip("'")
    # Refuse another deployment rather than silently substituting a model.
    if values.get("DECISION_MODEL_DEPLOYMENT", "Decision-1.1") != "Decision-1.1":
        raise SystemExit("Authorized configuration is not Decision-1.1; no requests sent")
    try:
        client = DecisionClient(values.get("AZURE_OPENAI_ENDPOINT", ""),
                                values.get("AZURE_OPENAI_API_KEY", ""), "Decision-1.1")
    except ValueError:
        raise SystemExit("Authorized configuration incomplete; no requests sent") from None
    provider = CountedProvider(client)
    aggregate = {"fixtures": 0, "ambiguities": 0, "correct_before": 0, "correct_after": 0,
                 "improved": 0, "regressed": 0, "source_conserved": True,
                 "out_of_scope_unchanged": True, "no_call_when_disabled": True,
                 "no_call_without_ambiguity": True, "outcomes": {}, "reasons": {}}
    outcomes, reasons = Counter(), Counter()
    with TemporaryDirectory(prefix="pdfgyeol-decision-public-") as temp:
        kinds = ("measurements_4", "measurements_8", "prose_columns", "nested_pairs", "no_ambiguity")
        fixtures = [fixture(Path(temp) / f"{kind}.pdf", kind) for kind in kinds]
        assert all(bool(f[1].alternatives) == (kind != "no_ambiguity") for kind, f in zip(kinds, fixtures))
        for page, layout, expected, heading, footer in fixtures:
            before_page = asdict(page)
            calls = provider.calls
            disabled = resolve_page(page, layout, provider=provider)
            assert disabled.layout is layout and provider.calls == calls
            result = resolve_page(page, layout, config=DecisionConfig(enabled=True), provider=provider)
            if not layout.alternatives:
                assert provider.calls == calls
            text = {f.id: f.text for f in page.fragments}
            before = "".join(text[i] for i in layout.ordered_fragment_ids())
            after = "".join(text[i] for i in result.layout.ordered_fragment_ids())
            correct_before, correct_after = before == expected, after == expected
            aggregate["fixtures"] += 1
            aggregate["ambiguities"] += len(layout.alternatives)
            aggregate["correct_before"] += correct_before
            aggregate["correct_after"] += correct_after
            aggregate["improved"] += not correct_before and correct_after
            aggregate["regressed"] += correct_before and not correct_after
            conserved = (Counter(result.layout.ordered_fragment_ids()) == Counter(f.id for f in page.fragments)
                         and asdict(page) == before_page)
            aggregate["source_conserved"] &= conserved
            aggregate["out_of_scope_unchanged"] &= unchanged_outside(layout, result.layout)
            outcomes.update(r.outcome for r in result.decisions)
            reasons.update(r.reason for r in result.decisions)
            # Payload itself and response bodies are intentionally never logged.
            assert after.startswith(heading) and after.endswith(footer)
    aggregate.update({"calls": provider.calls, "max_state_bytes": max(provider.request_bytes, default=0),
                      "outcomes": dict(outcomes), "reasons": dict(reasons)})
    report = json.dumps(aggregate, indent=2, sort_keys=True)
    if args.report:
        args.report.write_text(report + "\n", encoding="utf-8")
    print(report)
    if not aggregate["source_conserved"] or not aggregate["out_of_scope_unchanged"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
