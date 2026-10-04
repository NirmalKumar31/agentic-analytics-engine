"""Audit every design sheet for facts that contradict each other.

A mockup that says 200 rows on one sheet and 240 on another is not a style
problem: it is the same class of defect as a chart whose axis contradicts its
headline, and a reviewer who spots it stops trusting the rest of the package.
"""

import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path("docs/design")
SHEETS = (
    sorted(ROOT.glob("mockups/*.svg"))
    + sorted(ROOT.glob("wireframes/*.svg"))
    + [ROOT / "visual-system.svg"]
)
fails, checks = [], 0


def fail(sheet, msg):
    fails.append(f"{sheet}: {msg}")


def text_of(path):
    root = ET.parse(path).getroot()
    return [(e.text or "") for e in root.iter("{http://www.w3.org/2000/svg}text")]


ALL = {}
for p in SHEETS:
    try:
        ALL[p.name] = text_of(p)
    except ET.ParseError as e:
        fail(p.name, f"not well-formed XML: {e}")
checks += len(SHEETS)

raw = {p.name: p.read_text() for p in SHEETS}

# 1. No unescaped ampersand survived `esc()`.
for name, src in raw.items():
    for m in re.finditer(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;)", src):
        fail(name, f"unescaped & at offset {m.start()}")
    checks += 1

# 2. A fact stated on two sheets must be stated the same way. Each entry is
#    (trigger, required co-occurring text) applied within a single sheet.
PAIRED = [
    ("29,225.00", "140 of 200 rows"),
    ("commerce_demo", "502,819 rows"),
]
for name, texts in ALL.items():
    joined = " | ".join(texts)
    for trigger, required in PAIRED:
        if trigger in joined and required not in joined:
            fail(name, f"states {trigger!r} without {required!r}")
        checks += 1

# 3. Numbers that must never appear with a conflicting partner.
CONFLICTS = [
    ("29,225.00", r"\b(60|128,440|240) of \d", "a total bound to a different row count"),
    ("orders_2024.csv", r"\b240 rows\b", "orders_2024.csv has 200 rows"),
    ("sales.csv", r"\b200 rows\b", "sales.csv has 240 rows"),
]
for name, texts in ALL.items():
    joined = " | ".join(texts)
    for trigger, pattern, why in CONFLICTS:
        if trigger in joined and re.search(pattern, joined):
            fail(name, f"{why} ({trigger!r})")
        checks += 1

# 4. The build SHA is a single real value across the package.
SHA = "5913f6e"
for name, texts in ALL.items():
    for t in texts:
        for m in re.finditer(r"\bsha ([0-9a-f]{7})\b", t):
            if m.group(1) != SHA:
                fail(name, f"build sha {m.group(1)} != {SHA}")
            checks += 1

# 5. Contract hashes: a3f9c1 means "the agreeing contract". b71e04 is the
#    divergent one and may only appear on a divergence sheet.
for name, texts in ALL.items():
    joined = " | ".join(texts)
    if "b71e04" in joined and "diff" not in name:
        fail(name, "divergent contract hash b71e04 on a non-divergence sheet")
    checks += 1

# 6. One terminal state per state sheet.
STATUSES = ["refused", "completed", "failed", "timeout", "budget_exhausted", "cancelled"]
EXPECTED = {
    "refused": "refused",
    "no-findings": "completed",
    "verification-withheld": "completed",
    "quota-stopped": "budget_exhausted",
    "failed": "failed",
    "cancelled": "cancelled",
}
seen_states = set()
for name, texts in ALL.items():
    m = re.match(r"state-(.+)-(light|dark)\.svg$", name)
    if not m:
        continue
    key = m.group(1)
    seen_states.add(key)
    joined = " | ".join(texts)
    if key not in EXPECTED:
        fail(name, f"unknown terminal state {key!r}")
        continue
    want = EXPECTED[key]
    found = {s for s in STATUSES if re.search(rf"\b{s}\b", joined)}
    # `completed` legitimately appears inside the quota sheet's prose? It must not.
    if found != {want}:
        fail(name, f"expected exactly the status {want!r}, found {sorted(found)}")
    checks += 1
    # The sheet must carry one headline register, not two stacked states.
    if joined.count("outcome ") != 1:
        fail(
            name, f"expected exactly one `outcome ...` truth line, found {joined.count('outcome ')}"
        )
    checks += 1

MUST_COVER = set(EXPECTED)
missing = MUST_COVER - seen_states
if missing:
    fails.append(f"missing individual terminal-state sheets: {sorted(missing)}")
checks += 1

# 7. Compare: exactly one evidence action per sheet, never two.
for name, texts in ALL.items():
    if not name.startswith("compare-") or "evidence" in name:
        continue
    joined = " | ".join(texts)
    if joined.count("Inspect both traces") != 2:  # desktop pane + mobile pane
        fail(
            name,
            f"expected one Inspect-both-traces action per pane, found {joined.count('Inspect both traces')}",
        )
    if "Evidence · deterministic" in joined or "Evidence · AI" in joined:
        fail(name, "two persistent per-strategy evidence buttons are still present")
    checks += 2

# 8. The divergence caption must match the table it describes.
for name, texts in ALL.items():
    if "compare-diff" not in name:
        continue
    joined = " | ".join(texts)
    same = len(re.findall(r"\bsame\b", joined))
    differs = len(re.findall(r"\bdiffers\b", joined))
    cap = re.search(r"(\d+) fields agree · (\d+) differ", joined)
    if not cap:
        fail(name, "no agree/differ caption found")
    else:
        # desktop table rows only; the mobile pane repeats `differs` as a label
        if int(cap.group(1)) != same:
            fail(name, f"caption says {cap.group(1)} agree, table has {same}")
        checks += 1

# 9. The composite terminal sheet must say it is documentation.
for name, texts in ALL.items():
    if not re.match(r"terminal-(light|dark)\.svg", name):
        continue
    joined = " | ".join(texts)
    if "DOCUMENTATION COMPOSITE" not in joined.upper():
        fail(name, "composite sheet does not declare itself documentation-only")
    if "state-*" not in joined:
        fail(name, "composite sheet does not point at the individual state sheets")
    checks += 2

# 10. The arithmetic behind the report table, checked rather than eyeballed.

facts_path = ROOT / "facts.json"
if not facts_path.exists():
    fails.append("facts.json missing — run sheets.py")
else:
    facts = json.loads(facts_path.read_text())
    rows = facts["report_table"]["rows"]
    revenues = [int(r[1].replace(",", "")) for r in rows]
    shares = [float(r[2].rstrip("%")) for r in rows]

    if sum(revenues) != facts["report_table"]["revenue_total"]:
        fails.append(
            f"report table revenues sum to {sum(revenues):,}, total says {facts['report_table']['revenue_total']:,}"
        )
    checks += 1
    if abs(sum(shares) - 100.0) > 0.15:
        fails.append(f"share column sums to {sum(shares):.1f}%, not 100%")
    checks += 1
    if revenues != sorted(revenues, reverse=True):
        fails.append("report table is captioned `ranked` but is not in descending revenue order")
    checks += 1
    for r, share in zip(rows, shares, strict=False):
        want = int(r[1].replace(",", "")) / facts["report_table"]["revenue_total"] * 100
        if abs(share - want) > 0.05:
            fails.append(f"{r[0]}: share {share}% != {want:.1f}% of total")
        checks += 1

    m = facts["margin"]
    if abs((m["rate"] + m["mix"]) - m["total"]) > 0.005:
        fails.append(f"margin decomposition {m['rate']} + {m['mix']} != headline {m['total']}")
    checks += 1

    # Every computed cell must actually be on both report sheets.
    for sheet in ("report-light.svg", "report-dark.svg"):
        joined = " | ".join(ALL.get(sheet, []))
        for r in rows:
            for cell in (r[0], r[1], r[2]):
                if cell not in joined:
                    fail(sheet, f"computed cell {cell!r} is not on the sheet")
                checks += 1

print(f"{len(SHEETS)} sheets · {checks} checks")
if fails:
    print(f"\nFAIL ({len(fails)}):")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all consistent")
