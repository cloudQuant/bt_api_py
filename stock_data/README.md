# stock_data —— A 股历史数据目录

本目录存放由 **backtrader_web**（迭代 198/199 同花顺数据接入）导出的 A 股历史数据，
组织方式为**一标一份 Parquet**，便于按标的读取做回测/研究。

数据文件**不入版本库**（见本节末尾的 `.gitignore` 规则）。

## 目录结构

```
stock_data/
├── README.md                                  # 本文档（入库）
├── trading_calendar.parquet                   # 交易日历（全局一份）
├── daily/
│   └── <thscode>.parquet                      # 日线 OHLCV（约 10 年）
├── adjustment_factors/
│   └── <thscode>.parquet                      # 除权因子事件流
└── financials/
    ├── income/<thscode>.parquet               # 利润表（多期）
    ├── balance/<thscode>.parquet              # 资产负债表（多期）
    └── cashflow/<thscode>.parquet             # 现金流量表（多期）
```

`<thscode>` 为带交易所后缀的完整代码，例如 `600519.SH`、`000001.SZ`、`830799.BJ`。

## 文件格式（Parquet）

| 数据集 | 列 |
|---|---|
| `daily` | `thscode`(str), `date`(date32), `open/high/low/close/volume/turnover`(float64) |
| `adjustment_factors` | `thscode`(str), `ex_date`(date32), `dividend_per_share`(float64), `per_share_bonus`(float64) |
| `financials/*` | `period_end`(date32), `fiscal_year`, `fiscal_period`, 报表金额字段（未披露为 null） |
| `trading_calendar` | `date`(date32), `date_ms`(int64) |

说明：
- `date` / `ex_date` / `period_end` 均为**交易日/事件日**的日期（已按 `Asia/Shanghai` 归一化）。
- 日线为**未复权**原始价；复权请结合 `adjustment_factors` 自行计算（或使用 THS 的 `adjust` 接口）。
- 财务 `null` 表示「该期未披露」，未做补零。

## 数据来源与更新

- **数据源**：同花顺金融数据 API（THS，`fuyao.aicubes.cn`）
- **导出脚本**：`backtrader_web/src/backend/scripts/export_ths_to_stock_data.py`
- **回填 / 每日增量**（在 backtrader_web 仓库执行）：
  ```bash
  # 全量回填（断点续跑，可重复执行）
  python scripts/backfill_ths_history.py --dataset daily-k --limit 400 \
      --dump-file "$TMPDIR/ths_daily_k_full.parquet" --apply
  python scripts/backfill_ths_history.py --dataset adjustment-factors --limit 250 --apply
  python scripts/backfill_ths_history.py --dataset financials-income --limit 250 --apply
  # 每日增量
  python scripts/collect_ths_daily.py --apply
  # 导出到本目录
  python scripts/export_ths_to_stock_data.py --target-dir <本目录>
  ```

## 不入库

`stock_data` 下的数据文件全部由 `.gitignore` 排除，版本库只保留本 README。
