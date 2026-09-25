#!/usr/bin/env bash
# Create/refresh the dedicated Codex E2E account cache with device auth.
set -euo pipefail

volume=${CODEX_AUTH_VOLUME:-omc-e2e-codex-auth}
if [[ ! $volume =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    printf 'CODEX_AUTH_VOLUME must be a Docker named volume, not a path\n' >&2
    exit 2
fi

docker volume create "$volume" >/dev/null
docker run --rm -it \
    --mount "type=volume,src=$volume,dst=/codex-auth" \
    -e CODEX_HOME=/tmp/omc-codex-home \
    node:22-bookworm-slim bash -c '
set -euo pipefail
npm install -g @openai/codex@0.156.1 >/dev/null
mkdir -p "$CODEX_HOME"
codex login --device-auth
node -e "
const fs = require(\"node:fs\");
const src = process.env.CODEX_HOME + \"/auth.json\";
const data = JSON.parse(fs.readFileSync(src, \"utf8\"));
if (!data || Array.isArray(data) || typeof data !== \"object\") {
  throw new Error(\"Codex login did not create a valid auth cache\");
}
const tmp = \"/codex-auth/.auth-\" + process.pid;
try {
  fs.copyFileSync(src, tmp);
  fs.chmodSync(tmp, 0o600);
  fs.renameSync(tmp, \"/codex-auth/auth.json\");
} finally {
  if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
}
"
'
printf 'Codex E2E account cache saved in Docker volume %s\n' "$volume"
