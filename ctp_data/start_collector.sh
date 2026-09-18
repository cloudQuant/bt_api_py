#!/usr/bin/env bash
#
# 迭代04 CTP 全市场 tick 采集 —— 启动脚本
#
# 行为：前台运行到本时段收盘（相当于 --until-close --wait-open）。
#   * 自动加载仓库根目录的 .env（collector 自己不读 .env）
#   * 优先使用仓库里的 bt_api_ctp 源码，避免静默命中 site-packages 里的旧安装
#   * 自动挑选能 import bt_api_ctp 的 python 解释器
#   * Ctrl+C 触发优雅停机：停订阅 -> flush -> finalize 写 report.json
#
# 用法：
#   sh ctp_data/start_collector.sh                    # 跑到收盘
#   sh ctp_data/start_collector.sh -v                 # 额外参数原样透传
#   PYTHON=/path/to/python sh ctp_data/start_collector.sh
#   COLLECTOR_ARGS="--once --duration 60" sh ctp_data/start_collector.sh
#
# 注意：请直接运行，不要 `source`（脚本用 $0 定位自己，source 时 $0 是调用者的）。
#
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
CONFIG="$SCRIPT_DIR/collector.yaml"
ENV_FILE="$REPO_ROOT/.env"
DEFAULT_ARGS="--until-close --wait-open"

log() { printf '%s [start_collector] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
die() { log "错误：$*"; exit 2; }

if [ ! -f "$CONFIG" ]; then
    die "找不到配置 $CONFIG
       请先复制模板：cp \"$SCRIPT_DIR/collector.example.yaml\" \"$CONFIG\"，并修改其中的 data_root"
fi

if [ -f "$ENV_FILE" ]; then
    log "加载环境变量：$ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
else
    log "警告：$ENV_FILE 不存在，请确认 CTP_MD_FRONT / CTP_TD_FRONT / CTP_BROKER_ID / CTP_USER_ID / CTP_PASSWORD 已在当前环境中设置"
fi

# 限制第三方数值库的线程池大小（默认等于 CPU 核数，8 核机器上会一次拉起 8 个
# 计算线程）。采集本身是单进程：1 个主线程 + tick-compact + ctp-resubscribe
# 两个工作线程 + CTP 原生线程；这里限制的是 pyarrow / numexpr 的并行度。
# 想要更多并行（例如服务器上加快收盘压缩）就在 .env 或环境变量里显式设置。
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"
export NUMEXPR_MAX_THREADS="${NUMEXPR_MAX_THREADS:-4}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-4}"

# 仓库根目录下没有 bt_api_ctp 这个包目录（源码在 bt_api/bt_api_ctp/src/），
# 所以不显式指定 PYTHONPATH 时，`python -m bt_api_ctp.collector` 会静默命中
# site-packages 里可能已经过时的安装版本 —— 改了插件代码却跑了旧版本。
# 源码目录存在就放到 PYTHONPATH 最前（优先于 site-packages）；纯部署环境
# （只有已安装包、没有源码）则自动回退到已安装版本。
REPO_SRC="$REPO_ROOT/bt_api/bt_api_ctp/src"
if [ -d "$REPO_SRC/bt_api_ctp" ]; then
    PYTHONPATH="$REPO_SRC${PYTHONPATH:+:$PYTHONPATH}"
    export PYTHONPATH
fi

# 挑一个能 import bt_api_ctp 的解释器：PATH 上的 python 往往不是装了依赖的那个。
if [ -n "${PYTHON:-}" ]; then
    CANDIDATES="$PYTHON"
else
    CANDIDATES="python3 python $HOME/opt/anaconda3/bin/python $HOME/anaconda3/bin/python $HOME/miniconda3/bin/python"
fi

PYTHON_BIN=""
for candidate in $CANDIDATES; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import bt_api_ctp.collector' >/dev/null 2>&1; then
        PYTHON_BIN=$(command -v "$candidate")
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    die "找不到能 import bt_api_ctp 的 python。
       已尝试：$CANDIDATES
       请先安装（在仓库根目录执行 pip install -e .），或用 PYTHON=/path/to/python 指定"
fi

ARGS="${COLLECTOR_ARGS:-$DEFAULT_ARGS}"
log "解释器：$PYTHON_BIN"
log "配置：$CONFIG"
log "线程上限：OMP_NUM_THREADS=$OMP_NUM_THREADS NUMEXPR_MAX_THREADS=$NUMEXPR_MAX_THREADS"

# 打印实际使用的 bt_api_ctp 位置：版本漂移只有在这里看得见。
MODULE_FILE=$("$PYTHON_BIN" -c 'import bt_api_ctp; print(bt_api_ctp.__file__)' 2>/dev/null || true)
if [ -n "$MODULE_FILE" ]; then
    log "bt_api_ctp：$MODULE_FILE"
    if [ -d "$REPO_SRC/bt_api_ctp" ]; then
        case "$MODULE_FILE" in
            "$REPO_SRC"/*) ;;
            *) log "警告：已把 $REPO_SRC 放到 PYTHONPATH 最前，但实际导入的不是它，请检查环境" ;;
        esac
    else
        log "提示：仓库内未找到 $REPO_SRC，使用已安装版本；改过插件代码需重新安装才会生效"
    fi
fi

# 打印 data_root 解析后的绝对路径。配置里写成相对路径时相对仓库根，
# 而脚本下面会 cd 到仓库根，所以这里算出来的就是数据真正落盘的位置。
DATA_ROOT=$(awk -F':[[:space:]]*' '/^data_root:/{print $2; exit}' "$CONFIG" 2>/dev/null || true)
if [ -n "$DATA_ROOT" ]; then
    case "$DATA_ROOT" in
        /*) : ;;
        *) DATA_ROOT="$REPO_ROOT/$DATA_ROOT" ;;
    esac
    log "数据根目录：$DATA_ROOT"
fi

log "参数：$ARGS $*"
log "按 Ctrl+C 优雅停机（不要用 kill -9，会丢失未压缩的数据）"

cd "$REPO_ROOT"
# ARGS 故意不加引号：它需要按空格拆成多个参数（见 COLLECTOR_ARGS 用法）。
# shellcheck disable=SC2086
exec "$PYTHON_BIN" -m bt_api_ctp.collector --config "$CONFIG" $ARGS "$@"
