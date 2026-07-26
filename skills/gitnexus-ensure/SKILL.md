---
name: gitnexus-ensure
description: Internal — used by the gitnexus-* skills; not meant for direct invocation. Ensure the GitNexus CLI is installed and healthy under ~/.omc/dependencies/gitnexus (approved-source-only clone + build).
---

# omc gitnexus-ensure (internal)

GitNexus is omc's managed code-knowledge-graph dependency, installed and built
by the Python CLI. It runs as `node <CLI>` where:

    CLI = ~/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js

(`~/.omc` is `$OMC_HOME` when that env var is set.)

## Ensure it

Run:

    omc internal gitnexus ensure

This installs GitNexus when missing (approved-source clone from
`https://github.com/chris-husse/GitNexus.git` + the two-step npm build) and is a
silent no-op when the CLI is already healthy. It refuses any existing clone
whose origin is not the approved source. Report what it prints; on a non-zero
exit, surface its output and stop — never claim success on a broken build.
