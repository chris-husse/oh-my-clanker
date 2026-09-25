#!/usr/bin/env bash
# Install and validate only the selected provider at runtime. With no argument,
# provision both at image build time; the Dockerfile treats that as optional.
set -euo pipefail

provider=${1:-all}
case "$provider" in claude|codex|all) ;; *) echo "unknown provider: $provider" >&2; exit 2 ;; esac
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

run_step() {
    local label=$1 output=$2
    shift 2
    if "$@" >"$output" 2>"$scratch/stderr"; then
        return 0
    fi
    printf '%s failed: ' "$label" >&2
    printf '%s ' "$@" >&2
    printf '\n' >&2
    python3 - "$scratch/stderr" "$output" <<'PY' >&2
import os, sys
text = "".join(open(path, errors="replace").read() for path in sys.argv[1:])
for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
    if value := os.environ.get(name):
        text = text.replace(value, "[redacted]")
sys.stderr.write(text[-2000:])
PY
    printf '\nFix the reported plugin/marketplace error, then rerun this E2E test.\n' >&2
    return 1
}

setup_claude() {
    # Re-adding an already registered marketplace exits successfully on the
    # tested Claude CLI. A nonzero exit is a real setup failure and is reported.
    run_step 'Claude marketplace registration' "$scratch/claude-marketplace" \
        claude plugin marketplace add /repo
    run_step 'Claude OMC installation' "$scratch/claude-omc" \
        claude plugin install omc@oh-my-clanker --scope user
    run_step 'Claude Superpowers marketplace registration' "$scratch/claude-super-marketplace" \
        claude plugin marketplace add obra/superpowers-marketplace
    run_step 'Claude Superpowers installation' "$scratch/claude-super" \
        claude plugin install superpowers@superpowers-marketplace --scope user
    run_step 'Claude plugin listing' "$scratch/claude-list" claude plugin list --json
    python3 - "$scratch/claude-list" <<'PY'
import json, sys
try:
    entries = {p["id"]: p for p in json.load(open(sys.argv[1]))}
    for key in ("omc@oh-my-clanker", "superpowers@superpowers-marketplace"):
        p = entries[key]
        assert p.get("enabled") is True and not p.get("errors")
except (ValueError, KeyError, TypeError, AssertionError):
    sys.exit("Claude plugin setup incomplete: omc and superpowers must be enabled")
PY
}

setup_codex() {
    # Codex 0.156.1 plugin add --json returns the installedPath. Keep that
    # metadata for pure readiness checks later in this container.
    run_step 'Codex marketplace registration' "$scratch/codex-marketplace" \
        codex plugin marketplace add /repo
    run_step 'Codex OMC installation' "$scratch/codex-omc.json" \
        codex plugin add omc@oh-my-clanker --json
    run_step 'Codex Superpowers marketplace registration' "$scratch/codex-super-marketplace" \
        codex plugin marketplace add obra/superpowers-marketplace
    run_step 'Codex Superpowers installation' "$scratch/codex-super.json" \
        codex plugin add superpowers@superpowers-marketplace --json
    run_step 'Codex plugin listing' "$scratch/codex-list.json" codex plugin list --json
    run_step 'Codex installed payload verification' "$scratch/codex-verified" \
        env PYTHONPATH=/repo python3 -m tests.e2e.codex_plugin_payload record \
        "$scratch/codex-omc.json" "$scratch/codex-super.json" "$scratch/codex-list.json"
}

case "$provider" in
    claude) setup_claude ;;
    codex) setup_codex ;;
    all) setup_claude; setup_codex ;;
esac
echo "plugin setup done for $provider"
