#!/usr/bin/env bash
# Register the omc + superpowers plugins for Claude Code inside the E2E image.
# Idempotent; safe to re-run at container start (network needed for superpowers).
set -euo pipefail

claude plugin marketplace add /repo 2>/dev/null || true
claude plugin install omc@oh-my-clanker --scope user 2>/dev/null || true

claude plugin marketplace add obra/superpowers-marketplace 2>/dev/null || true
claude plugin install superpowers@superpowers-marketplace --scope user 2>/dev/null || true

# OpenCode: local plugin dir registration (no marketplace exists)
mkdir -p ~/.config/opencode/plugins
cp /repo/.opencode/plugins/omc.js ~/.config/opencode/plugins/omc.js

# Codex: repo marketplace registration
codex plugin marketplace add /repo 2>/dev/null || true

echo "plugin setup done"
