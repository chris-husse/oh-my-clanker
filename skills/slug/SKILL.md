---
name: slug
description: Turn a ticket key, ticket URL, or task description into a short git-branch slug with structured diagnostics. Used headlessly by `omc start` and invocable as /omc:slug.
---

# omc slug

Turn the input below into a **branch slug**, or explain precisely why you cannot.
You MUST end your reply with exactly one `OMC_SLUG` verdict line (format below) —
it is parsed by a machine. No text after it.

## Input

```text
$ARGUMENTS
```

## Steps

1. **Classify the input**: a ticket key (e.g. `PROJ-123`), a ticket URL, or a
   free-text task description.
2. **Resolve a key/URL**: find a configured tool that can read it — a Jira MCP
   server, a GitHub/GitLab MCP or CLI, or similar. Fetch ONLY the ticket's
   title/summary (read-only; never write to the tracker).
   - No tool available that could resolve this kind of reference →
     reason `mcp-missing`. The message must name what to configure (e.g. "no
     Jira MCP server is configured — add one and authenticate it, then retry").
   - A matching tool exists but calls fail with authentication/authorization
     errors → reason `mcp-unauthenticated`. The message must give the exact
     re-auth step for that tool (e.g. "Jira MCP is configured but not
     authenticated — run /mcp and authenticate 'jira', then retry").
   - Tool works but the ticket does not exist / is not readable → reason
     `ticket-not-found`. Message: name the key and say to create it first.
3. **A free-text description** is used directly — no tools needed. If it is too
   thin to name work after (e.g. "stuff"), reason `context-insufficient`; say
   what to add.
4. **Derive the slug**: lowercase, hyphenated, `[a-z0-9-]` only, at most 6
   words and 50 characters. Bake the ticket key in when one exists.
   Example: PROJ-123 "Fix login timeout in auth service" →
   `proj-123-fix-login-timeout`.

## Verdict (REQUIRED, last line, exactly one)

Success: `OMC_SLUG {"ok": true, "slug": "proj-123-fix-login-timeout"}`

Failure: `OMC_SLUG {"ok": false, "reason": "mcp-missing" | "mcp-unauthenticated" | "ticket-not-found" | "context-insufficient", "message": "<one actionable sentence>"}`
