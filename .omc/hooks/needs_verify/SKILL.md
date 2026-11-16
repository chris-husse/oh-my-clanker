---
name: needs_verify
description: omc's own needs_verify hook - verify runs unless every path that changed between the two trees is under docs/ or is README.md.
---

# needs_verify (this repo)

The MR gate asks this hook when the tree differs from the last green-verify
tree. The caller's context is `previous=<sha> tree=<sha>`: the last verified
tree and the tree about to be pushed.

1. Read `previous` and `tree` from the context. A missing value → answer
   `"run": true` with the reason "needs_verify was given no previous and
   tree" and stop.
2. From the project root, list the paths that differ between the two trees:

   ```sh
   git diff --name-only <previous> <tree> --no-renames
   ```

   `--no-renames` lists a moved file under both its old and its new path,
   so a file moved out of `skills/` into `docs/` still names its product
   path. When the command fails (a non-zero exit, an unknown tree), the
   answer is `"run": true` with the error as its `reason`, its first line
   of output quoted.
3. Judge every listed path. A path is documentation exactly when it starts
   with `docs/` or is the root `README.md`. Everything else is product,
   `skills/` included: the skills' Markdown is what omc ships, and
   `.omc/` is how this repo runs its own lifecycle.
4. Answer:
   - every path documentation (or no path listed) → `"run": false`, the
     reason naming the documentation the change touched;
   - any product path → `"run": true`, the reason naming the first product
     path in the list.

End with exactly one `OMC_HOOK` line, plain text (no backticks or code
fences around it), its `run` a JSON boolean and its `reason` a JSON string
(quotes in a quoted error escaped). Two examples of its shape:

- `OMC_HOOK {"hook": "needs_verify", "run": false, "reason": "only documentation changed: docs/superpowers/specs/x.md, README.md"}`
- `OMC_HOOK {"hook": "needs_verify", "run": true, "reason": "product path changed: skills/verify/SKILL.md"}`
