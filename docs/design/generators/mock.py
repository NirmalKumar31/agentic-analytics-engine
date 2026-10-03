"""High-fidelity mockups: real content, real labels, light and dark."""
import pathlib

LIGHT = dict(canvas="#F2F4F1", paper="#FBFCFA", raised="#FFFFFF", inset="#E7EAE5",
             ink="#121619", ink2="#49535B", muted="#5C666F", rule="#DDE2DC",
             signal="#0E6E66", signal_weak="#DCEDEA", warn="#A63B24", warn_weak="#F7E4DF",
             grid="#E6E9E4", scrim="#121619", scrim_op=".30", name="LIGHT")
DARK = dict(canvas="#0E1113", paper="#14181B", raised="#1A1F23", inset="#0A0D0F",
            ink="#EDF1F3", ink2="#A3AEB6", muted="#8C98A1", rule="#232A2F",
            signal="#45C3B2", signal_weak="#10322E", warn="#E8755A", warn_weak="#391A12",
            grid="#1E2429", scrim="#000000", scrim_op=".62", name="DARK")
SERIES = ["#cc7682", "#7d69b5", "#7a5b1d", "#1d5860", "#244824"]
SERIES_D = ["#b44352", "#8a78bc", "#c3912e", "#54becc", "#a1d1a1"]
FONT = "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"

def mark(x, y, c, s=1.0):
    """The product mark: a measured field — baseline, two risers, one reading."""
    return (f'<g transform="translate({x},{y}) scale({s})" fill="none" stroke="{c}" '
            f'stroke-width="1.8" stroke-linecap="round">'
            f'<path d="M0 14 L16 14"/><path d="M3 14 L3 8"/><path d="M8 14 L8 4"/>'
            f'<circle cx="13" cy="6" r="2.2" fill="{c}" stroke="none"/></g>')

def esc(s):
    """XML-escape. `Home & Kitchen` is real data and a raw & is invalid SVG."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("&amp;amp;", "&amp;").replace("&amp;#", "&#"))


def txt(x, y, s, size=14, w=400, fill=None, font=FONT, anchor="start", op=1, t=None):
    T = t or LIGHT
    s = esc(s)
    f = fill or T["ink"]
    o = f' opacity="{op}"' if op != 1 else ""
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" '
            f'font-weight="{w}" fill="{f}" text-anchor="{anchor}"{o}>{s}</text>')

def rule_line(x1, y, x2, T):
    return f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{T["rule"]}"/>'

def header(x, y, w, T, dataset=None):
    o = [f'<rect x="{x}" y="{y}" width="{w}" height="52" fill="{T["paper"]}"/>',
         rule_line(x, y+52, x+w, T),
         mark(x+16, y+18, T["signal"]),
         txt(x+42, y+31, "Agentic Analytics", 14, 650, T["ink"], t=T)]
    if dataset:
        o.append(txt(x+w/2, y+31, dataset, 12.5, 400, T["muted"], anchor="middle", t=T))
    o.append(txt(x+w-16, y+31, "Governed ▾    ◑    End session", 12.5, 400, T["muted"], anchor="end", t=T))
    return "\n".join(o)

def line_chart(x, y, w, h, T, series, pts=None, labels=None, lo=1300, hi=1800,
               title="Revenue by quarter", unit="thousands", annot="+21% vs Q2"):
    """A real line chart. Values, labels and units are passed in, because a
    chart whose axis contradicts the question above it is worse than no
    chart: it looks like evidence and is not."""
    pts = pts or [1412, 1455, 1498, 1710]
    labels = labels or ["2025-01", "2025-04", "2025-07", "2025-10"]
    n = len(pts)
    px = lambda i: x + 72 + i * ((w - 118) / (n - 1))
    # 96, not 78: the plot area has to start below the unit caption at y+32,
    # or the top gridline's label lands on top of it. It did, on every chart
    # in the package.
    py = lambda v: y + h - 42 - (v - lo) / (hi - lo) * (h - 96)
    step = (hi - lo) / 5
    o = []
    for k in range(6):
        gv = lo + k * step
        o.append(f'<line x1="{x+66}" y1="{py(gv)}" x2="{x+w-20}" y2="{py(gv)}" stroke="{T["grid"]}"/>')
        o.append(txt(x+58, py(gv)+4, f"{gv:,.0f}", 10.5, 400, T["muted"], font=MONO, anchor="end", t=T))
    d = " ".join(("M" if i == 0 else "L") + f"{px(i)} {py(v)}" for i, v in enumerate(pts))
    o.append(f'<path d="{d}" fill="none" stroke="{series[3]}" stroke-width="2.6" stroke-linejoin="round"/>')
    for i, v in enumerate(pts):
        o.append(f'<circle cx="{px(i)}" cy="{py(v)}" r="3.4" fill="{T["paper"]}" stroke="{series[3]}" stroke-width="2"/>')
        o.append(txt(px(i), y+h-18, labels[i], 10.5, 400, T["muted"], font=MONO, anchor="middle", t=T))
    if w > 500 and annot:
        # Clamped: a series that peaks at the top of its range would otherwise
        # put the annotation on the chart title.
        ay = max(py(pts[-1]) - 38, y + 52)
        o.append(f'<line x1="{px(n-1)}" y1="{py(pts[-1])-10}" x2="{px(n-1)-66}" y2="{ay+4}" stroke="{T["ink2"]}" stroke-width="1"/>')
        o.append(txt(px(n-1)-70, ay, annot, 11.5, 650, T["ink"], anchor="end", t=T))
        o.append(txt(x+w, y+16, "table ·  evidence ·  download", 12, 400, T["signal"], anchor="end", t=T))
    o.append(txt(x, y+16, title, 14, 650, T["ink"], t=T))
    if unit:
        o.append(txt(x, y+32, f"{unit}", 10.5, 400, T["muted"], font=MONO, t=T))
    return "\n".join(o)


def table(x, y, w, T, rows, cols, widths):
    o = [rule_line(x, y, x+w, T)]
    cx = x
    for c, cw in zip(cols, widths):
        anchor = "end" if cw < 0 else "start"
        cw = abs(cw)
        o.append(txt(cx + (cw-12 if anchor == "end" else 0), y+20, c, 11.5, 650, T["muted"], t=T, anchor=anchor))
        cx += cw
    o.append(rule_line(x, y+30, x+w, T))
    for r, row in enumerate(rows):
        yy = y + 30 + (r+1) * 27
        if r % 2 == 1:
            o.append(f'<rect x="{x}" y="{yy-19}" width="{w}" height="27" fill="{T["inset"]}" opacity=".55"/>')
        cx = x
        for v, cw in zip(row, widths):
            anchor = "end" if cw < 0 else "start"
            font = MONO if anchor == "end" else FONT
            cw2 = abs(cw)
            o.append(txt(cx + (cw2-12 if anchor == "end" else 0), yy, v, 12.5, 400,
                         T["ink"] if anchor == "end" else T["ink2"], font=font, anchor=anchor, t=T))
            cx += cw2
    return "\n".join(o)
