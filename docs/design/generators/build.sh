#!/usr/bin/env bash
# Regenerate every design sheet, rasterise it, and audit the set.
#
# Run from the repository root. Playwright comes from web/node_modules, which
# is the only Chromium this repo already depends on -- the sheets are
# rasterised by the same browser the e2e suite uses.
set -euo pipefail
cd "$(dirname "$0")/../../.."
G=docs/design/generators

python3 "$G/wf.py"
python3 "$G/sheets.py"
python3 "$G/compare.py"
python3 "$G/states.py"

node "$G/render.mjs" docs/design/*.svg docs/design/mockups/*.svg docs/design/wireframes/*.svg

python3 "$G/audit_mockups.py"
