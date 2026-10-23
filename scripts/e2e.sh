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
holder_ttl=$((72 * 3600))
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

prune_stale() {
    [ "$no_docker" = 1 ] && return 0
    local now holders holder details holder_id holder_source created tag tag_source
    local pinned_sources=" $source_id "
    local -a stale_ids=() stage_tags=() base_tags=()
    now=$(date +%s)
    holders=$(docker ps -aq --filter label=omc.e2e=holder)
    while IFS= read -r holder; do
        [ -z "$holder" ] && continue
        # A delimiter keeps a missing label (which Docker may print as
        # <no value>) from shifting the source and creation fields.
        if ! details=$(docker inspect --format \
            '{{.Id}}|{{index .Config.Labels "omc.e2e.source"}}|{{index .Config.Labels "omc.e2e.created"}}' "$holder"); then
            echo "could not inspect E2E holder $holder; nothing pruned" >&2
            return 1
        fi
        IFS='|' read -r holder_id holder_source created <<< "$details"
        if [ -z "$holder_id" ]; then
            echo "could not inspect E2E holder $holder; nothing pruned" >&2
            return 1
        fi
        if [ "$holder_source" != "$source_id" ] && \
            { [[ ! "$created" =~ ^[0-9]+$ ]] || (( now - 10#$created > holder_ttl )); }; then
            stale_ids+=("$holder_id")
        else
            pinned_sources+=" $holder_source "
        fi
    done <<< "$holders"

    if [ "${#stale_ids[@]}" -gt 0 ]; then
        docker rm -f "${stale_ids[@]}" >/dev/null
    fi

    local images
    images=$(docker images --format '{{.Repository}}:{{.Tag}}')
    while IFS= read -r tag; do
        case "$tag" in
            omc-e2e-stage:*|omc-e2e:*)
                tag_source=${tag##*:}
                tag_source=${tag_source##*-}
                ;;
            *) continue ;;
        esac
        [[ $tag_source =~ ^[0-9a-f]{12}$ ]] || continue
        [[ $pinned_sources == *" $tag_source "* ]] && continue
        case "$tag" in
            omc-e2e-stage:*) stage_tags+=("$tag") ;;
            omc-e2e:*) base_tags+=("$tag") ;;
        esac
    done <<< "$images"

    # Children first: on classic Docker storage, removing a base tag while
    # snapshots exist leaves its layers behind as a dangling parent.
    if [ "${#stage_tags[@]}" -gt 0 ]; then
        docker rmi -f "${stage_tags[@]}" >/dev/null
    fi
    if [ "${#base_tags[@]}" -gt 0 ]; then
        docker rmi -f "${base_tags[@]}" >/dev/null
    fi
    if [ "${#stale_ids[@]}" -gt 0 ] || [ "${#stage_tags[@]}" -gt 0 ] || [ "${#base_tags[@]}" -gt 0 ]; then
        echo "pruned ${#stale_ids[@]} stale holders and $((${#stage_tags[@]} + ${#base_tags[@]})) images (older than 72 h)" >&2
    fi
}
ensure_image() {
    [ "$no_docker" = 1 ] && return 0
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        echo "building $image from $root …" >&2
        DOCKER_BUILDKIT=1 docker build -q -f "$root/docker/Dockerfile.e2e" -t "$image" "$root" >/dev/null
        echo "built $image" >&2
    fi
}
hold_image() {
    # Pin an image with an idle container so an external image GC cannot
    # remove it mid-run (Docker Desktop's kubelet under disk pressure deleted
    # every container-less image every ~10 s, observed 2026-10-06; see
    # tests/e2e/stages.py:hold_image, which does the same for snapshots).
    # `prune_stale` removes a holder 72 h after its creation once its tree
    # is not the current one; manual `prune` removes it immediately.
    [ "$no_docker" = 1 ] && return 0
    local name="omc-e2e-holder-base-${source_id}"
    if ! docker container inspect "$name" >/dev/null 2>&1; then
        docker run -d --name "$name" --label omc.e2e=holder \
            --label "omc.e2e.source=${source_id}" \
            --label "omc.e2e.created=$(date +%s)" "$image" sleep infinity >/dev/null
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
    # Read the whole listing: grep -q can SIGPIPE Docker under pipefail.
    if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -- "^omc-e2e-stage:.*-${source_id}\$" >/dev/null; then
        echo "golden path left no stage snapshots for ${source_id}" >&2
        return 1
    fi
}
run_rest() {
    uv run pytest -m "e2e and not golden and not expensive and not codex_gate" -q -n auto \
        --dist loadgroup -p no:cacheprovider "$@"
}
prune() {
    # Immediate cleanup of every other tree; automatic pruning above uses
    # a 72 h threshold for holders with a valid creation label.
    # Holders first: a running holder blocks even `rmi -f` of its image.
    docker ps -aq --filter label=omc.e2e=holder \
        | xargs docker inspect --format '{{.Id}} {{index .Config.Labels "omc.e2e.source"}}' 2>/dev/null \
        | awk -v id="${source_id}" '$2 != id {print $1}' | xargs docker rm -f >/dev/null 2>&1 || true
    docker images --format '{{.Repository}}:{{.Tag}}' \
        | grep -E '^omc-e2e(:|-stage:)' | grep -v -- "${source_id}\$" \
        | xargs docker rmi -f >/dev/null 2>&1 || true
    echo "kept images for ${source_id}; removed the rest" >&2
}
case "$mode" in
    golden) prune_stale; ensure_image; hold_image; sweep; run_golden "$@" ;;
    rest) prune_stale; ensure_image; hold_image; run_rest "$@" ;;
    all) prune_stale; ensure_image; hold_image; sweep; run_golden "$@"; run_rest "$@" ;;
    prune) prune ;;
    *) echo "usage: scripts/e2e.sh [golden|rest|all|prune] [pytest args]" >&2; exit 2 ;;
esac
