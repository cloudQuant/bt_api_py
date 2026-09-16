#!/usr/bin/env bash
# git_push_all.sh — 并行 push 主仓库与全部 submodule 的当前分支
#
# 用法:
#   ./scripts/git_push_all.sh                 # 默认 8 个并发进程
#   ./scripts/git_push_all.sh -j 4            # 4 个并发
#   JOBS=4 ./scripts/git_push_all.sh          # 等价于 -j 4
#   ./scripts/git_push_all.sh --dry-run       # 其余参数原样透传给 git push
#   ./scripts/git_push_all.sh --no-verify     # 例如绕过本机未安装的 git-lfs pre-push hook
#
# 行为:
#   - 仓库清单 = 主仓库 + .gitmodules 中全部 submodule（未初始化的自动跳过）
#   - 处于 detached HEAD 的仓库标记为 [SKIP]（不推；请先切到分支）
#   - 每个仓库执行 git push origin <当前分支>，额外参数透传
#   - 结束打印 OK/FAIL/SKIP 汇总；存在 FAIL 时退出码为 1
#   - 设置 GIT_ALL_REPOS_FILE=<file> 可显式指定仓库路径清单（每行一个，测试用）

set -uo pipefail

JOBS="${JOBS:-8}"
PASSTHROUGH=()
while [ $# -gt 0 ]; do
  case "$1" in
    -j|--jobs)
      JOBS="$2"; shift 2 ;;
    -j*) JOBS="${1#-j}"; shift ;;
    --jobs=*) JOBS="${1#*=}"; shift ;;
    *) PASSTHROUGH+=("$1"); shift ;;
  esac
done
case "$JOBS" in *[!0-9]*|'') echo "invalid jobs: $JOBS" >&2; exit 2 ;; esac
[ "$JOBS" -ge 1 ] || { echo "jobs must be >= 1" >&2; exit 2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 仓库清单：显式文件 > (主仓库 + .gitmodules)
declare -a REPOS=()
if [ -n "${GIT_ALL_REPOS_FILE:-}" ] && [ -f "$GIT_ALL_REPOS_FILE" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && [ -e "$ROOT/$line/.git" ] && REPOS+=("$ROOT/$line")
  done < "$GIT_ALL_REPOS_FILE"
else
  REPOS+=("$ROOT")
  if [ -f "$ROOT/.gitmodules" ]; then
    while read -r _key path; do
      [ -n "$path" ] && [ -e "$ROOT/$path/.git" ] && REPOS+=("$ROOT/$path")
    done < <(git config --file "$ROOT/.gitmodules" --get-regexp '^submodule\..*\.path$')
  fi
fi
[ "${#REPOS[@]}" -ge 1 ] || { echo "no repositories found" >&2; exit 2; }

# 网络健壮性：120 秒内低于 1KB/s 才断开，避免死连接拖住整批
export GIT_HTTP_LOW_SPEED_LIMIT=1000
export GIT_HTTP_LOW_SPEED_TIME=120

_label() { printf '%s' "${1#"$ROOT"/}"; }

run_one() {
  local repo="$1"; shift   # 其余参数透传给 git push
  local branch output
  branch=$(git -C "$repo" branch --show-current 2>/dev/null)
  if [ -z "$branch" ]; then
    printf '[SKIP] %s: detached HEAD\n' "$(_label "$repo")"
    return 0
  fi
  if output=$(git -C "$repo" push "$@" origin "$branch" 2>&1); then
    printf '[ OK ] %s (%s)\n' "$(_label "$repo")" "$branch"
  else
    printf '[FAIL] %s (%s)\n%s\n' "$(_label "$repo")" "$branch" \
      "$(printf '%s' "$output" | sed 's/^/    /')"
  fi
}
export -f run_one
export -f _label
export ROOT

LOGFILE=$(mktemp "${TMPDIR:-/tmp}/git_push_all.XXXXXX")

echo "== git push: ${#REPOS[@]} repos, $JOBS jobs =="
printf '%s\n' "${REPOS[@]}" \
  | xargs -P "$JOBS" -I{} bash -c 'run_one "$@"' _ {} "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}" \
  | tee "$LOGFILE"

ok=$(grep -c '^\[ OK \]' "$LOGFILE" || true)
fail=$(grep -c '^\[FAIL\]' "$LOGFILE" || true)
skip=$(grep -c '^\[SKIP\]' "$LOGFILE" || true)
rm -f "$LOGFILE"

echo "== summary: OK=$ok FAIL=$fail SKIP=$skip =="
[ "${fail:-0}" -eq 0 ]
