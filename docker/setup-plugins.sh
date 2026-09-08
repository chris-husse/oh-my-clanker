#!/usr/bin/env bash
# Register the omc + superpowers plugins for Claude Code inside the E2E image.
# Idempotent; safe to re-run at container start (network needed for superpowers).
set -euo pipefail

claude plugin marketplace add /repo 2>/dev/null || true
claude plugin install omc@oh-my-clanker --scope user 2>/dev/null || true

# superpowers from obra's marketplace on purpose: the omc manifest declares no
# dependency (see PLUGIN-NOTES.md, "Resolution 2"), so ensure_plugin must accept
# a superpowers@<any-marketplace> — the image proves the non-official case.
claude plugin marketplace add obra/superpowers-marketplace 2>/dev/null || true
claude plugin install superpowers@superpowers-marketplace --scope user 2>/dev/null || true

# OpenCode: local plugin dir registration (no marketplace exists)
mkdir -p ~/.config/opencode/plugins
cp /repo/.opencode/plugins/omc.js ~/.config/opencode/plugins/omc.js

# Codex: marketplace registration AND install — registration alone leaves the
# plugin "not installed", which serves no skills. Verbs are add/remove.
codex plugin marketplace add /repo 2>/dev/null || true
codex plugin add omc@oh-my-clanker 2>/dev/null || true

# superpowers for codex from obra: codex's own curated catalog entry is
# admin-blocked on managed machines (exit 1), so the git marketplace is the
# route that actually works.
codex plugin marketplace add obra/superpowers-marketplace 2>/dev/null || true
codex plugin add superpowers@superpowers-marketplace 2>/dev/null || true

echo "plugin setup done"
