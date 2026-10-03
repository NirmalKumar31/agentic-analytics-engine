"""Wireframe sheets: desktop + mobile per state, annotated."""
import pathlib

INK, INK2, MUTED, RULE = "#121619", "#49535B", "#5C666F", "#DDE2DC"
SIGNAL, WARN, CANVAS, PAPER = "#0E6E66", "#A63B24", "#F2F4F1", "#FBFCFA"
W, H = 1600, 1100

def box(x, y, w, h, label, sub="", fill=PAPER, stroke=RULE, dash=None, lab_size=13):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" stroke="{stroke}"{d}/>']
    out.append(f'<text x="{x+12}" y="{y+22}" font-size="{lab_size}" font-weight="650" fill="{INK}">{label}</text>')
    if sub:
        for i, line in enumerate(sub.split("|")):
            out.append(f'<text x="{x+12}" y="{y+42+i*16}" font-size="11.5" fill="{MUTED}">{line}</text>')
    return "\n".join(out)

def note(x, y, text, colour=SIGNAL):
    return (f'<circle cx="{x}" cy="{y}" r="9" fill="{colour}"/>'
            f'<text x="{x}" y="{y+4}" font-size="11" font-weight="700" fill="#fff" text-anchor="middle">{text}</text>')

def legend(x, y, items):
    out = []
    for i, (n, t) in enumerate(items):
        out.append(note(x, y + i*26, n))
        out.append(f'<text x="{x+18}" y="{y+i*26+4}" font-size="12.5" fill="{INK2}">{t}</text>')
    return "\n".join(out)

def sheet(name, title, strap, desktop, mobile, notes):
    body = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="{CANVAS}"/>
<g font-family="Inter, -apple-system, Segoe UI, sans-serif">
<text x="70" y="52" font-size="11.5" font-weight="700" fill="{SIGNAL}" letter-spacing=".12em">REDESIGN WIREFRAME · {name.upper()}</text>
<text x="70" y="96" font-size="34" font-weight="700" fill="{INK}">{title}</text>
<text x="70" y="126" font-size="16" fill="{INK2}">{strap}</text>
<line x1="70" y1="152" x2="{W-70}" y2="152" stroke="{RULE}"/>
<text x="70" y="186" font-size="12" font-weight="700" fill="{MUTED}" letter-spacing=".1em">DESKTOP 1440</text>
<text x="1030" y="186" font-size="12" font-weight="700" fill="{MUTED}" letter-spacing=".1em">MOBILE 390</text>
{desktop}
{mobile}
<text x="1230" y="186" font-size="12" font-weight="700" fill="{MUTED}" letter-spacing=".1em">NOTES</text>
{notes}
</g></svg>'''
    pathlib.Path(f'docs/design/wireframes/{name}.svg').write_text(body)
    return name
