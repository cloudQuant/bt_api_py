#!/usr/bin/env sh
#
# 一键安装 bt_api_py 及其全部子模块包 —— Unix 薄启动器
#
# 真正的安装逻辑在 scripts/install_bt_api_submodules.py（跨平台）。
# 本脚本只负责：挑一个 Python 3.11+ 解释器 → 用默认参数调安装器 → 跑 doctor 自检。
#
# 默认行为（核心 + 全部 15 个子模块，源码优先、editable、已装的也重装）：
#   python scripts/install_bt_api_submodules.py --with-root --editable --editable-root --upgrade
#   * editable：import 直接指向仓库源码，避免 site-packages 里的旧快照（曾导致改了代码却不生效）
#   * --upgrade：不加它安装器会认为"已装"而跳过
#   * 缺失的子模块源码会被自动 git submodule update --init（可用 --skip-submodule-update 关掉）
#
# 用法：
#   sh scripts/install_all.sh                    # 核心 + 全部子模块
#   sh scripts/install_all.sh ctp                # 只装子集：位置参数 = 包子集（可多个）
#   sh scripts/install_all.sh --strategy none    # 只体检当前安装状态，什么都不装
#   sh scripts/install_all.sh --dry-run          # 只打印将要执行的 pip 命令
#   PYTHON=/path/to/python sh scripts/install_all.sh
#
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
INSTALLER="$SCRIPT_DIR/install_bt_api_submodules.py"
DEFAULT_ARGS="--with-root --editable --editable-root --upgrade"

log() { printf '%s [install_all] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
die() { log "错误：$*"; exit 2; }

[ -f "$INSTALLER" ] || die "找不到安装器：$INSTALLER"

# 挑解释器：安装器用 tomllib，因此必须是 Python 3.11+（与 pyproject requires-python 一致）。
if [ -n "${PYTHON:-}" ]; then
    CANDIDATES="$PYTHON"
else
    CANDIDATES="python3 python $HOME/opt/anaconda3/bin/python $HOME/anaconda3/bin/python $HOME/miniconda3/bin/python"
fi

PYTHON_BIN=""
for candidate in $CANDIDATES; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import tomllib' >/dev/null 2>&1; then
        PYTHON_BIN=$(command -v "$candidate")
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    die "找不到 Python 3.11+ 解释器（安装器需要 tomllib）。
       已尝试：$CANDIDATES
       请安装 Python 3.11+，或用 PYTHON=/path/to/python 指定"
fi

ARGS="${INSTALL_ALL_ARGS:-$DEFAULT_ARGS}"
log "解释器：$PYTHON_BIN（$("$PYTHON_BIN" -V 2>&1)）"
log "安装参数：$ARGS $*"
log "安装目录：$REPO_ROOT"
echo

cd "$REPO_ROOT"
set +e
# ARGS 不加引号：它要按空格拆成多个参数（见 INSTALL_ALL_ARGS 用法）。
# shellcheck disable=SC2086
"$PYTHON_BIN" "$INSTALLER" $ARGS "$@"
rc=$?
set -e

echo
log "安装结果自检（python -m bt_api_py.doctor --bundle core-reference）："
"$PYTHON_BIN" -m bt_api_py.doctor --bundle core-reference || true

if [ "$rc" -ne 0 ]; then
    log "安装器返回非 0（$rc），请检查上面的输出"
fi
exit "$rc"
