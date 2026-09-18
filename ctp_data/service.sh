#!/usr/bin/env sh
#
# 常驻调度器启动器（macOS / Linux）
#
# 与 start_collector.sh 的区别：那个只跑一个时段（跑到收盘就退出），这个长期活着，
# 按下一个开盘时刻反复拉起 start_collector 用的同一条采集命令。
#
# 用法：
#   sh ctp_data/service.sh                    # 常驻前台运行，Ctrl+C / SIGTERM 停止
#   sh ctp_data/service.sh --dry-run          # 只打印调度计划，不启动采集
#   COLLECTOR_SERVICE_ARGS='--lead 600' sh ctp_data/service.sh
#   PYTHON=/path/to/python sh ctp_data/service.sh
#
# 注意：请直接运行，不要 `source`。
#
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENV_FILE="$REPO_ROOT/.env"

log() { printf '%s [ctp_service] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
die() { log "错误：$*"; exit 2; }

# 采集子进程需要 .env 里的 CTP_* 账号；service.py 只负责调度，不读 .env。
if [ -f "$ENV_FILE" ]; then
    log "加载环境变量：$ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
else
    log "警告：$ENV_FILE 不存在，请确认 CTP_MD_FRONT / CTP_TD_FRONT / CTP_BROKER_ID / CTP_USER_ID / CTP_PASSWORD 已在当前环境中设置"
fi

# 仓库源码优先（与 start_collector.sh 一致）：否则会静默用到 site-packages 里的旧安装。
REPO_SRC="$REPO_ROOT/bt_api/bt_api_ctp/src"
if [ -d "$REPO_SRC/bt_api_ctp" ]; then
    PYTHONPATH="$REPO_SRC${PYTHONPATH:+:$PYTHONPATH}"
    export PYTHONPATH
fi

# 数值库线程池上限（与 start_collector.sh 一致，避免笔记本被拉满）。
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
export NUMEXPR_MAX_THREADS="${NUMEXPR_MAX_THREADS:-4}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-4}"

if [ -n "${PYTHON:-}" ]; then
    CANDIDATES="$PYTHON"
else
    CANDIDATES="python3 python $HOME/opt/anaconda3/bin/python $HOME/anaconda3/bin/python $HOME/miniconda3/bin/python"
fi

PYTHON_BIN=""
for candidate in $CANDIDATES; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import bt_api_ctp.collector.schedule' >/dev/null 2>&1; then
        PYTHON_BIN=$(command -v "$candidate")
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    die "找不到能 import bt_api_ctp 的 python。
       已尝试：$CANDIDATES
       请先安装（在仓库根目录执行 pip install -e .），或用 PYTHON=/path/to/python 指定"
fi

ARGS="${COLLECTOR_SERVICE_ARGS:-}"
log "解释器：$PYTHON_BIN"
log "配置：$SCRIPT_DIR/collector.yaml（可用 --config 覆盖）"
log "参数：$ARGS $*"
log "Ctrl+C / SIGTERM 停止：会先让当前采集优雅收尾再退出"

cd "$REPO_ROOT"
# ARGS 故意不加引号：它需要按空格拆成多个参数（见 COLLECTOR_SERVICE_ARGS 用法）。
# shellcheck disable=SC2086
exec "$PYTHON_BIN" "$SCRIPT_DIR/service.py" $ARGS "$@"
