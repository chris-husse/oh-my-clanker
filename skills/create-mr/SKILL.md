---
name: create-mr
description: Internal — used by /omc:finish; not meant for direct invocation. Given a squashed feature branch, generate the MR description, amend it into the commit, and push with --force-with-lease. Never calls a forge API — the user creates the MR/PR.
---

# omc create-mr (internal)

Precondition: the current branch holds exactly the work to publish — typically
one squashed commit ahead of `origin/<base>` (that's what `/omc:finish`
guarantees) — and the working tree is clean.

## Step 1 — the description

Invoke `get-mr-description` with `base = origin/<base>` and `extent = HEAD`.

## Step 2 — amend it into the commit

`git commit --amend` so the squashed commit's message IS the description
(title line + body). Forges auto-fill the MR/PR title and text from this
commit — that is why no MR is created here.

## Step 3 — push

```sh
git push --force-with-lease origin <branch>
```

Force because `/omc:finish` rewrote history; the lease guards against
clobbering someone else's push. First push of a new branch: plain
`git push -u origin <branch>` is fine. If the push is REJECTED, surface the
error and stop — never retry blindly, never `--force`.

## Step 4 — report

State: branch pushed, one commit, its title. Then a convenience pointer,
derived from `git config --get remote.origin.url` (redact any credentials in
the URL before printing):

- GitHub host → `https://github.com/<owner>/<repo>/compare/<base>...<branch>?expand=1`
- GitLab host → `https://<host>/<owner>/<repo>/-/merge_requests/new?merge_request[source_branch]=<branch>&merge_request[target_branch]=<base>`
- Anything else (or no recognizable forge) → "pushed `<branch>` — open the
  MR/PR from your forge's UI".

**Do not create the MR/PR. Do not call `gh`, `glab`, or any forge API.**
