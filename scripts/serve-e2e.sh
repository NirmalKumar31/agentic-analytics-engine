#!/usr/bin/env bash
# A server with the ceilings CI's container has.
#
# The browser suite is not self-starting: it runs against whatever is at
# AAE_E2E_BASE_URL. That made it easy to develop against a server with the
# abuse controls turned off -- 5000 uploads an hour, 600 live sessions --
# and to report "all tests passed" from a run that could never have
# exhausted anything. CI then failed on both ceilings at once, and the
# numbers that mattered had to be reconstructed from rejection messages in
# its log.
#
# These values are the ones `.github/workflows/ci.yml` passes to
# `docker run`, with one addition: AAE_MAX_ACTIVE_UPLOAD_SESSIONS is set
# explicitly to the default the workflow was silently relying on. Making it
# visible is the point -- it is the ceiling Chromium hit first.
#
# Usage, from the repository root:
#
#   scripts/serve-e2e.sh 8124 &
#   cd web && AAE_E2E_BASE_URL=http://127.0.0.1:8124 npm run test:e2e
#
# A relaxed server is fine for exploring a single spec. It is not evidence.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-8124}"

export AAE_PROVIDER_MODE=fake
export AAE_LIVE_ANALYTICS_ENABLED=true
export AAE_UPLOADS_ENABLED=true
# The four abuse controls, exactly as CI sets them.
export AAE_UPLOADS_PER_IP_PER_HOUR=200
export AAE_ANALYSES_PER_IP_PER_HOUR=200
export AAE_ANALYSES_PER_SESSION=40
# Not set by the workflow, which is how it ran at the default of 24 without
# anyone choosing 24. Stated here so a local run meets the same wall.
export AAE_MAX_ACTIVE_UPLOAD_SESSIONS=24

echo "Serving on http://127.0.0.1:${PORT} with CI's ceilings:"
echo "  uploads/IP/hour        ${AAE_UPLOADS_PER_IP_PER_HOUR}"
echo "  analyses/IP/hour       ${AAE_ANALYSES_PER_IP_PER_HOUR}"
echo "  analyses/session       ${AAE_ANALYSES_PER_SESSION}"
echo "  live upload sessions   ${AAE_MAX_ACTIVE_UPLOAD_SESSIONS}"
echo "  provider mode          ${AAE_PROVIDER_MODE}"

exec .venv/bin/uvicorn agentic_analytics.api.app:app --host 127.0.0.1 --port "${PORT}"
