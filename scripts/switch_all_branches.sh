#!/usr/bin/env bash
# 将主仓库及所有已初始化的递归子仓库统一切换到 master 或 dev。
#
# 用法：
#   ./scripts/switch_all_branches.sh master
#   ./scripts/switch_all_branches.sh dev
#
# 安全约束：
#   * 只接受 master 和 dev，避免意外切到拼写错误的分支；
#   * 先检查所有工作树是否干净、所有 origin/<branch> 是否存在，再开始切换；
#   * 仅允许 fast-forward 同步，绝不重置、覆盖或创建 merge commit；
#   * 未初始化的子仓库会明确报错，避免出现“只切了部分仓库”。

set -euo pipefail

usage() {
    printf 'Usage: %s <master|dev>\n' "${0##*/}" >&2
    exit 2
}

TARGET="${1:-}"
case "$TARGET" in
    master|dev) ;;
    *) usage ;;
esac

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")/.." rev-parse --show-toplevel)"
MODULES_FILE="$(mktemp "${TMPDIR:-/tmp}/switch_all_branches.XXXXXX")"
trap 'rm -f "$MODULES_FILE"' EXIT

git -C "$ROOT" submodule foreach --recursive --quiet \
    'printf "%s\n" "$displaypath"' >"$MODULES_FILE"

declare -a REPOS=("$ROOT")
while IFS= read -r relative_path; do
    [ -n "$relative_path" ] || continue
    if [ ! -e "$ROOT/$relative_path/.git" ]; then
        printf 'ERROR: submodule is not initialized: %s\n' "$relative_path" >&2
        printf 'Run: git submodule update --init --recursive\n' >&2
        exit 1
    fi
    REPOS+=("$ROOT/$relative_path")
done <"$MODULES_FILE"

label() {
    if [ "$1" = "$ROOT" ]; then
        printf 'root'
    else
        printf '%s' "${1#"$ROOT"/}"
    fi
}

check_clean() {
    local repo="$1" status
    if [ "$repo" = "$ROOT" ]; then
        status="$(git -C "$repo" status --porcelain --ignore-submodules=dirty)"
    else
        status="$(git -C "$repo" status --porcelain)"
    fi
    if [ -n "$status" ]; then
        printf 'ERROR: %s has uncommitted changes; no branch was switched.\n' "$(label "$repo")" >&2
        printf '%s\n' "$status" | sed 's/^/  /' >&2
        return 1
    fi
}

echo "== Preflight for '$TARGET' across ${#REPOS[@]} repositories =="
for repo in "${REPOS[@]}"; do
    check_clean "$repo"
done

for repo in "${REPOS[@]}"; do
    git -C "$repo" remote get-url origin >/dev/null
    git -C "$repo" fetch --prune origin
    if ! git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$TARGET"; then
        printf 'ERROR: %s has no origin/%s branch; no branch was switched.\n' \
            "$(label "$repo")" "$TARGET" >&2
        exit 1
    fi
done

switch_and_update() {
    local repo="$1"
    if git -C "$repo" show-ref --verify --quiet "refs/heads/$TARGET"; then
        git -C "$repo" switch "$TARGET"
    else
        git -C "$repo" switch --track -c "$TARGET" "origin/$TARGET"
    fi
    git -C "$repo" merge --ff-only "origin/$TARGET"
    printf '[ OK ] %s -> %s\n' "$(label "$repo")" "$TARGET"
}

echo "== Switching all repositories to '$TARGET' =="
for repo in "${REPOS[@]}"; do
    switch_and_update "$repo"
done

echo "== Complete: ${#REPOS[@]} repositories are on '$TARGET' =="
