---
name: get-mr-description
description: Internal — used by /omc:create-mr; not meant for direct invocation. Produce a markdown MR/PR description for a diff scope (base ref + extent); the first line doubles as the squashed commit's subject.
---

# omc get-mr-description (internal)

## Inputs

- **base** — the ref to compare against (default `origin/<base branch>`).
- **extent** — what to summarize relative to `base` (default `HEAD`).

If not given, infer the most useful scope from context (default
`origin/<base>..HEAD`) and state which scope you used.

## Method

Read the real change, not just the commit messages: `git log <base>..<extent>`
for intent, `git diff <base>..<extent> --stat` for shape, and the diff itself
for anything the messages don't explain.

## Output

Return ONLY the description text, no fences, no commentary:

- **Line 1**: an imperative title, ≤72 characters (it becomes the squashed
  commit's subject and the MR/PR title).
- Blank line.
- **Body** (markdown): the goal of the change, the rough design choices, what
  changed, and how it was tested.

Scale the detail to the change: short and sweet for small diffs, one page
maximum for large ones. Never invent tests or claims the diff doesn't support.
