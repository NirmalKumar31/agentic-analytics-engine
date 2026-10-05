/**
 * What the browser downloads before it can show anything.
 *
 * A redesign is the easiest way to quietly double a bundle: a component
 * library arrives for one control, an icon set for one glyph, a charting
 * runtime moves onto the critical path because somebody removed a dynamic
 * `import()`. None of those changes looks wrong on its own and none of them
 * fails a test.
 *
 * This reads `dist/` and fails on each. It is a script rather than a vitest
 * case because CI runs `npm run test` *before* `npm run build`: a test that
 * needs the build would either fail there or -- worse -- be written to skip
 * when `dist/` is missing, which is a bundle check that never runs. It is
 * wired as its own CI step after the build, the same shape as
 * `check-playwright-skips.mjs`.
 *
 * ---------------------------------------------------------------- measured
 *
 * The baseline is the branch point, 0d82e87, built from the same lockfile in
 * a worktree and measured by running this script against its `dist`. Both
 * columns are real builds, not estimates:
 *
 *                     0d82e87      feat/visual-redesign     delta
 *   index.css        32.89 kB           41.87 kB          +8.98 kB
 *   index.js        316.54 kB          307.13 kB          -9.41 kB
 *   vega.js         840.81 kB          840.81 kB        same chunk hash
 *
 *   initial payload, gzipped, as this script measures it:
 *                   102.20 kB          103.72 kB          +1.52 kB
 *                   (css 7.55,         (css 8.66,
 *                    js 94.65)          js 95.06)
 *
 * That is +1.5% on what the browser downloads first. (Vite's build log
 * reports slightly larger gzip figures, because it compresses at a
 * different level; the numbers above are the ones this script prints, so
 * they can be re-measured.)
 *
 * The stylesheet grew because seventeen modules of new surfaces replaced
 * seven panels, and because those modules carry their reasoning. The
 * application code shrank, because ten components were deleted and their
 * replacements are smaller. Vega's chunk is byte-identical across the two
 * builds -- its content hash did not change -- and still separate.
 */

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { gzipSync } from 'node:zlib'

const DIST = process.argv[2] ?? 'dist'
const ASSETS = join(DIST, 'assets')

/** Gzipped kB of the initial payload: stylesheet plus entry chunk. */
const PAYLOAD_CEILING_KB = 120
/** Gzipped kB of the stylesheet on its own, which is what the redesign grew. */
const STYLESHEET_CEILING_KB = 12
/** Raw bytes above which a chunk is "large"; only Vega may be. */
const LARGE_CHUNK = 400 * 1024

const problems = []
const notes = []

if (!existsSync(ASSETS)) {
  console.error(
    `No ${ASSETS}. Build first: this check reads the production build, and ` +
      'skipping when there is no build is how a bundle check stops running.',
  )
  process.exit(1)
}

/** `index-C0Kl3TBY.css` -> `index.css`, so a rebuild does not break a check. */
const unhashed = (name) => name.replace(/-[A-Za-z0-9_-]{8,}\./, '.')

const files = new Map()
for (const file of readdirSync(ASSETS)) files.set(unhashed(file), file)

const kB = (bytes) => (bytes / 1024).toFixed(2) + ' kB'
const gz = (logical) =>
  gzipSync(readFileSync(join(ASSETS, files.get(logical)))).byteLength

// ------------------------------------------------------------- Vega is lazy
//
// The charting runtime is 861 kB raw, 296 kB gzipped -- nearly three times
// the rest of the application. It sits behind a dynamic `import()` in
// `Chart.tsx` so a reader who never runs an analysis never downloads it.
// Removing the `await import(...)` is a one-line change that no other test
// notices: the chart still renders, and every assertion about it still
// passes.
const html = readFileSync(join(DIST, 'index.html'), 'utf8')
if (/vega/i.test(html)) {
  problems.push('index.html references Vega, so it is fetched with the page')
}
if (!files.has('vega.js')) {
  problems.push('Vega is not a chunk of its own')
} else if (files.has('index.js')) {
  const entry = readFileSync(join(ASSETS, files.get('index.js')), 'utf8')
  const chunk = files.get('vega.js')
  if (new RegExp(`(^|[^.])import\\s*["'][^"']*${chunk}`).test(entry)) {
    problems.push('the entry chunk imports Vega statically')
  }
  notes.push(`vega chunk ${kB(statSync(join(ASSETS, chunk)).size)}, lazy`)
}

// ------------------------------------------------------------ initial size
if (files.has('index.css') && files.has('index.js')) {
  const css = gz('index.css')
  const js = gz('index.js')
  notes.push(`initial payload ${kB(css + js)} gzipped (css ${kB(css)}, js ${kB(js)})`)
  if (css + js >= PAYLOAD_CEILING_KB * 1024) {
    problems.push(
      `initial payload ${kB(css + js)} gzipped, over the ${PAYLOAD_CEILING_KB} kB ceiling`,
    )
  }
  if (css >= STYLESHEET_CEILING_KB * 1024) {
    problems.push(
      `stylesheet ${kB(css)} gzipped, over the ${STYLESHEET_CEILING_KB} kB ceiling`,
    )
  }
} else {
  problems.push('no index.css / index.js in the build')
}

// ----------------------------------------------------- one runtime, not two
//
// Two charting libraries, or two date libraries, is how a bundle doubles
// without any single change looking wrong.
const large = readdirSync(ASSETS)
  .filter((f) => f.endsWith('.js'))
  .filter((f) => statSync(join(ASSETS, f)).size > LARGE_CHUNK)
  .map(unhashed)
  .sort()
if (large.join(',') !== 'vega.js') {
  problems.push(`more than one large chunk: ${large.join(', ')}`)
}

for (const note of notes) console.log(`  ${note}`)
if (problems.length > 0) {
  console.error('The production bundle did not prove what it needed to:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log('Bundle: Vega lazy, one large chunk, initial payload under ceiling.')
