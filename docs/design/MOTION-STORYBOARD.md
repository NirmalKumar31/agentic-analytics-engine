# Motion and visual signature — storyboard

Every effect below is **state feedback**. None of it is decoration, and each
has a reduced-motion equivalent that is not simply "nothing happens" where
that would remove meaning.

Two rules apply to all of it:

- **No continuous layout work.** Animate `opacity` and `transform` only.
  Nothing animates `width`, `height`, `top` or `left`.
- **Nothing blocks interaction.** A control is clickable before its entrance
  finishes. Motion never gates input.

Forbidden outright: particles, neon glow, looping pulses, fake neural-network
lines, animated gradients behind text, hover-lift on every surface, and any
motion that makes a number harder to read while it moves.

---

## 1 · Landing — the analytical field

| | |
|---|---|
| **Trigger** | Landing mounts, no dataset present |
| **Elements** | One SVG layer of 7 contour paths and 5 sparse coordinate marks, behind all content |
| **Property** | `transform: translateX` on the contour group; `opacity` on the marks |
| **Duration / easing** | 60s linear loop for drift; marks fade 2s `ease-in-out`, staggered |
| **Stateful?** | Ambient, looping — the **only** looping effect in the product |
| **Reduced motion** | Field renders static. It is a texture, not information, so stillness loses nothing |
| **Performance** | Pauses on `visibilitychange`; removed from the DOM once a dataset exists |

The field is landing-only. It does not appear behind a report, ever.

## 2 · Upload → dataset context bar

| | |
|---|---|
| **Trigger** | Upload resolves and the schema profile returns |
| **Elements** | The drop-zone; the new `DatasetContextBar`; the contour field |
| **Property** | Drop-zone `opacity` 1→0 + `translateY` 0→−8px; bar `opacity` 0→1 + `translateY` 8→0; field `opacity` →0 |
| **Duration / easing** | 280ms `cubic-bezier(.2,.8,.25,1)`, bar begins at 120ms |
| **Stateful?** | One-shot |
| **Reduced motion** | Both swap instantly; the field is removed without fading |
| **Performance** | The bar is mounted before the transition starts, so no layout runs mid-animation |

**No scanning animation.** The file is not "analysed" theatrically; the profile
either returned or it did not.

## 3 · Active run — the illuminated trace

| | |
|---|---|
| **Trigger** | A backend stage event arrives. **Never** a timer |
| **Elements** | Five stage nodes, four connectors |
| **Property** | Connector `stroke-dashoffset` draws the trace; node `fill` and `transform: scale(1→1.08→1)` |
| **Duration / easing** | Connector 400ms `ease-out`; node settle 180ms |
| **Stateful?** | Stateful — each stage holds its state until the backend changes it. Motion **stops** when the run ends |
| **Reduced motion** | Nodes change colour and label only; no travelling trace, no scale |
| **Performance** | `stroke-dashoffset` is composited; no layout |

**A stage may not animate as complete until the backend says it is complete.**
An AI interpretation node appears only when a model call actually occurred.

## 4 · Report arrival

| | |
|---|---|
| **Trigger** | The run publishes |
| **Elements** | Answer line; context line; chart series; findings 1–4 |
| **Property** | `opacity` + `translateY` 10→0 for text; chart series `stroke-dashoffset` draws once |
| **Duration / easing** | Answer 260ms; context +80ms; chart 420ms `ease-out`; findings 160ms each, 60ms stagger |
| **Stateful?** | One-shot. The chart never redraws on scroll or hover |
| **Reduced motion** | Everything appears at final state; the chart is drawn complete |
| **Performance** | Findings stagger is capped at 4 regardless of count |

Order is deliberate: **the answer settles before the chart draws**, because the
answer is the thing being reported.

## 5 · Compare — convergence into a verdict

| | |
|---|---|
| **Trigger** | Both strategies reach a terminal state |
| **Elements** | Two strategy rows; the verdict strip; two connecting hairlines |
| **Property** | Hairline `stroke-dashoffset`; verdict `opacity` + `translateY` 6→0 |
| **Duration / easing** | 300ms `ease-out`, hairlines first |
| **Stateful?** | One-shot, and **conditional** |
| **Reduced motion** | Verdict appears with no connecting line animation |
| **Performance** | No effect runs while either side is still running |

**The convergence only plays when the strategies agree.** On disagreement the
hairlines do not join: the diff view appears instead. The motion states a fact
and must not state a false one.

## 6 · Evidence drawer / bottom sheet

| | |
|---|---|
| **Trigger** | "Show work" activated |
| **Elements** | Desktop: right edge sheet. Mobile: bottom sheet. Scrim over the canvas |
| **Property** | `transform: translateX(100%→0)` desktop, `translateY(100%→0)` mobile; scrim `opacity` 0→.4 |
| **Duration / easing** | 240ms in `cubic-bezier(.2,.8,.25,1)`, 180ms out |
| **Stateful?** | Stateful — open or closed |
| **Reduced motion** | Appears and disappears instantly; scrim still renders |
| **Performance** | `will-change: transform` only while animating, removed on completion |

**Focus:** moves to the drawer heading on open, is trapped while open, and
returns to the trigger on Escape or close. This is behaviour, not decoration,
and it is tested.

**Scrim token.** The scrim is a dedicated token, not `ink` at low opacity:
`ink` is near-black in light and near-white in dark, so reusing it made the
page *brighter* behind the drawer in dark mode. Light `#121619` at .30, dark
`#000000` at .62.

## 6a · Evidence drawer — switching strategy tabs

Compare opens the same drawer with one tab per strategy, reached by a single
**Inspect both traces** control.

| | |
|---|---|
| **Trigger** | Tab activated (click, or ← / → while the tablist has focus) |
| **Elements** | The active-tab underline; the trace panel below it |
| **Property** | Underline `transform: translateX` + `width` to the new tab; panel `opacity` 0→1 |
| **Duration / easing** | Underline 160ms `cubic-bezier(.2,.8,.25,1)`; panel 120ms `linear` |
| **Stateful?** | Stateful — which tab is active |
| **Reduced motion** | Underline jumps; panel swaps with no fade |
| **Performance** | Both traces are already in memory. **Switching tabs issues no request and re-runs nothing** — the drawer is a view onto two finished runs |

The panel does not slide horizontally. A sliding panel implies the two traces
are points on a continuum; they are two independent records of two independent
runs, and the only thing that moves is the indicator saying which one is shown.

## 7 · Terminal states

| | |
|---|---|
| **Trigger** | Run reaches refused / no-findings / withheld / quota / failed / cancelled |
| **Elements** | Accent rule, heading, explanation, action control |
| **Property** | `opacity` + `translateY` 8→0; accent rule `scaleY` 0→1 from the top |
| **Duration / easing** | 220ms `ease-out`; rule 180ms |
| **Stateful?** | One-shot |
| **Reduced motion** | Static, full height rule |
| **Performance** | Trivial |

No shake, no flash, no red pulse. A refusal is a result, not an alarm.

**One state, one entrance.** The transition plays once, for the single state
the run reached. There is no cross-fade between terminal states, because no run
passes through two of them. A state that is replaced on screen means the run
was re-submitted, and that re-mounts the whole region rather than morphing one
state into another.

The accent rule's colour is the only thing that varies across the six states,
and it is the only place severity is expressed. **The primary action keeps the
signal colour in every state** — a recovery button drawn in the warning colour
reads as the hazard rather than the exit from it.

## 8 · Theme transition

| | |
|---|---|
| **Trigger** | Theme toggle |
| **Elements** | Every surface, text and rule token |
| **Property** | `background-color` and `color` transition on `:root` |
| **Duration / easing** | 180ms `linear` |
| **Stateful?** | Stateful |
| **Reduced motion** | Instant swap |
| **Performance** | Chart series re-read their tokens; no re-render of data |

The chart ramp switches to its dark variant — both are already
dichromacy-verified.

---

## Visual signature

**Product mark.** A measured field: a baseline, two risers of differing height
and one plotted reading. It is drawn in the signal colour at 16px in the
header, and at 2× on the landing. It is not a logotype and never appears
inside the report canvas.

**Motif.** The same contour geometry as the landing field, used in exactly
three places: the landing background, the mark itself, and the run timeline's
connectors. Nowhere else.

**Typographic signature.** The headline answer is the only display-scale type
in the product — 34px/700, sentence case, never truncated, never all-caps. One
per page. That single rule is what makes a report recognisable at a glance.

**Chart annotation.** A hairline leader from the data point to a short bold
label in ink, never in a bubble or a card. Used once per chart at most.
