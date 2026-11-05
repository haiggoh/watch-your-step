"""watch-your-step: the SessionStart primer — every rule carries a one-line reminder, within budget.

The primer is PREVENTION: it tells the model each trap once at session start, so the PreToolUse
guard stays a safety net that rarely fires. This file is the barrier — a rule added without a
primer fails the suite, and so does a primer over budget."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES = json.loads((ROOT / "rules" / "rules.json").read_text())["rules"]

PRIMER_MAX_LINE = 110    # chars per rule's primer
PRIMER_MAX_TOTAL = 2400  # chars for all primers together (the injected block's size budget)


def test_every_rule_has_a_primer_within_budget():
    total = 0
    for rule in RULES:
        primer = rule.get("primer")
        assert isinstance(primer, str) and primer.strip(), f"{rule['id']} has no primer"
        assert len(primer) <= PRIMER_MAX_LINE, \
            f"{rule['id']}: primer is {len(primer)} chars (max {PRIMER_MAX_LINE}): {primer!r}"
        total += len(primer)
    assert total <= PRIMER_MAX_TOTAL, f"primers total {total} chars (budget {PRIMER_MAX_TOTAL})"
