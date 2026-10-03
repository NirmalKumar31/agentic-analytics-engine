"""One terminal state per sheet.

The composite `terminal-*` sheet shows two states side by side because that is
documentation. These are the implementation references: a run reaches exactly
one of these, and the rendered page shows exactly that one.

Every status string below is a real `RunOutcome` from
`src/agentic_analytics/graph/runner.py` or a real `RunRecord.status`. None is
invented for the mockup.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mock import *

OUT = pathlib.Path('docs/design/mockups')

def frame(name, T, label, panes, W=1740, H=1000):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
           f'<rect width="{W}" height="{H}" fill="{T["canvas"]}"/><g font-family="{FONT}">'
           f'<text x="40" y="34" font-size="11" font-weight="700" fill="{T["signal"]}" letter-spacing=".12em">'
           f'TERMINAL STATE · {esc(label)} · {T["name"]}</text>' + "".join(panes) + '</g></svg>')
    (OUT / f'{name}.svg').write_text(svg)

def pane(x, y, w, h, T, inner):
    return (f'<g transform="translate({x},{y})"><rect width="{w}" height="{h}" rx="6" '
            f'fill="{T["canvas"]}" stroke="{T["rule"]}"/>{inner}</g>')


# ---------------------------------------------------------------- the states
#
# `accent` is a token name, never a literal. Severity is carried by the rule
# bar and by word order, not by a coloured box around the whole state: a run
# that honestly found nothing is not an error and must not look like one.
STATES = [
    dict(
        key="refused", label="REFUSED",
        status="refused", accent="signal",
        dataset="sales.csv · 240 rows · 2 date columns",
        question="What was total annual revenue in 2024?",
        head=["Which date defines 2024?"], head_m=["Which date", "defines 2024?"],
        body=["This table has two date columns and they answer different questions. order_date is when",
              "revenue was earned; signup_date is when the account began. Choosing for you would produce",
              "a number that looks authoritative and answers a question you did not ask."],
        body_m=["Two date columns answer", "different questions. Choosing", "for you would answer a", "question you did not ask."],
        actions=[("Ask using order_date", True), ("Ask using signup_date", False)],
        action_m=("Ask using order_date", True),
        disclosure="Why this is refused",
        truth="outcome refused · nothing published · no model call · 0.3 s",
        caption="DESKTOP 1440 — the refusal is a question, and the answer is one click away",
    ),
    dict(
        key="no-findings", label="NO FINDINGS",
        status="completed", accent="ink2",
        dataset="sales.csv · 240 rows",
        question="What is the total refund_value where region is West?",
        head=["No findings to publish"], head_m=["No findings", "to publish"],
        body=["The analysis ran and completed. 60 rows matched region = West, and none of them held a",
              "refund_value, so there was nothing to total.",
              "This is a result about your data, not a problem with the engine."],
        body_m=["60 rows matched. None held", "a refund_value, so there was", "nothing to total.", "Not a failure."],
        actions=[], action_m=None,
        disclosure="Show work",
        truth="outcome completed · 60 rows matched · 0 observations · 0 published · 0 withheld",
        caption="DESKTOP 1440 — completed, and honest that completed is not the same as published",
    ),
    dict(
        key="verification-withheld", label="VERIFICATION WITHHELD",
        status="completed", accent="ink2",
        dataset="orders_2024.csv · 200 rows · 3 fields",
        question="Did the discount campaign cause the Q3 revenue increase?",
        head=["The result stands; the explanation does not"],
        head_m=["The result stands;", "the explanation", "does not"],
        body=["Revenue was computed and is shown below. Two candidate explanations were produced and both",
              "were withheld at verification, so neither is presented as a finding. A claim that cannot be",
              "checked is withheld, never shown with a caveat attached."],
        # Not "both failed verification": this run completed. Borrowing the
        # vocabulary of the `failed` status for a completed run is the exact
        # confusion these six separate sheets exist to prevent.
        body_m=["The number below was computed.", "Two explanations were", "produced, and both were", "withheld at verification."],
        actions=[("See why each was withheld", False)], action_m=("See why each was withheld", False),
        disclosure=None,
        truth="outcome completed · 1 result · 0 published · 2 withheld · causal_from_observational, no_evidence",
        caption="DESKTOP 1440 — computed result kept, unverified claims withheld",
        withheld=[("causal_from_observational",
                   "“The discount campaign drove the increase” asserts cause from an observational table with no design that could establish it."),
                  ("no_evidence",
                   "“Margin pressure was concentrated in Apparel” cited no cell in the result it was checked against.")],
        result=("29,225.00", "total revenue · 2024 by order_date · 140 of 200 rows matched"),
    ),
    dict(
        key="quota-stopped", label="QUOTA STOPPED",
        status="budget_exhausted", accent="warn",
        dataset="commerce_demo · 7 tables · 502,819 rows",
        question="Explain every driver of the Q3 margin change by category and region.",
        head=["This AI run reached its input token limit"],
        head_m=["This run reached", "its token limit"],
        body=["The run was stopped by its own budget before it finished, so nothing was published. Partial",
              "work is not presented as an answer. The spend below is what had already been incurred when",
              "the limit was reached — it is recorded whether or not anything was published."],
        body_m=["Stopped by its own budget", "before finishing. Nothing", "was published; partial work", "is not an answer."],
        actions=[("Run deterministically instead", True), ("Narrow the question", False)],
        action_m=("Run deterministically instead", True),
        disclosure="Show what was spent",
        truth="outcome budget_exhausted · 0 published · 3 provider calls · $0.000412 · limit 24,000 input tokens",
        caption="DESKTOP 1440 — the budget is a product rule, stated plainly, with a free route offered",
    ),
    dict(
        key="failed", label="FAILED",
        status="failed", accent="warn",
        dataset="orders_2024.csv · 200 rows · 3 fields",
        question="What was total revenue in 2024 using order_date?",
        head=["The analysis did not complete"], head_m=["The analysis did", "not complete"],
        body=["Something in the engine broke. This is not a statement about your data and not a refusal —",
              "the run stopped before it could reach any conclusion, and nothing partial has been kept.",
              "The reference below identifies this run in the server log."],
        body_m=["Something in the engine", "broke. Not your data, and", "not a refusal. Nothing", "partial was kept."],
        actions=[("Try again", True)], action_m=("Try again", True),
        disclosure="Show the trace",
        truth="outcome failed · stage execute · run 7f2a9c41 · sha 5913f6e · nothing published",
        caption="DESKTOP 1440 — a real failure says so, and never borrows the language of a refusal",
    ),
    dict(
        key="cancelled", label="CANCELLED",
        status="cancelled", accent="muted",
        dataset="orders_2024.csv · session ended",
        question="What was total revenue in 2024 using order_date?",
        head=["This run stopped because its dataset was closed"],
        head_m=["This run stopped", "because its dataset", "was closed"],
        body=["The file was deleted, replaced, or its 15-minute session expired while the analysis was",
              "running. Nothing went wrong with the analysis — the thing it was analysing was withdrawn,",
              "so the run was cancelled rather than finished."],
        body_m=["The file was deleted,", "replaced, or expired while", "this ran. Nothing went", "wrong with the analysis."],
        actions=[("Upload the file again", True)], action_m=("Upload the file again", True),
        disclosure=None,
        truth="outcome cancelled · dataset withdrawn · nothing published · no model call",
        caption="DESKTOP 1440 — withdrawn input, not engine failure; the wording keeps them apart",
    ),
]


def desktop(T, s, W=1180):
    # The rule bar carries severity; the primary button never does. A
    # recovery action rendered in the warning colour reads as the dangerous
    # thing on the page, when it is the way out of it.
    accent, action = T[s["accent"]], T["signal"]
    o = [header(0, 0, W, T, s["dataset"]),
         txt(72, 112, s["question"], 13.5, 400, T["muted"], t=T)]
    nlines = len(s["body"])
    # The headline needs air under it: at display scale, 20px of leading
    # reads as the body being a subtitle rather than a separate register.
    bar_h = 72 + 36 * len(s["head"]) + 21 * nlines
    o += [f'<rect x="72" y="132" width="6" height="{bar_h}" fill="{accent}"/>']
    y = 132
    for i, h in enumerate(s["head"]):
        o.append(txt(100, y + 44 + i * 36, h, 30, 700, T["ink"], t=T))
    y += 36 * len(s["head"]) + 46
    for i, b in enumerate(s["body"]):
        o.append(txt(100, y + i * 21, b, 14, 400, T["ink2"], t=T))
    y += nlines * 21 + 16

    # Actions. A state with nothing to decide gets no button: an action that
    # only restates the state is noise, and it teaches people to ignore
    # buttons that matter.
    if s["actions"]:
        x = 100
        for text, primary in s["actions"]:
            w = 42 + len(text) * 7.4
            if primary:
                o += [f'<rect x="{x}" y="{y}" width="{w}" height="34" rx="4" fill="{action}"/>',
                      txt(x + w/2, y+22, text, 13, 650,
                          T["paper"] if T["name"] == "LIGHT" else T["canvas"], anchor="middle", t=T)]
            else:
                o += [f'<rect x="{x}" y="{y}" width="{w}" height="34" rx="4" fill="none" stroke="{T["rule"]}"/>',
                      txt(x + w/2, y+22, text, 13, 500, T["ink2"], anchor="middle", t=T)]
            x += w + 12
        y += 54

    if s.get("result"):
        value, context = s["result"]
        o += [rule_line(72, y, W-72, T),
              txt(100, y+52, value, 34, 700, T["ink"], t=T),
              txt(100, y+76, context, 12.5, 400, T["muted"], t=T),
              f'<rect x="{100 + len(value)*20 + 28}" y="{y+28}" width="128" height="22" rx="3" fill="{T["inset"]}"/>',
              txt(100 + len(value)*20 + 42, y+43, "not interpreted", 11, 650, T["muted"], t=T)]
        y += 104

    if s.get("withheld"):
        o += [rule_line(72, y, W-72, T), txt(100, y+34, "What was withheld, and under which rule", 15, 650, T["ink"], t=T)]
        y += 52
        for rule, why in s["withheld"]:
            o += [txt(100, y+16, rule, 12, 650, T["warn"], font=MONO, t=T),
                  txt(100, y+38, why, 13.5, 400, T["ink2"], t=T)]
            y += 58
        y += 4

    if s["disclosure"]:
        o.append(txt(100, y+16, f'{s["disclosure"]} ▾', 12.5, 400, T["signal"], t=T))
        y += 32
    o += [rule_line(72, y+16, W-72, T), txt(72, y+46, s["truth"], 11.5, 400, T["muted"], font=MONO, t=T)]
    return "\n".join(o), y + 76


def mobile(T, s, W=390):
    accent, action = T[s["accent"]], T["signal"]
    o = [f'<rect x="0" y="0" width="{W}" height="44" fill="{T["paper"]}"/>', rule_line(0, 44, W, T),
         mark(16, 14, T["signal"], .9), txt(40, 27, "Agentic Analytics", 12.5, 650, T["ink"], t=T),
         txt(W-16, 27, "◑", 12.5, 400, T["muted"], anchor="end", t=T),
         txt(20, 78, s["question"][:42] + ("…" if len(s["question"]) > 42 else ""), 11.5, 400, T["muted"], t=T)]
    hl, bl = len(s["head_m"]), len(s["body_m"])
    bar_h = 52 + 27 * hl + 18 * bl
    o.append(f'<rect x="20" y="96" width="5" height="{bar_h}" fill="{accent}"/>')
    y = 96
    for i, h in enumerate(s["head_m"]):
        o.append(txt(40, y + 34 + i * 27, h, 21, 700, T["ink"], t=T))
    y += 27 * hl + 36
    for i, b in enumerate(s["body_m"]):
        o.append(txt(40, y + i * 18, b, 12.5, 400, T["ink2"], t=T))
    y += bl * 18 + 14
    if s["action_m"]:
        text, primary = s["action_m"]
        w = W - 60
        fill = action if primary else "none"
        o += [f'<rect x="40" y="{y}" width="{w}" height="32" rx="4" fill="{fill}"'
              + (f' stroke="{T["rule"]}"' if not primary else '') + '/>',
              txt(40 + w/2, y+21, text, 12, 650,
                  (T["paper"] if T["name"] == "LIGHT" else T["canvas"]) if primary else T["ink2"],
                  anchor="middle", t=T)]
        y += 48
    if s.get("result"):
        value, _ = s["result"]
        o += [rule_line(20, y, W-20, T), txt(40, y+40, value, 26, 700, T["ink"], t=T),
              txt(40, y+60, "total revenue · not interpreted", 11, 400, T["muted"], t=T)]
        y += 82
    if s["disclosure"]:
        o.append(txt(40, y+16, f'{s["disclosure"]} ▾', 12, 400, T["signal"], t=T))
        y += 30
    o += [rule_line(20, y+12, W-20, T),
          txt(20, y+36, s["status"], 11, 650, T["muted"], font=MONO, t=T)]
    return "\n".join(o), y + 60


for s in STATES:
    for T in (LIGHT, DARK):
        body, dh = desktop(T, s)
        mbody, mh = mobile(T, s)
        H = max(dh, mh) + 150
        frame(f'state-{s["key"]}-{T["name"].lower()}', T, s["label"], [
            pane(40, 56, 1180, dh, T, body),
            pane(1270, 56, 390, mh, T, mbody),
            txt(40, 56+dh+26, s["caption"], 11, 700, T["muted"], t=T),
            txt(1270, 56+mh+26, f'MOBILE 390 — status {s["status"]}', 11, 700, T["muted"], t=T)],
            H=56 + max(dh, mh) + 70)
print(f"{len(STATES)*2} individual terminal-state sheets written")
