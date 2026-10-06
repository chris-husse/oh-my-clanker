"""Live `omc watch --once`: real git sync + real GitNexus reindex. Most tests
need no tokens; the documentation test requires a Claude token
(require_token)."""

from __future__ import annotations

import re

import pytest

from .harness import configure_omc, make_work_repo, require_token, run_in

pytestmark = pytest.mark.e2e


def _push_remote_commit(container, repo):
    script = (
        f"git clone -q {repo}-origin /work/other && cd /work/other && "
        "echo teammate > t.txt && git add -A && git commit -qm 'remote change' && "
        "git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def test_watch_once_syncs_and_reindexes_for_real(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _push_remote_commit(container, repo)

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "synced main" in out, out
    rc, _ = run_in(container, ["test", "-f", f"{repo}/t.txt"])
    assert rc == 0, "remote commit did not arrive via ff-sync"
    # the REAL gitnexus analyze ran and produced an index
    rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"])
    assert rc == 0, f"watch did not build a real index:\n{out[:1500]}"
    # the wt starter got seeded (create-if-absent)
    rc, _ = run_in(container, ["test", "-f", f"{repo}/.config/wt.toml"])
    assert rc == 0, "ensure_wt_config did not seed the starter"


def test_configure_in_repo_seeds_project_and_global_instructions(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["rm", "/root/.claude/CLAUDE.md"])
    assert rc == 0, out
    rc, out = run_in(container, ["omc", "configure", "--set", "llm.default=claude"], cwd=repo)
    assert rc == 0, out
    for name in ("AGENTS.md", "CLAUDE.md", ".gitignore"):
        rc, _ = run_in(container, ["test", "-e", f"{repo}/{name}"])
        assert rc == 1, f"configure created root {name}"
        rc, _ = run_in(container, ["test", "-L", f"{repo}/{name}"])
        assert rc == 1, f"configure created root {name} symlink"
    rc, global_instructions = run_in(container, ["cat", "/root/.claude/CLAUDE.md"])
    assert rc == 0, global_instructions
    assert global_instructions.count("<!-- omc:begin ") == 1
    assert "# omc behavior layer" in global_instructions
    rc, _ = run_in(container, ["test", "-f", f"{repo}/.omc/config/AGENTS.md"])
    assert rc == 0, "project layer not seeded"


def test_watch_once_up_to_date_still_refreshes_index(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "up to date" in out, out
    # --once checks now and repairs whatever is stale: the REAL index exists
    # afterwards even when there are no new commits
    rc, _ = run_in(container, ["test", "-d", f"{repo}/.gitnexus"])
    assert rc == 0, f"--once left the index missing:\n{out[:1500]}"


def _seed_container_hook(container, repo, body):
    script = (
        f"mkdir -p {repo}/.omc/hooks && cat > {repo}/.omc/hooks/post-watch.sh <<'EOF'\n{body}\nEOF"
    )
    rc, out = run_in(container, ["bash", "-c", script])
    assert rc == 0, out


def test_watch_once_runs_project_post_watch_hook_for_real(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _seed_container_hook(container, repo, 'echo "$OMC_WATCH_OUTCOME" > marker.txt')

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "running project post-watch hook (.omc/hooks/post-watch.sh)" in out, out
    assert "post-watch hook done" in out, out
    # hook really ran, in the repo root, with the outcome env var
    rc, marker = run_in(container, ["cat", f"{repo}/marker.txt"])
    assert rc == 0 and marker.strip() == "refreshed", marker


def test_watch_once_failing_hook_links_log_and_exits_zero(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    _seed_container_hook(container, repo, "echo boom-e2e >&2\nexit 1")

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, out  # hook failure never breaks --once
    m = re.search(r"post-watch hook failed \(exit 1\) — log: (\S+)", out)
    assert m, f"missing failure narration:\n{out}"
    rc, log = run_in(container, ["cat", m.group(1)])
    assert rc == 0 and "boom-e2e" in log, log


def test_watch_auto_build_skips_when_unconfigured(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    rc, out = run_in(container, ["omc", "watch", "--once", "--auto-build"], cwd=repo, timeout=300)
    assert rc == 0, out
    assert "no project build stage configured — skipping auto-build" in out, out


def test_watch_auto_build_runs_stage_via_shim_provider(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container)
    # seed a project build stage + a claude shim that answers with a passing verdict.
    # NOTE: a printf-based one-liner with escaped embedded quotes (as originally
    # sketched) loses its backslashes when the container's shell writes the file,
    # producing an invalid (unquoted-key) OMC_STAGE JSON line. A quoted heredoc
    # sidesteps that escaping entirely, matching _seed_container_hook's style above.
    seed = (
        f"mkdir -p {repo}/.omc/skills/build /shim && "
        f"printf '# build\\nrun true\\n' > {repo}/.omc/skills/build/SKILL.md && "
        "cat > /shim/claude <<'EOF'\n"
        "#!/bin/sh\n"
        "echo 'OMC_STAGE "
        '{"stage": "build", "configured": true, "passed": true, "summary": "ok"}'
        "'\n"
        "EOF\n"
        "chmod +x /shim/claude"
    )
    rc, out = run_in(container, ["bash", "-c", seed])
    assert rc == 0, out
    shim_path = "PATH=/shim:/usr/local/bin:/usr/bin:/bin"
    argv = ["env", shim_path, "omc", "watch", "--once", "--auto-build"]
    rc, out = run_in(container, argv, cwd=repo, timeout=300)
    assert rc == 0, out
    assert "running project build stage via claude" in out, out
    assert "auto-build passed" in out, out


_CLI = "/root/.omc/dependencies/gitnexus/gitnexus/dist/cli/index.js"


def test_watch_once_heals_feature_branch_owned_index(container):
    configure_omc(container, "claude")
    repo = make_work_repo(container, path="/work/heal-repo")
    # Arrange the inversion: FIRST index runs on a feature branch, so the
    # flat store is stamped with it; then the branch dies.
    rc, out = run_in(container, ["git", "switch", "-qc", "feature/first"], cwd=repo)
    assert rc == 0, out
    rc, out = run_in(
        container,
        ["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills"],
        cwd=repo,
        timeout=300,
    )
    assert rc == 0, f"seed analyze failed:\n{out[:1500]}"
    rc, out = run_in(
        container,
        ["bash", "-c", f"grep -o '\"branch\"[^,]*' {repo}/.gitnexus/meta.json"],
    )
    assert "feature/first" in out, f"seed did not stamp the flat store:\n{out}"
    # stale docs mirror that the heal must clear
    rc, _ = run_in(
        container,
        [
            "bash",
            "-c",
            f"mkdir -p {repo}/.omc/docs/gitnexus/docs && "
            f"echo stale > {repo}/.omc/docs/gitnexus/docs/x.md",
        ],
    )
    assert rc == 0
    rc, out = run_in(container, ["git", "switch", "-q", "main"], cwd=repo)
    assert rc == 0, out
    rc, out = run_in(container, ["git", "branch", "-qD", "feature/first"], cwd=repo)
    assert rc == 0, out

    rc, out = run_in(container, ["omc", "watch", "--once"], cwd=repo, timeout=300)
    assert rc == 0, f"watch --once failed:\n{out[:2000]}"
    assert "destroying and rebuilding" in out, out
    assert "index rebuilt for main" in out, out
    assert "docs mirror cleared" in out, out

    # flat store now belongs to main at HEAD; no shadow branch store; registry advanced
    check = (
        "import json, subprocess, sys\n"
        f"meta = json.load(open('{repo}/.gitnexus/meta.json'))\n"
        f"head = subprocess.run(['git', '-C', '{repo}', 'rev-parse', 'HEAD'],"
        " capture_output=True, text=True).stdout.strip()\n"
        "assert meta['branch'] == 'main', meta['branch']\n"
        "assert meta['lastCommit'] == head, (meta['lastCommit'], head)\n"
        "reg = json.load(open('/root/.gitnexus/registry.json'))\n"
        "entries = reg if isinstance(reg, list) else reg.get('repos', [])\n"
        f"entry = [e for e in entries if e.get('path') == '{repo}']\n"
        "assert entry and entry[0]['lastCommit'] == head, entry\n"
        "print('HEAL-OK')\n"
    )
    rc, out = run_in(container, ["python3", "-c", check])
    assert rc == 0 and "HEAL-OK" in out, out
    rc, _ = run_in(container, ["bash", "-c", f"ls {repo}/.gitnexus/branches 2>/dev/null | grep ."])
    assert rc != 0, "shadow branch store survived the heal"
    rc, _ = run_in(container, ["test", "-e", f"{repo}/.omc/docs/gitnexus/docs/x.md"])
    assert rc != 0, "stale docs mirror survived the heal"


def test_watch_once_regroups_a_stale_pinned_wiki(container):
    """The fork's GitNexus (PR #4) regroups on its own once omc runs `wiki` at the
    right time; omc must not carry an unpin workaround. Seed: wiki metadata
    behind the index with a one-module tree, then three distinct concerns land."""
    require_token("claude")
    configure_omc(container, "claude")
    repo = make_work_repo(container, path="/work/regroup-repo")
    rc, out = run_in(
        container,
        ["node", _CLI, "analyze", "--skip-agents-md", "--skip-skills"],
        cwd=repo,
        timeout=300,
    )
    assert rc == 0, out[:1500]
    seed = (
        "import json, os, subprocess\n"
        f"repo = '{repo}'\n"
        "head = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'],"
        " capture_output=True, text=True).stdout.strip()\n"
        "w = repo + '/.gitnexus/wiki'\n"
        "os.makedirs(w, exist_ok=True)\n"
        "tree = [{'name': 'Everything', 'slug': 'everything', 'files': ['README.md']}]\n"
        "json.dump(tree, open(w + '/module_tree.json', 'w'))\n"
        "json.dump(tree, open(w + '/first_module_tree.json', 'w'))\n"
        "json.dump({'fromCommit': head, 'generatedAt': 'x', 'model': 'm', 'lang': '',"
        " 'moduleFiles': {'Everything': ['README.md']}, 'moduleTree': tree},"
        " open(w + '/meta.json', 'w'))\n"
        "open(w + '/everything.md', 'w').write('# Everything\\n')\n"
        "open(w + '/overview.md', 'w').write('# Overview\\n')\n"
    )
    rc, out = run_in(container, ["python3", "-c", seed])
    assert rc == 0, out
    # Three distinct concerns (auth, billing, reports), three files each,
    # landing on origin so watch syncs them: >5 new files trips GitNexus's
    # escalation and the LLM has genuinely separable material to group.
    grow = (
        f"git clone -q {repo}-origin /work/regroup-other && cd /work/regroup-other && "
        "mkdir -p auth billing reports && "
        "printf 'export function login(user, pw) { return user && pw ? "
        "{ok: true, user} : {ok: false}; }\\n' > auth/login.js && "
        "printf 'export function logout(session) { session.active = false; "
        "return session; }\\n' > auth/logout.js && "
        'printf \'export function hashPassword(pw) { return [...pw].reverse().join("");'
        " }\\n' > auth/password.js && "
        "printf 'export function createInvoice(items) { return {total: "
        "items.reduce((a, i) => a + i.price, 0), items}; }\\n' > billing/invoice.js && "
        "printf 'export function charge(card, amount) { return {card: card.slice(-4), "
        'amount, status: "charged"}; }\\n\' > billing/charge.js && '
        'printf \'export function refund(chargeId) { return {chargeId, status: "refunded"}; '
        "}\\n' > billing/refund.js && "
        "printf 'export function dailyReport(rows) { return rows.filter(r => "
        "r.day === new Date().getDay()); }\\n' > reports/daily.js && "
        "printf 'export function monthlyReport(rows) { return rows.filter(r => "
        "r.month === new Date().getMonth()); }\\n' > reports/monthly.js && "
        "printf 'export function exportCsv(rows) { return rows.map(r => "
        'Object.values(r).join(",")).join("\\\\n"); }\\n\' > reports/export.js && '
        "git add -A && git commit -qm 'auth, billing, reports' && git push -q origin main"
    )
    rc, out = run_in(container, ["bash", "-c", grow])
    assert rc == 0, out

    rc, out = run_in(
        container, ["omc", "watch", "--once", "--enable-documentation"], cwd=repo, timeout=1800
    )
    assert rc == 0, f"watch failed:\n{out[:2000]}"
    assert "✓ documentation refreshed" in out, out

    rc, tree = run_in(container, ["bash", "-c", f"/bin/cat {repo}/.gitnexus/wiki/module_tree.json"])
    assert rc == 0 and tree.count('"slug"') > 1, f"module tree still pinned to one module:\n{tree}"
    rc, pages = run_in(container, ["bash", "-c", f"ls {repo}/.omc/docs/gitnexus/docs/*.md | wc -l"])
    assert rc == 0 and int(pages.strip()) > 1, f"docs mirror empty or single page:\n{pages}"
    rc, status = run_in(container, ["omc", "internal", "gitnexus", "status"], cwd=repo)
    assert rc == 0 and '"fresh": true' in status, status
