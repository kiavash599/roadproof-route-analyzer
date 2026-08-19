#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${SITES_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/sites-env.sh" -- "$0" "$@"
fi

vinext_cli="${SITES_PROJECT_ROOT}/node_modules/vinext/dist/cli.js"
if [[ ! -f "${vinext_cli}" ]]; then
  echo "vinext is unavailable. Run npm run install:ci and wait for it to finish before building." >&2
  exit 69
fi

echo "Running bounded vinext build..."
node "${script_dir}/run-bounded.mjs" \
  "${SITES_BUILD_TIMEOUT:-3m}" \
  "${SITES_BUILD_KILL_AFTER:-10s}" \
  "$(command -v node)" \
  "${vinext_cli}" build

"${script_dir}/validate-artifact.sh"
