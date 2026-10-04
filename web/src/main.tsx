import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'

/*
 * The stylesheet order below is load-bearing, not alphabetical.
 *
 * These twelve modules were cut from one 2,234-line `styles.css` at
 * boundaries that already existed in it, and concatenating them in this
 * exact order reproduces that file byte for byte. The cascade depends on
 * it: `states.css` and `motion.css` deliberately override the baseline
 * rules in `shell.css`, `report.css` and the rest, and they win by coming
 * later rather than by specificity or `!important`. `responsive.css` is
 * last because its narrow-viewport containment has to beat everything.
 *
 * Two consequences worth knowing before editing:
 *
 *   - Reordering these lines changes the rendered output even though
 *     nothing in any module changes. `src/test/cssArchitecture.test.ts`
 *     pins the order for that reason.
 *   - A rule's module is decided by where it sat in the cascade, not by
 *     topic. `print.css` holds the first `@media print` block; a second
 *     one lives at the end of `states.css`, because moving it up here
 *     would let earlier state rules override print treatment. Grouping
 *     the two "sensibly" into one file is exactly the regression this
 *     layout prevents.
 */
import './styles/tokens.css'
import './styles/reset.css'
import './styles/foundation.css'
import './styles/shell.css'
import './styles/landing.css'
import './styles/composer.css'
import './styles/timeline.css'
import './styles/controls.css'
import './styles/workflow.css'
import './styles/findings.css'
import './styles/drawer.css'
import './styles/audit.css'
import './styles/report.css'
import './styles/print.css'
import './styles/motion.css'
import './styles/states.css'
import './styles/responsive.css'

const container = document.getElementById('root')
if (container) {
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}
