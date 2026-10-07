"""Compare: agreement, divergence, and the two-tab evidence drawer.

One `Inspect both traces` action, never two persistent evidence buttons. The
space that reclaims is used: for why the strategies agree, or for the
structured difference when they do not. It is not held empty for symmetry.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from kit import (
    DARK,
    FONT,
    LIGHT,
    MONO,
    SERIES,
    SERIES_D,
    esc,
    header,
    line_chart,
    mark,
    rule_line,
    table,
    txt,
)

OUT = pathlib.Path("docs/design/mockups")


def frame(name, T, label, panes, W=1740, H=1000):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        f'<rect width="{W}" height="{H}" fill="{T["canvas"]}"/><g font-family="{FONT}">'
        f'<text x="40" y="34" font-size="11" font-weight="700" fill="{T["signal"]}" letter-spacing=".12em">'
        f"HIGH-FIDELITY MOCKUP · {esc(label)} · {T['name']}</text>" + "".join(panes) + "</g></svg>"
    )
    (OUT / f"{name}.svg").write_text(svg)


def pane(x, y, w, h, T, inner):
    return (
        f'<g transform="translate({x},{y})"><rect width="{w}" height="{h}" rx="6" '
        f'fill="{T["canvas"]}" stroke="{T["rule"]}"/>{inner}</g>'
    )


def inspect_action(x, y, w, T, label="Inspect both traces"):
    """One control for both traces. Two persistent buttons implied two
    destinations and made the reader choose a side before reading anything."""
    return "\n".join(
        [
            f'<rect x="{x}" y="{y}" width="{w}" height="42" rx="4" fill="none" stroke="{T["signal"]}"/>',
            txt(x + 24, y + 27, label, 13.5, 650, T["signal"], t=T),
            txt(x + w - 24, y + 27, "→", 13.5, 400, T["signal"], anchor="end", t=T),
            txt(
                x + w + 20,
                y + 27,
                "opens one drawer with a tab per strategy",
                12,
                400,
                T["muted"],
                t=T,
            ),
        ]
    )


ROUTE_COLS = ["strategy", "route", "calls", "cost", "runtime", "contract", "status"]
ROUTE_W = [200, 200, -90, -140, -110, -130, -122]


# ------------------------------------------------------------- agreement
def compare_agree(T, series, W=1180):
    o = [
        header(0, 0, W, T, "orders_2024.csv · 200 rows · 3 fields"),
        txt(72, 100, "What was total revenue in 2024 using order_date?", 24, 700, T["ink"], t=T),
        txt(
            72,
            126,
            "Compared across two planning strategies · 200 rows · 140 matched 2024",
            12.5,
            400,
            T["muted"],
            t=T,
        ),
        f'<rect x="72" y="150" width="{W - 144}" height="64" rx="5" fill="{T["signal_weak"]}"/>',
    ]
    for i, (k, v) in enumerate(
        [
            ("contracts", "identical"),
            ("coverage", "identical"),
            ("output", "identical"),
            ("recorded route", "deterministic was sufficient"),
        ]
    ):
        xx = 96 + i * 260
        o += [
            txt(xx, 176, k, 11.5, 400, T["muted"], t=T),
            txt(xx, 196, v, 14, 650, T["signal"], t=T),
        ]
    o += [
        txt(72, 254, "How each strategy got there", 16, 650, T["ink"], t=T),
        table(
            72,
            272,
            W - 144,
            T,
            [
                ["Deterministic", "rules only", "0", "$0.000000", "1.9 s", "a3f9c1", "completed"],
                [
                    "AI (typed plan)",
                    "governed planner",
                    "1",
                    "$0.000200",
                    "4.3 s",
                    "a3f9c1",
                    "completed",
                ],
            ],
            ROUTE_COLS,
            ROUTE_W,
        ),
    ]
    o += [
        rule_line(72, 400, W - 72, T),
        txt(72, 436, "Both strategies produced the same answer", 19, 650, T["ink"], t=T),
        txt(72, 510, "29,225.00", 38, 700, T["ink"], t=T),
        txt(
            72,
            536,
            "total revenue · 2024 by order_date · 140 of 200 rows matched",
            12.5,
            400,
            T["muted"],
            t=T,
        ),
        line_chart(
            72,
            560,
            W - 144,
            240,
            T,
            series,
            pts=[6480, 7120, 7540, 8085],
            labels=["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"],
            lo=6000,
            hi=8500,
            title="Revenue by quarter, 2024",
            unit="revenue",
            annot="sums to 29,225.00",
        ),
    ]
    # Reclaimed space: what "agree" was actually measured over. Agreement
    # that is asserted but not specified is a slogan.
    o += [
        rule_line(72, 824, W - 72, T),
        txt(72, 858, "Why this counts as agreement", 16, 650, T["ink"], t=T),
    ]
    for i, (k, v) in enumerate(
        [
            (
                "accepted contract",
                "byte-identical typed plan: same measure, same period field, same filters, same grouping",
            ),
            (
                "canonical output",
                "same canonical hash a3f9c1 over the result rows — explanation and wording are excluded from it",
            ),
            (
                "coverage",
                "both matched 140 of 200 rows and 140 observations; neither discarded a row the other kept",
            ),
        ]
    ):
        yy = 892 + i * 44
        o += [
            txt(72, yy, k, 12, 650, T["muted"], font=MONO, t=T),
            txt(290, yy, v, 13.5, 400, T["ink2"], t=T),
        ]
    o += [
        txt(
            72,
            1024,
            "Wording differs between the two narrations and is not compared: it is not part of the contract.",
            12.5,
            400,
            T["muted"],
            t=T,
        ),
        inspect_action(72, 1056, 300, T),
    ]
    return "\n".join(o), 1130


def mobile_agree(T, W=390):
    o = [
        f'<rect x="0" y="0" width="{W}" height="44" fill="{T["paper"]}"/>',
        rule_line(0, 44, W, T),
        mark(16, 14, T["signal"], 0.9),
        txt(40, 27, "Agentic Analytics", 12.5, 650, T["ink"], t=T),
        txt(20, 82, "What was total revenue in", 15, 700, T["ink"], t=T),
        txt(20, 102, "2024 using order_date?", 15, 700, T["ink"], t=T),
        f'<rect x="20" y="120" width="{W - 40}" height="92" rx="5" fill="{T["signal_weak"]}"/>',
    ]
    for i, (k, v) in enumerate(
        [("contracts", "identical"), ("coverage", "identical"), ("output", "identical")]
    ):
        o += [
            txt(36, 146 + i * 24, k, 11.5, 400, T["muted"], t=T),
            txt(W - 36, 146 + i * 24, v, 12, 650, T["signal"], anchor="end", t=T),
        ]
    o += [
        txt(20, 248, "Both strategies agreed", 15, 650, T["ink"], t=T),
        txt(20, 292, "29,225.00", 28, 700, T["ink"], t=T),
        txt(20, 314, "total revenue · 2024 by order_date", 11.5, 400, T["muted"], t=T),
        line_chart(
            20,
            334,
            W - 40,
            190,
            T,
            SERIES,
            pts=[6480, 7120, 7540, 8085],
            labels=["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"],
            lo=6000,
            hi=8500,
            title="Revenue by quarter, 2024",
            unit="revenue",
        ),
        rule_line(20, 544, W - 20, T),
        txt(20, 572, "Why this counts as agreement", 13, 650, T["ink"], t=T),
    ]
    for i, v in enumerate(
        ["Same accepted contract", "Same canonical hash a3f9c1", "Both matched 140 of 200 rows"]
    ):
        o.append(txt(20, 596 + i * 20, f"· {v}", 12, 400, T["ink2"], t=T))
    o += [
        f'<rect x="20" y="668" width="{W - 40}" height="40" rx="4" fill="{T["paper"]}" stroke="{T["signal"]}"/>',
        txt(36, 693, "Inspect both traces", 12.5, 650, T["signal"], t=T),
        txt(W - 36, 693, "▲", 12.5, 650, T["signal"], anchor="end", t=T),
        f'<rect x="20" y="716" width="{W - 40}" height="40" rx="4" fill="{T["paper"]}" stroke="{T["rule"]}"/>',
        txt(36, 741, "How each strategy got there", 12.5, 500, T["ink2"], t=T),
        txt(W - 36, 741, "▾", 12.5, 400, T["muted"], anchor="end", t=T),
    ]
    return "\n".join(o), 790


# ------------------------------------------------------------- divergence
DIFF_ROWS = [
    ["measure", "gross_margin_pct", "gross_margin_pct", "same"],
    ["period field", "order_date", "order_date", "same"],
    ["period", "Q3 2025", "Q3 2025", "same"],
    ["grouping", "category", "category, region", "differs"],
    ["rows matched", "128,440", "128,440", "same"],
    ["result rows", "6", "24", "differs"],
    ["canonical hash", "a3f9c1", "b71e04", "differs"],
    ["leading driver", "Electronics  −3.1 pts", "Electronics · West  −1.4 pts", "differs"],
]

# Counted, never typed. A hand-written "5 agree, 3 differ" under a table of
# eight rows is exactly the kind of caption that drifts away from the data
# it describes.
N_SAME = sum(1 for r in DIFF_ROWS if r[3] == "same")
N_DIFF = len(DIFF_ROWS) - N_SAME


def diff_table(x, y, w, T):
    widths = [210, 300, 330, 140]
    o = [rule_line(x, y, x + w, T)]
    cx = x
    for c, cw in zip(["field", "deterministic", "AI (typed plan)", ""], widths, strict=False):
        o.append(txt(cx, y + 20, c, 11.5, 650, T["muted"], t=T))
        cx += cw
    o.append(rule_line(x, y + 30, x + w, T))
    for r, row in enumerate(DIFF_ROWS):
        yy = y + 30 + (r + 1) * 30
        same = row[3] == "same"
        if not same:
            o.append(
                f'<rect x="{x}" y="{yy - 21}" width="{w}" height="30" fill="{T["warn_weak"]}" opacity=".55"/>'
            )
        cx = x
        for i, (v, cw) in enumerate(zip(row, widths, strict=False)):
            if i == 3:
                o.append(
                    txt(
                        cx,
                        yy,
                        v,
                        11.5,
                        650 if not same else 400,
                        T["warn"] if not same else T["muted"],
                        font=MONO,
                        t=T,
                    )
                )
            else:
                o.append(
                    txt(
                        cx,
                        yy,
                        v,
                        12.5,
                        400 if i else 500,
                        T["ink2"] if i else T["ink"],
                        font=MONO if i and i < 3 else FONT,
                        t=T,
                    )
                )
            cx += cw
    return "\n".join(o)


def compare_diff(T, W=1180):
    o = [
        header(0, 0, W, T, "commerce_demo · 7 tables · 502,819 rows"),
        txt(72, 100, "What drove the Q3 2025 margin change?", 24, 700, T["ink"], t=T),
        txt(
            72,
            126,
            "Compared across two planning strategies · 128,440 rows matched Q3 2025 by order_date",
            12.5,
            400,
            T["muted"],
            t=T,
        ),
        f'<rect x="72" y="150" width="{W - 144}" height="64" rx="5" fill="{T["warn_weak"]}"/>',
    ]
    for i, (k, v, warn) in enumerate(
        [
            ("contracts", "differ", True),
            ("coverage", "identical", False),
            ("output", "differs", True),
            ("recorded route", "both completed", False),
        ]
    ):
        xx = 96 + i * 260
        o += [
            txt(xx, 176, k, 11.5, 400, T["muted"], t=T),
            txt(xx, 196, v, 14, 650, T["warn"] if warn else T["ink2"], t=T),
        ]
    o += [
        txt(72, 254, "How each strategy got there", 16, 650, T["ink"], t=T),
        table(
            72,
            272,
            W - 144,
            T,
            [
                ["Deterministic", "rules only", "0", "$0.000000", "2.4 s", "a3f9c1", "completed"],
                [
                    "AI (typed plan)",
                    "governed planner",
                    "2",
                    "$0.000310",
                    "6.1 s",
                    "b71e04",
                    "completed",
                ],
            ],
            ROUTE_COLS,
            ROUTE_W,
        ),
    ]
    o += [
        rule_line(72, 400, W - 72, T),
        txt(72, 436, "The strategies did not agree", 19, 650, T["ink"], t=T),
        txt(
            72,
            462,
            "Neither result is presented as the answer. The question did not name a breakdown, so each strategy",
            13.5,
            400,
            T["ink2"],
            t=T,
        ),
        txt(
            72,
            482,
            "chose one, and they chose differently. The difference is shown field by field rather than summarised.",
            13.5,
            400,
            T["ink2"],
            t=T,
        ),
    ]
    # The reclaimed space carries the structured diff (the thing a reader
    # opened Compare for) rather than two results side by side, which
    # invites picking the preferred number.
    o += [
        diff_table(72, 516, W - 144, T),
        txt(
            72,
            844,
            f"{N_SAME} fields agree · {N_DIFF} differ. Differences are computed over the canonical contract, which excludes",
            12.5,
            400,
            T["muted"],
            t=T,
        ),
        txt(
            72,
            864,
            "explanation, interpretation and wording: those are provenance, not the contract.",
            12.5,
            400,
            T["muted"],
            t=T,
        ),
        inspect_action(72, 896, 300, T),
    ]
    return "\n".join(o), 970


def mobile_diff(T, W=390):
    o = [
        f'<rect x="0" y="0" width="{W}" height="44" fill="{T["paper"]}"/>',
        rule_line(0, 44, W, T),
        mark(16, 14, T["signal"], 0.9),
        txt(40, 27, "Agentic Analytics", 12.5, 650, T["ink"], t=T),
        txt(20, 82, "What drove the Q3 2025", 15, 700, T["ink"], t=T),
        txt(20, 102, "margin change?", 15, 700, T["ink"], t=T),
        f'<rect x="20" y="120" width="{W - 40}" height="68" rx="5" fill="{T["warn_weak"]}"/>',
        txt(36, 146, "contracts", 11.5, 400, T["muted"], t=T),
        txt(W - 36, 146, "differ", 12, 650, T["warn"], anchor="end", t=T),
        txt(36, 170, "output", 11.5, 400, T["muted"], t=T),
        txt(W - 36, 170, "differs", 12, 650, T["warn"], anchor="end", t=T),
        txt(20, 224, "The strategies did not agree", 15, 650, T["ink"], t=T),
        txt(20, 248, "Neither is presented as the answer.", 12.5, 400, T["ink2"], t=T),
        txt(20, 266, "The question named no breakdown.", 12.5, 400, T["ink2"], t=T),
        txt(20, 306, "What differs", 13.5, 650, T["ink"], t=T),
    ]
    y = 320
    for fieldname, det, ai, _ in [r for r in DIFF_ROWS if r[3] != "same"]:
        o += [
            f'<rect x="20" y="{y}" width="{W - 40}" height="72" rx="4" fill="{T["paper"]}" stroke="{T["rule"]}"/>',
            txt(36, y + 22, fieldname, 11.5, 650, T["warn"], font=MONO, t=T),
            txt(36, y + 44, "deterministic", 10.5, 400, T["muted"], t=T),
            txt(W - 36, y + 44, det, 11.5, 400, T["ink"], font=MONO, anchor="end", t=T),
            txt(36, y + 62, "AI (typed plan)", 10.5, 400, T["muted"], t=T),
            txt(W - 36, y + 62, ai, 11.5, 400, T["ink"], font=MONO, anchor="end", t=T),
        ]
        y += 82
    o += [
        txt(20, y + 16, f"{N_SAME} further fields agree", 12, 400, T["muted"], t=T),
        f'<rect x="20" y="{y + 34}" width="{W - 40}" height="40" rx="4" fill="{T["paper"]}" stroke="{T["signal"]}"/>',
        txt(36, y + 59, "Inspect both traces", 12.5, 650, T["signal"], t=T),
        txt(W - 36, y + 59, "▲", 12.5, 650, T["signal"], anchor="end", t=T),
    ]
    return "\n".join(o), y + 100


# ---------------------------------------------------------- evidence drawer
TRACE = {
    "Deterministic": [
        ("accepted contract", "a3f9c1"),
        ("canonical hash", "a3f9c1"),
        ("build sha", "5913f6e"),
        ("engine", "0.9.0"),
        ("route", "rules only · no model call"),
        ("coverage", "140 of 200 rows · 140 observations"),
        ("verification", "1 claim · 1 published · 0 withheld"),
        ("cited cells", "result[0].total_revenue"),
        ("planner fallback", "not reached"),
        ("timings", "parse 12 ms · plan 3 ms · execute 1.8 s"),
    ],
    "AI (typed plan)": [
        ("accepted contract", "a3f9c1"),
        ("canonical hash", "a3f9c1"),
        ("build sha", "5913f6e"),
        ("engine", "0.9.0"),
        ("route", "governed planner · 1 call · $0.000200"),
        ("model", "requested claude-sonnet-5 · resolved claude-sonnet-5"),
        ("coverage", "140 of 200 rows · 140 observations"),
        ("verification", "1 claim · 1 published · 0 withheld"),
        ("cited cells", "result[0].total_revenue"),
        ("planner fallback", "not reached"),
        ("timings", "parse 12 ms · plan 2.1 s · execute 1.9 s"),
    ],
}


def drawer_desktop(T, W=1180, H=760):
    """Edge sheet. The page stays visible and is not re-laid-out: the drawer
    is a disclosure over the argument, not a separate destination."""
    dw = 560
    o = [
        header(0, 0, W, T, "orders_2024.csv · 200 rows · 3 fields"),
        txt(72, 110, "What was total revenue in 2024 using order_date?", 20, 700, T["ink"], t=T),
        txt(72, 152, "29,225.00", 32, 700, T["ink"], t=T),
        txt(
            72,
            176,
            "total revenue · both strategies · 140 of 200 rows matched",
            12,
            400,
            T["muted"],
            t=T,
        ),
        txt(72, 230, "Why this counts as agreement", 14, 650, T["ink2"], t=T),
    ]
    for i, (k, v) in enumerate(
        [
            ("accepted contract", "byte-identical typed plan"),
            ("canonical output", "same canonical hash a3f9c1"),
            ("coverage", "140 of 200 rows, 140 observations"),
        ]
    ):
        o += [
            txt(72, 262 + i * 26, k, 11.5, 650, T["muted"], font=MONO, t=T),
            txt(250, 262 + i * 26, v, 13, 400, T["ink2"], t=T),
        ]
    o += [
        f'<rect x="0" y="52" width="{W}" height="{H - 52}" fill="{T["scrim"]}" opacity="{T["scrim_op"]}"/>',
        f'<rect x="{W - dw}" y="52" width="{dw}" height="{H - 52}" fill="{T["paper"]}"/>',
        f'<line x1="{W - dw}" y1="52" x2="{W - dw}" y2="{H}" stroke="{T["rule"]}"/>',
        txt(W - dw + 28, 92, "Evidence", 18, 700, T["ink"], t=T),
        txt(W - 28, 92, "Esc  ✕", 12.5, 400, T["muted"], anchor="end", t=T),
        txt(
            W - dw + 28,
            114,
            "Both traces are here. Switching tabs runs nothing.",
            12,
            400,
            T["muted"],
            t=T,
        ),
    ]
    # Two tabs, one drawer.
    tx = W - dw + 28
    for i, name in enumerate(TRACE):
        tw = 150 + i * 28
        active = i == 0
        o += [
            txt(
                tx, 158, name, 13.5, 650 if active else 400, T["ink"] if active else T["muted"], t=T
            )
        ]
        if active:
            o.append(f'<rect x="{tx}" y="170" width="{tw - 30}" height="2" fill="{T["signal"]}"/>')
        tx += tw
    o.append(rule_line(W - dw + 28, 172, W - 28, T))
    for i, (k, v) in enumerate(TRACE["Deterministic"]):
        yy = 206 + i * 38
        o += [
            txt(W - dw + 28, yy, k, 11, 400, T["muted"], t=T),
            txt(W - dw + 28, yy + 18, v, 12.5, 400, T["ink"], font=MONO, t=T),
        ]
    yy = 206 + len(TRACE["Deterministic"]) * 38
    o += [
        rule_line(W - dw + 28, yy, W - 28, T),
        txt(W - dw + 28, yy + 26, "Activity trace  ▾", 12.5, 400, T["signal"], t=T),
        txt(W - dw + 28, yy + 48, "Planning audit  ▾", 12.5, 400, T["signal"], t=T),
        txt(
            W - dw + 28,
            yy + 76,
            "In print, both tabs are expanded as an appendix.",
            11.5,
            400,
            T["muted"],
            t=T,
        ),
    ]
    return "\n".join(o), H


def drawer_mobile(T, W=390, H=700):
    o = [
        f'<rect x="0" y="0" width="{W}" height="44" fill="{T["paper"]}"/>',
        rule_line(0, 44, W, T),
        mark(16, 14, T["signal"], 0.9),
        txt(40, 27, "Agentic Analytics", 12.5, 650, T["ink"], t=T),
        txt(20, 82, "29,225.00", 24, 700, T["ink"], t=T),
        txt(20, 104, "total revenue · both strategies", 11.5, 400, T["muted"], t=T),
        f'<rect x="0" y="44" width="{W}" height="{H - 44}" fill="{T["scrim"]}" opacity="{T["scrim_op"]}"/>',
        f'<rect x="0" y="150" width="{W}" height="{H - 150}" rx="10" fill="{T["paper"]}"/>',
        f'<rect x="{W / 2 - 20}" y="162" width="40" height="3" rx="2" fill="{T["rule"]}"/>',
        txt(20, 196, "Evidence", 16, 700, T["ink"], t=T),
        txt(W - 20, 196, "✕", 13, 400, T["muted"], anchor="end", t=T),
    ]
    tx = 20
    for i, name in enumerate(TRACE):
        active = i == 0
        o.append(
            txt(
                tx, 230, name, 12.5, 650 if active else 400, T["ink"] if active else T["muted"], t=T
            )
        )
        if active:
            o.append(f'<rect x="{tx}" y="240" width="96" height="2" fill="{T["signal"]}"/>')
        tx += 124
    o.append(rule_line(20, 242, W - 20, T))
    for i, (k, v) in enumerate(TRACE["Deterministic"][:8]):
        yy = 272 + i * 40
        o += [
            txt(20, yy, k, 10.5, 400, T["muted"], t=T),
            txt(20, yy + 17, v, 11.5, 400, T["ink"], font=MONO, t=T),
        ]
    return "\n".join(o), H


# --------------------------------------------------------------- emit
for T, series in ((LIGHT, SERIES), (DARK, SERIES_D)):
    low = T["name"].lower()
    b, dh = compare_agree(T, series)
    mb, mh = mobile_agree(T)
    frame(
        f"compare-{low}",
        T,
        "COMPARE · AGREEMENT",
        [
            pane(40, 56, 1180, dh, T, b),
            pane(1270, 56, 390, mh, T, mb),
            txt(
                40,
                56 + dh + 26,
                "DESKTOP 1440 — one shared answer, then why it counts as agreement, then one action",
                11,
                700,
                T["muted"],
                t=T,
            ),
            txt(1270, 56 + mh + 26, "MOBILE 390", 11, 700, T["muted"], t=T),
        ],
        H=56 + max(dh, mh) + 70,
    )

    b, dh = compare_diff(T)
    mb, mh = mobile_diff(T)
    frame(
        f"compare-diff-{low}",
        T,
        "COMPARE · DIVERGENCE",
        [
            pane(40, 56, 1180, dh, T, b),
            pane(1270, 56, 390, mh, T, mb),
            txt(
                40,
                56 + dh + 26,
                "DESKTOP 1440 — the reclaimed space carries the structured difference",
                11,
                700,
                T["muted"],
                t=T,
            ),
            txt(
                1270,
                56 + mh + 26,
                "MOBILE 390 — differing fields first, agreeing fields counted",
                11,
                700,
                T["muted"],
                t=T,
            ),
        ],
        H=56 + max(dh, mh) + 70,
    )

    b, dh = drawer_desktop(T)
    mb, mh = drawer_mobile(T)
    frame(
        f"compare-evidence-{low}",
        T,
        "COMPARE · EVIDENCE DRAWER",
        [
            pane(40, 56, 1180, dh, T, b),
            pane(1270, 56, 390, mh, T, mb),
            txt(
                40,
                56 + dh + 26,
                "DESKTOP 1440 — edge sheet, two tabs, page still visible behind",
                11,
                700,
                T["muted"],
                t=T,
            ),
            txt(
                1270,
                56 + mh + 26,
                "MOBILE 390 — bottom sheet, same two tabs",
                11,
                700,
                T["muted"],
                t=T,
            ),
        ],
        H=56 + max(dh, mh) + 70,
    )
print("6 compare sheets written")
