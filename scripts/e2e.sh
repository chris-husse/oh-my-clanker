#!/usr/bin/env bash
# E2E runner: build the image ONCE per source tree, run the golden stages
# sequentially (stage snapshots are their side effect), then everything else
# in parallel. Usage: scripts/e2e.sh [golden|rest|all|prune] [pytest args...]
#
# OMC_E2E_NO_DOCKER=1 skips every Docker action (build, sweep, snapshot
# check) — for `--collect-only` runs and the unit test that pins the recipes.
set -euo pipefail
mode=${1:-all}
if [ $# -gt 0 ]; then shift; fi
root=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)

# Source id = the content of the working tree (tracked + untracked, minus
# ignored), so two checkouts with identical content share one image and any
# edit — tracked or not — gets its own id. A temporary index keeps the real
# one untouched; write-tree only adds objects, never refs.
source_id=$(
    # A path that does not exist yet: git rejects an empty pre-created index.
    tmp_index=$(mktemp -u)
    trap 'rm -f "$tmp_index"' EXIT
    GIT_INDEX_FILE="$tmp_index" git -C "$root" add -A >/dev/null 2>&1
    GIT_INDEX_FILE="$tmp_index" git -C "$root" write-tree | cut -c1-12
)
image="omc-e2e:${source_id}"
export OMC_E2E_PREBUILT_IMAGE="$image" OMC_E2E_PREBUILT_SOURCE="$source_id" OMC_E2E_SOURCE_ID="$source_id"
# Ryuk (testcontainers' reaper) prunes images when a session ends — including
# the stage snapshots `docker commit` produces, label override or not
# (observed 2026-10-01; with xdist every worker has its own Ryuk, so snapshots
# vanished mid-run). Fixtures remove their containers on teardown; `sweep`
# below handles a crashed run's leftovers.
export TESTCONTAINERS_RYUK_DISABLED=true

collect_only=0
for arg in "$@"; do [ "$arg" = "--collect-only" ] && collect_only=1; done
no_docker=${OMC_E2E_NO_DOCKER:-0}
[ "$collect_only" = 1 ] && no_docker=1

ensure_image() {
    [ "$no_docker" = 1 ] && return 0
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo "building $image from $root …" >&2
        DOCKER_BUILDKIT=1 docker build -q -f "$root/docker/Dockerfile.e2e" -t "$image" "$root" >/dev/null
        echo "built $image" >&2
    fi
}
sweep() {
    # Only containers a crashed run left behind: never a running one, so a
    # concurrent run on this host (same or another checkout) is untouched.
    [ "$no_docker" = 1 ] && return 0
    docker ps -a --filter status=exited --filter status=created --filter status=dead \
        --format '{{.ID}} {{.Image}}' \
        | awk '$2 ~ /^omc-e2e(:|-stage:)/ {print $1}' | xargs docker rm -f >/dev/null 2>&1 || true
}
run_golden() {
    uv run pytest -m "e2e and golden and not expensive and not codex_gate" -q -n 0 \
        -p no:cacheprovider "$root/tests/e2e/golden" "$@"
    [ "$no_docker" = 1 ] && return 0
    # The stages' side effect is the point: refuse to continue without snapshots.
    if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -q -- "^omc-e2e-stage:.*-${source_id}\$"; then
        echo "golden path left no stage snapshots for ${source_id}" >&2
        return 1
    fi
}
run_rest() {
    uv run pytest -m "e2e and not golden and not expensive and not codex_gate" -q -n auto \
        --dist loadgroup -p no:cacheprovider "$@"
}
prune() {
    # Explicit housekeeping, never automatic: a concurrent run on another
    # checkout may be using an image this checkout considers stale.
    docker images --format '{{.Repository}}:{{.Tag}}' \
        | grep -E '^omc-e2e(:|-stage:)' | grep -v -- "${source_id}\$" \
        | xargs docker rmi -f >/dev/null 2>&1 || true
    echo "kept images for ${source_id}; removed the rest" >&2
}
case "$mode" in
    golden) ensure_image; sweep; run_golden "$@" ;;
    rest) ensure_image; run_rest "$@" ;;
    all) ensure_image; sweep; run_golden "$@"; run_rest "$@" ;;
    prune) prune ;;
    *) echo "usage: scripts/e2e.sh [golden|rest|all|prune] [pytest args]" >&2; exit 2 ;;
esac
