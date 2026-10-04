import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'

/*
 * The stylesheet order below is load-bearing, not alphabetical.
 *
 * These modules were cut from one 2,234-line `styles.css` at boundaries
 * that already existed in it. The cascade depends on the order: `states.css`
 * and `motion.css` deliberately override the baseline rules in `shell.css`,
 * `report.css` and the rest, and they win by coming later rather than by
 * specificity or `!important`. `responsive.css` is last because its
 * narrow-viewport containment has to beat everything.
 *
 * Two consequences worth knowing before editing:
 *
 *   - Reordering these lines changes the rendered output even though
 *     nothing in any module changes. `src/test/cssArchitecture.test.ts`
 *     pins the order for that reason.
 *   - A rule's module is decided by where it sat in the cascade, not by
 *     topic. Six modules declare `@media print`, and `print.css` is the
 *     last of them: `motion.css`, `states.css` and `responsive.css` follow
 *     it and declare none, so nothing can override print treatment after
 *     the report's own print rules have been stated. Moving `print.css`
 *     earlier, or adding a print block to a later module, is exactly the
 *     regression this layout prevents.
 *
 * `drawer.css` was the twelfth module. It styled the provenance drawer, the
 * right rail and the bare `.chip`, all three of which were deleted during
 * the redesign; what was left matched nothing, so the module went with
 * them rather than being kept as a record of a component that is gone.
 */
import './styles/tokens.css'
import './styles/reset.css'
import './styles/foundation.css'
import './styles/shell.css'
import './styles/landing.css'
import './styles/composer.css'
import './styles/timeline.css'
import './styles/answer.css'
import './styles/controls.css'
import './styles/workflow.css'
import './styles/findings.css'
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
