---
name: hook
description: Internal — used by /omc:design, /omc:rebase-main and create-mr's MR gate; not meant for direct invocation. Run one project hook (.omc/hooks/<name>/SKILL.md in the repo omc is invoked from) as a black box and relay its single OMC_HOOK verdict; reports an absent or failed hook, never decides what the caller does with it.
---

# omc hook (project-hook proxy)

There is nothing omc-specific to do here: this skill runs one of the
PROJECT's hooks, if the project defines it, and reports its answer. It only
reports; it never edits the hook file.

## User Input

```text
$ARGUMENTS
```

The input is `<name> [key=value …]`. The first word is the hook name; the
rest is the caller's context, passed to the hook unchanged. The names and
their contexts:

| Hook | Context the caller passes | Answer field |
|---|---|---|
| `needs_review` | `previous=<sha> tree=<sha>` | `run` |
| `needs_verify` | `previous=<sha> tree=<sha>` | `run` |
| `post_design` | `record=<path>` | `go` |
| `post_rebase` | `base=<branch> old_merge_base=<sha> new_base=<sha> squash=true\|false` | `ok`, plus `before_implementation` |
| `pre_push` | `base=<branch> branch=<branch>` | `ok` |

Every answer also carries `reason`, one sentence. A name not in the table →
the error shape (Step 4) with `"error": "unknown hook <name>"`.

## Steps

1. Resolve the project root: `git rev-parse --show-toplevel`; if not in a
   git repo, use the current directory. Look for
   `<project-root>/.omc/hooks/<name>/SKILL.md`.
2. **Missing** → the verdict is
   `OMC_HOOK {"hook": "<name>", "configured": false}`.
3. **Present** → read it and follow its instructions as a black box, running
   commands from the project root, with the caller's context. Take its
   instructions at face value; the proxy never edits the hook file, or
   anything else, to change its answer.
4. Validate the hook's last `OMC_HOOK` line before relaying anything. The
   hook failed when it printed no `OMC_HOOK` line, when that line's JSON
   does not parse, when it carries a `hook` field that is not `<name>`, when
   it lacks the hook's answer field (or, for `post_rebase`,
   `before_implementation`), or when an answer field is not a JSON boolean
   (an echoed `true|false` template fails here). A failed hook → the verdict
   is `OMC_HOOK {"hook": "<name>", "configured": true, "error": "<why>"}`,
   with `<why>` one sentence naming which of these it was; the hook's own
   line is not relayed.
5. Relay only a valid line: the hook's last `OMC_HOOK` line, its JSON
   unchanged, stripped of any markdown wrapping (backticks or code fences)
   the hook put around it.

What a caller does with an absent or failed hook is the caller's rule; the
proxy only reports.

**Always** end with exactly one machine-readable `OMC_HOOK` line (plain
text, no backticks or code fences around it): the hook's own valid line, or the
absent or error shape above.
