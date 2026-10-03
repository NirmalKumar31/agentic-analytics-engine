import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mock import *

OUT = pathlib.Path('docs/design/mockups')
# Share is computed, not typed. The hand-written column summed to 118.9%,
# which is the kind of detail a reviewer checks first and the kind a reader
# never recovers trust from.
_CATEGORIES = [("Apparel", 1_522_302, "41.1%"), ("Electronics", 1_318_044, "29.8%"),
               ("Home & Kitchen", 1_104_871, "35.2%"), ("Sports & Outdoors", 861_330, "38.7%"),
               ("Toys & Games", 825_693, "33.5%"), ("Beauty", 742_118, "44.0%")]
_TOTAL = sum(r for _, r, _ in _CATEGORIES)
# Ranked, because the context line says "ranked".
_CATEGORIES.sort(key=lambda c: -c[1])
ROWS = [[name, f"{rev:,}", f"{rev / _TOTAL * 100:.1f}%", margin] for name, rev, margin in _CATEGORIES]

# The margin decomposition has to add up to the headline it decomposes.
_MARGIN_TOTAL, _MARGIN_RATE = -7.63, -3.96
_MARGIN_MIX = round(_MARGIN_TOTAL - _MARGIN_RATE, 2)
COLS = ["category", "revenue", "share", "gross margin"]; WIDTHS = [300, -190, -150, -180]

def frame(name, T, label, panes, W=1740, H=1400):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
           f'<rect width="{W}" height="{H}" fill="{T["canvas"]}"/><g font-family="{FONT}">'
           f'<text x="40" y="34" font-size="11" font-weight="700" fill="{T["signal"]}" letter-spacing=".12em">'
           f'HIGH-FIDELITY MOCKUP · {esc(label)} · {T["name"]}</text>' + "".join(panes) + '</g></svg>')
    (OUT / f'{name}.svg').write_text(svg)

def desktop_report(T, series, W=1180):
    y = 52
    o = [header(0, 0, W, T, "commerce_demo · 7 tables · 502,819 rows"),
         txt(72, y+48, "Revenue increased in Q3 2025, but gross margin fell. What caused it?", 13.5, 400, T["muted"], t=T),
         txt(72, y+96, "Revenue rose 21%; margin fell 7.6 points", 34, 700, T["ink"], t=T),
         txt(72, y+126, "502,819 rows · 498,112 observations with a value · Q3 2025 by order_date · 6 verified, 0 withheld · 4 shown, ranked", 12.5, 400, T["muted"], t=T),
         rule_line(72, y+148, W-72, T), line_chart(72, y+170, W-144, 300, T, series, unit="revenue · thousands"),
         rule_line(72, y+500, W-72, T), txt(72, y+534, "What the numbers show", 19, 650, T["ink"], t=T)]
    for i, (n, t) in enumerate([("1","Electronics accounts for 80.3% of the revenue increase — the largest single contribution across category."),
        ("2",f"Of the {_MARGIN_TOTAL} point margin change, {_MARGIN_RATE} comes from rate movement within categories and {_MARGIN_MIX} from mix shifting between them."),
        ("3","Beauty is the exception: revenue fell 4.1% while its margin rose 2.2 points."),
        ("4","No breakdown was named in the question, so drivers were chosen by the planner. Ambiguity is recorded in the evidence.")]):
        yy = y + 568 + i*46
        o += [f'<circle cx="80" cy="{yy-4}" r="10" fill="{T["signal_weak"]}"/>',
              txt(80, yy, n, 11.5, 700, T["signal"], anchor="middle", t=T),
              txt(102, yy, t, 14.5, 500 if i==0 else 400, T["ink"] if i==0 else T["ink2"], t=T)]
    o += [rule_line(72, y+760, W-72, T), txt(72, y+794, "Revenue and margin by category", 15, 650, T["ink"], t=T),
          txt(W-72, y+794, "6 rows · CSV", 12, 400, T["signal"], anchor="end", t=T),
          table(72, y+810, W-144, T, ROWS, COLS, WIDTHS)]
    yy = y + 1030
    o += [f'<rect x="72" y="{yy}" width="196" height="42" rx="4" fill="none" stroke="{T["signal"]}"/>',
          txt(96, yy+26, "Show work →", 13.5, 650, T["signal"], t=T),
          txt(292, yy+26, "contract a3f9c1 · sha 5913f6e · 412 ms", 12, 400, T["muted"], font=MONO, t=T)]
    return "\n".join(o), yy+80

def mobile_report(T, series, W=390):
    o = [f'<rect x="0" y="0" width="{W}" height="44" fill="{T["paper"]}"/>', rule_line(0,44,W,T),
         mark(16,14,T["signal"],.9), txt(40,27,"Agentic Analytics",12.5,650,T["ink"],t=T),
         txt(W-16,27,"◑  ⋯",12.5,400,T["muted"],anchor="end",t=T),
         txt(20,76,"Revenue increased in Q3 2025,",11.5,400,T["muted"],t=T),
         txt(20,92,"but gross margin fell. What caused it?",11.5,400,T["muted"],t=T),
         txt(20,128,"Revenue rose 21%;",22,700,T["ink"],t=T), txt(20,154,"margin fell 7.6 pts",22,700,T["ink"],t=T),
         txt(20,176,"502,819 rows · 498,112 with a value",11,400,T["muted"],t=T),
         txt(20,191,"Q3 2025 by order_date",11,400,T["muted"],t=T), rule_line(20,208,W-20,T),
         line_chart(20,222,W-40,210,T,series,unit="revenue · thousands"), rule_line(20,448,W-20,T),
         txt(20,480,"What the numbers show",15,650,T["ink"],t=T),
         f'<circle cx="28" cy="508" r="9" fill="{T["signal_weak"]}"/>', txt(28,512,"1",11,700,T["signal"],anchor="middle",t=T),
         txt(48,506,"Electronics accounts for 80.3% of",13,500,T["ink"],t=T), txt(48,524,"the revenue increase.",13,500,T["ink"],t=T)]
    for i,label in enumerate([f"2   Margin: rate {_MARGIN_RATE}, mix {_MARGIN_MIX}","3   Beauty is the exception","4   Planner chose the breakdown"]):
        yy = 556+i*30
        o += [f'<rect x="20" y="{yy-16}" width="{W-40}" height="26" rx="3" fill="{T["paper"]}" stroke="{T["rule"]}"/>',
              txt(32,yy+2,label,12,400,T["ink2"],t=T), txt(W-34,yy+2,"▾",12,400,T["muted"],t=T)]
    o += [rule_line(20,660,W-20,T), txt(20,690,"Revenue by category",14,650,T["ink"],t=T),
          txt(W-20,690,"6 rows ›",11.5,400,T["signal"],anchor="end",t=T),
          f'<rect x="20" y="704" width="{W-40}" height="150" rx="4" fill="{T["paper"]}" stroke="{T["rule"]}"/>',
          table(32,714,W-64,T,ROWS[:4],["category","revenue"],[180,-150]),
          txt(W-30,868,"scrolls inside its frame →",10.5,400,T["muted"],anchor="end",t=T),
          f'<rect x="20" y="900" width="{W-40}" height="46" rx="4" fill="{T["paper"]}" stroke="{T["signal"]}"/>',
          txt(36,929,"Show work",13.5,650,T["signal"],t=T), txt(W-36,929,"▲",13.5,650,T["signal"],anchor="end",t=T)]
    return "\n".join(o)

def pane(x, y, w, h, T, inner):
    return (f'<g transform="translate({x},{y})"><rect width="{w}" height="{h}" rx="6" '
            f'fill="{T["canvas"]}" stroke="{T["rule"]}"/>{inner}</g>')

for T, series in ((LIGHT, SERIES), (DARK, SERIES_D)):
    body, dh = desktop_report(T, series)
    frame(f'report-{T["name"].lower()}', T, "REPORT", [
        pane(40, 56, 1180, dh, T, body), pane(1270, 56, 390, 980, T, mobile_report(T, series)),
        txt(40, 56+dh+26, "DESKTOP 1440 — content column 1180", 11, 700, T["muted"], t=T),
        txt(1270, 56+980+26, "MOBILE 390", 11, 700, T["muted"], t=T)], H=56+dh+70)
# The numeric claims the report sheets make, written out so the audit can
# check the arithmetic instead of a reader checking it by eye.
import json
pathlib.Path('docs/design/facts.json').write_text(json.dumps({
    "report_table": {"columns": COLS, "rows": ROWS, "revenue_total": _TOTAL},
    "margin": {"total": _MARGIN_TOTAL, "rate": _MARGIN_RATE, "mix": _MARGIN_MIX},
}, indent=2) + "\n")
print("report sheets written")

# ------------------------------------------------------------------ landing
def field(x, y, w, h, T, op=".5", focus=None):
    """The analytical field: layered contour density, sparse coordinate ticks,
    and one localised signal around the primary action.

    Three layers at different densities so a large canvas reads as composed
    rather than empty. No glow, no particles, no gradient behind text."""
    o = []
    # layer 1 — broad, faintest
    o.append(f'<g opacity="{float(op)*0.45}" stroke="{T["signal"]}" fill="none" stroke-width=".6">')
    for i in range(5):
        yy = y + 30 + i * (h - 60) / 4
        o.append(f'<path d="M{x} {yy} C {x+w*0.3} {yy-26}, {x+w*0.6} {yy+22}, {x+w} {yy-12}"/>')
    o.append('</g>')
    # layer 2 — denser mid band
    o.append(f'<g opacity="{float(op)*0.8}" stroke="{T["signal"]}" fill="none" stroke-width=".7">')
    for i in range(11):
        yy = y + h*0.22 + i * (h * 0.56) / 10
        amp = 7 + (i % 4) * 4
        o.append(f'<path d="M{x} {yy} C {x+w*0.25} {yy-amp}, {x+w*0.55} {yy+amp}, {x+w} {yy-amp*0.5}"/>')
    o.append('</g>')
    # sparse coordinate ticks with labels — the motif reads as measurement
    o.append(f'<g opacity="{float(op)*0.9}" font-family="{MONO}">')
    for cx, cy, lab in [(0.14,0.26,"0.18"),(0.39,0.58,"0.42"),(0.68,0.22,"0.71"),
                        (0.83,0.66,"0.86"),(0.54,0.40,"0.57")]:
        gx, gy = x + w*cx, y + h*cy
        o.append(f'<path d="M{gx-4} {gy} L{gx+4} {gy} M{gx} {gy-4} L{gx} {gy+4}" stroke="{T["signal"]}" stroke-width=".9"/>')
        o.append(f'<text x="{gx+8}" y="{gy+3}" font-size="8.5" fill="{T["signal"]}" opacity=".55">{lab}</text>')
    o.append('</g>')
    # Localised signal near the primary action: a slight rise in contour
    # density in otherwise empty canvas. Three lines, not seven — the first
    # attempt read as a scribble, which is noise wearing the costume of
    # restraint. It is placed in clear space and never crosses type.
    if focus:
        fx, fy, fw, fh = focus
        o.append(f'<g opacity="{float(op)*0.55}" stroke="{T["signal"]}" fill="none" stroke-width=".7">')
        for i in range(3):
            yy = fy + fh + 34 + i * 11
            o.append(f'<path d="M{fx} {yy} C {fx+fw*0.32} {yy-9}, {fx+fw*0.68} {yy+9}, {fx+fw} {yy-5}"/>')
        o.append('</g>')
    return "\n".join(o)


def desktop_landing(T, W=1180, H=620):
    o = [field(0, 0, W, H, T, focus=(72, 272, 620, 188)), header(0, 0, W, T),
         txt(72, 196, "What would you like to understand?", 38, 700, T["ink"], t=T),
         txt(72, 230, "Bring a CSV or Parquet file, or start from a dataset we have prepared.", 16, 400, T["ink2"], t=T),
         f'<rect x="72" y="272" width="620" height="188" rx="6" fill="{T["paper"]}" stroke="{T["signal"]}" stroke-dasharray="7 6"/>',
         txt(104, 324, "Drop a file here, or choose one", 19, 650, T["ink"], t=T),
         txt(104, 352, "CSV or Parquet · up to 10 MB · 200 columns · 400,000 rows", 13, 400, T["muted"], t=T),
         txt(104, 376, "Your file stays for this session only and is deleted after 15 minutes.", 13, 400, T["muted"], t=T),
         txt(104, 400, "Nothing is sent to a model unless you choose an AI strategy.", 13, 400, T["muted"], t=T),
         f'<rect x="104" y="418" width="150" height="30" rx="4" fill="{T["signal"]}"/>',
         txt(179, 438, "Choose a file", 13, 650, T["paper"] if T["name"]=="LIGHT" else T["canvas"], anchor="middle", t=T),
         txt(732, 292, "Or start from prepared data", 15, 650, T["ink"], t=T)]
    for i, (t1, t2) in enumerate([("Revenue up, margin down in Q3","parallel time-series · discount and mix"),
                                  ("Return rate by customer segment","segmentation · chi-square independence"),
                                  ("Shipping delay and repeat purchase","joined cohort · two-proportion z-test")]):
        yy = 312 + i*50
        o += [rule_line(732, yy, W-72, T), txt(732, yy+22, t1, 14, 500, T["ink"], t=T),
              txt(732, yy+39, t2, 11.5, 400, T["muted"], font=MONO, t=T),
              txt(W-72, yy+30, "→", 15, 400, T["signal"], anchor="end", t=T)]
    return "\n".join(o)

def mobile_landing(T, W=390, H=700):
    return "\n".join([field(0, 0, W, H, T, ".22"),
        f'<rect x="0" y="0" width="{W}" height="44" fill="{T["paper"]}"/>', rule_line(0,44,W,T),
        mark(16,14,T["signal"],.9), txt(40,27,"Agentic Analytics",12.5,650,T["ink"],t=T),
        txt(W-16,27,"◑",12.5,400,T["muted"],anchor="end",t=T),
        txt(20,120,"What would you",24,700,T["ink"],t=T), txt(20,150,"like to understand?",24,700,T["ink"],t=T),
        txt(20,180,"Bring a CSV or Parquet file.",13,400,T["ink2"],t=T),
        f'<rect x="20" y="208" width="{W-40}" height="150" rx="6" fill="{T["paper"]}" stroke="{T["signal"]}" stroke-dasharray="7 6"/>',
        txt(40,250,"Drop a file, or tap",16,650,T["ink"],t=T),
        txt(40,276,"CSV / Parquet · up to 10 MB",12,400,T["muted"],t=T),
        txt(40,296,"Session only, deleted after 15 min",12,400,T["muted"],t=T),
        f'<rect x="40" y="314" width="130" height="28" rx="4" fill="{T["signal"]}"/>',
        txt(105,333,"Choose a file",12,650,T["paper"] if T["name"]=="LIGHT" else T["canvas"],anchor="middle",t=T),
        txt(20,400,"Or start from prepared data",13,650,T["ink"],t=T)] +
        ["\n".join([rule_line(20, 420+i*44, W-20, T),
                    txt(20, 442+i*44, t1, 12.5, 500, T["ink"], t=T),
                    txt(W-20, 442+i*44, "→", 13, 400, T["signal"], anchor="end", t=T)])
         for i, t1 in enumerate(["Revenue up, margin down in Q3","Return rate by segment","Shipping delay and repeat purchase"])])

for T in (LIGHT, DARK):
    frame(f'landing-{T["name"].lower()}', T, "LANDING", [
        pane(40, 56, 1180, 620, T, desktop_landing(T)),
        pane(1270, 56, 390, 700, T, mobile_landing(T)),
        txt(40, 56+620+26, "DESKTOP 1440 — ambient field, full density", 11, 700, T["muted"], t=T),
        txt(1270, 56+700+26, "MOBILE 390 — same field at .22, no focus signal", 11, 700, T["muted"], t=T)], H=860)
print("landing sheets written")

# Compare sheets moved to compare.py: it emits three pairs (agreement,
# divergence, evidence drawer) and owns compare-*.svg entirely.

# ---------------------------------------------------------- terminal states
def terminal(T, W=1180):
    o = [header(0, 0, W, T, "sales.csv · 240 rows")]
    o += [txt(72, 110, "What was total annual revenue in 2024?", 13.5, 400, T["muted"], t=T),
          f'<rect x="72" y="130" width="6" height="128" fill="{T["signal"]}"/>',
          txt(100, 168, "Which date defines 2024?", 28, 700, T["ink"], t=T),
          txt(100, 198, "This table has two date columns, and they answer different questions. order_date is when revenue", 14, 400, T["ink2"], t=T),
          txt(100, 218, "was earned; signup_date is when the account began.", 14, 400, T["ink2"], t=T),
          f'<rect x="100" y="236" width="290" height="34" rx="4" fill="{T["signal"]}"/>',
          txt(245, 258, "Ask using order_date", 13, 650, T["paper"] if T["name"]=="LIGHT" else T["canvas"], anchor="middle", t=T),
          f'<rect x="402" y="236" width="290" height="34" rx="4" fill="none" stroke="{T["rule"]}"/>',
          txt(547, 258, "Ask using signup_date", 13, 500, T["ink2"], anchor="middle", t=T),
          txt(100, 296, "Nothing was published. Why this is refused ▾", 12.5, 400, T["muted"], t=T),
          rule_line(72, 330, W-72, T)]
    o += [txt(72, 384, "What is the total refund_value where region is West?", 13.5, 400, T["muted"], t=T),
          f'<rect x="72" y="404" width="6" height="110" fill="{T["ink2"]}"/>',
          txt(100, 442, "No findings to publish", 28, 700, T["ink"], t=T),
          txt(100, 472, "The analysis ran and completed. 60 rows matched region = West, and none of them held a", 14, 400, T["ink2"], t=T),
          txt(100, 492, "refund_value, so there was nothing to total. This is not a failure.", 14, 400, T["ink2"], t=T),
          txt(100, 522, "Execution succeeded · 60 rows matched · 0 observations · Show work ▾", 12.5, 400, T["muted"], font=MONO, t=T)]
    return "\n".join(o)

def mobile_terminal(T, W=390):
    return "\n".join([f'<rect x="0" y="0" width="{W}" height="44" fill="{T["paper"]}"/>', rule_line(0,44,W,T),
        mark(16,14,T["signal"],.9), txt(40,27,"Agentic Analytics",12.5,650,T["ink"],t=T),
        txt(20,82,"What was total annual revenue in 2024?",11.5,400,T["muted"],t=T),
        f'<rect x="20" y="100" width="5" height="150" fill="{T["signal"]}"/>',
        txt(40,136,"Which date",22,700,T["ink"],t=T), txt(40,162,"defines 2024?",22,700,T["ink"],t=T),
        txt(40,188,"Two date columns answer",12.5,400,T["ink2"],t=T),
        txt(40,206,"different questions.",12.5,400,T["ink2"],t=T),
        f'<rect x="40" y="220" width="200" height="30" rx="4" fill="{T["signal"]}"/>',
        txt(140,240,"Ask using order_date",12,650,T["paper"] if T["name"]=="LIGHT" else T["canvas"],anchor="middle",t=T),
        txt(40,274,"Nothing was published.",12,400,T["muted"],t=T),
        txt(40,292,"Why this is refused ▾",12,400,T["signal"],t=T),
        rule_line(20,320,W-20,T),
        f'<rect x="20" y="344" width="5" height="120" fill="{T["ink2"]}"/>',
        txt(40,380,"No findings",22,700,T["ink"],t=T), txt(40,406,"to publish",22,700,T["ink"],t=T),
        txt(40,432,"60 rows matched. None held a",12.5,400,T["ink2"],t=T),
        txt(40,450,"refund_value. Not a failure.",12.5,400,T["ink2"],t=T)])

for T in (LIGHT, DARK):
    frame(f'terminal-{T["name"].lower()}', T, "COMPOSITE · DOCUMENTATION ONLY", [
        pane(40, 56, 1180, 580, T, terminal(T)),
        pane(1270, 56, 390, 500, T, mobile_terminal(T)),
        txt(40, 56+580+26, "DOCUMENTATION COMPOSITE — two states shown together for comparison.", 11, 700, T["muted"], t=T),
        txt(40, 56+580+44, "A run reaches exactly one terminal state. See state-*.png for the six individual references.", 11, 400, T["muted"], t=T),
        txt(1270, 56+500+26, "MOBILE 390", 11, 700, T["muted"], t=T)], H=720)
print("compare and terminal sheets written")
