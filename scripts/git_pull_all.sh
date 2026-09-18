#!/usr/bin/env bash
# git_pull_all.sh — 并行 pull 主仓库与全部 submodule 的当前分支
#
# 用法:
#   ./scripts/git_pull_all.sh                 # 默认 8 个并发进程
#   ./scripts/git_pull_all.sh -j 4            # 4 个并发
#   JOBS=4 ./scripts/git_pull_all.sh          # 等价于 -j 4
#   ./scripts/git_pull_all.sh --rebase        # 其余参数原样透传给 git pull
#
# 行为:
#   - 仓库清单 = 主仓库 + .gitmodules 中全部 submodule（未初始化的自动跳过）
#   - 每个仓库执行 git pull --ff-only origin <当前分支>：
#       * 只做 fast-forward，绝不产生 merge commit / 意外改动工作区
#       * 本地有分叉提交时该仓库报 FAIL，由人工决定如何处理
#   - detached HEAD 的仓库默认 [SKIP] 并打印其 SHA；但如果该子模块在 .gitmodules 里
#     声明了 branch（submodule.<name>.branch），则先切到该分支再拉
#   - 结束打印 OK/FAIL/SKIP 汇总与 detached 的处理办法；存在 FAIL 时退出码为 1
#   - 设置 GIT_ALL_REPOS_FILE=<file> 可显式指定仓库路径清单（每行一个，测试用）
#
# 注意：detached HEAD 不是错误，而是子模块「被主仓库按提交固定」的正常状态
#       （clone --recursive / git submodule update 的结果）。想更新到主仓库记录的
#       那一版用 git submodule update --init --recursive；想让某个子模块跟随自己的
#       分支，就先 checkout 到该分支（或在 .gitmodules 里声明 branch）。
#
# 注意：pull 之后主仓库记录的 submodule 指针可能落后于各子仓库的新头提交，
#       如需同步指针，请在主仓库执行 git submodule update --init --recursive

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

export GIT_HTTP_LOW_SPEED_LIMIT=1000
export GIT_HTTP_LOW_SPEED_TIME=120

_label() { printf '%s' "${1#"$ROOT"/}"; }

# 返回该仓库在 .gitmodules 里声明的分支（submodule.<name>.branch），没有则输出空。
configured_branch() {
  local rel="${1#"$ROOT"/}" key path name
  [ -f "$ROOT/.gitmodules" ] || return 0
  while read -r key path; do
    [ "$path" = "$rel" ] || continue
    name="${key%.path}"
    git config --file "$ROOT/.gitmodules" --get "submodule.${name#submodule.}.branch" 2>/dev/null || true
    return 0
  done < <(git config --file "$ROOT/.gitmodules" --get-regexp '^submodule\..*\.path$')
}

run_one() {
  local repo="$1"; shift   # 其余参数透传给 git pull
  local branch output wanted sha note=""
  branch=$(git -C "$repo" branch --show-current 2>/dev/null)
  if [ -z "$branch" ]; then
    # detached HEAD：.gitmodules 声明了 branch 就切过去；没声明则报明原因与 SHA 后跳过。
    wanted=$(configured_branch "$repo")
    if [ -n "$wanted" ] && git -C "$repo" checkout --quiet "$wanted" >/dev/null 2>&1; then
      branch="$wanted"
      note="，已从 detached HEAD 切到 $wanted"
    else
      sha=$(git -C "$repo" rev-parse --short HEAD 2>/dev/null)
      printf '[SKIP] %s: detached HEAD (SHA %s，子模块按提交固定，属正常状态)\n' \
        "$(_label "$repo")" "${sha:-unknown}"
      return 0
    fi
  fi
  if output=$(git -C "$repo" pull --ff-only "$@" origin "$branch" 2>&1); then
    printf '[ OK ] %s (%s%s)\n' "$(_label "$repo")" "$branch" "$note"
  else
    printf '[FAIL] %s (%s%s)\n%s\n' "$(_label "$repo")" "$branch" "$note" \
      "$(printf '%s' "$output" | sed 's/^/    /')"
  fi
}
export -f run_one
export -f _label
export -f configured_branch
export ROOT

LOGFILE=$(mktemp "${TMPDIR:-/tmp}/git_pull_all.XXXXXX")

echo "== git pull --ff-only: ${#REPOS[@]} repos, $JOBS jobs =="
printf '%s\n' "${REPOS[@]}" \
  | xargs -P "$JOBS" -I{} bash -c 'run_one "$@"' _ {} "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}" \
  | tee "$LOGFILE"

ok=$(grep -c '^\[ OK \]' "$LOGFILE" || true)
fail=$(grep -c '^\[FAIL\]' "$LOGFILE" || true)
skip=$(grep -c '^\[SKIP\]' "$LOGFILE" || true)
rm -f "$LOGFILE"

echo "== summary: OK=$ok FAIL=$fail SKIP=$skip =="
if [ "${skip:-0}" -gt 0 ]; then
  cat <<'EOF'

== 关于 [SKIP] detached HEAD ==
detached HEAD 不是错误，而是子模块「被主仓库按提交固定」的正常状态
（git clone --recursive / git submodule update 的结果）。两种正确做法：

  1) 只想更新到主仓库记录的那一版（部署 / 消费方）：
         git submodule update --init --recursive

  2) 想让某个子模块跟随它自己的分支（本脚本下次才会拉它）：
         git -C <子模块路径> checkout <分支>
     或在 .gitmodules 里为该子模块声明 branch，本脚本会据此自动切换：
         git config -f .gitmodules submodule.<name>.branch dev

注意：git config submodule.recurse true / git pull --recurse-submodules 走的是
做法 1（按 SHA 更新，仍是 detached），与做法 2 的方向相反，不要混用。
EOF
fi
[ "${fail:-0}" -eq 0 ]
