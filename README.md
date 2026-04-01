# EWY 量化交易研究

iShares MSCI South Korea ETF (`EWY`) 的量化分析与交易策略研究项目。

仓库保留原始分钟数据，同时在回测时统一做两件事：
- 全部时间戳归一到 `US/Eastern`
- 只使用常规交易时段 `09:30-16:00 ET`

当前仓库内的原始分钟数据共有 `219,296` 行；按上述口径清洗后，用于策略研究的样本为 `191,397` 条分钟数据、`500` 个交易日（`2024-03-25` 到 `2026-03-23`）。

## 当前结论

- **IBS 仍然是最强信号**：`28` 笔交易，胜率 `71%`，总收益 `+33.3%`，均收益 `+1.19%/笔`，平均持有 `3.3` 天。
- **旧版跌幅策略被明显高估**：原先常用的 `-3% / +2.5% / 3天` 在修正口径后只剩 `30` 笔、胜率 `63%`、总收益 `+1.0%`。
- **盘中跌幅策略仍有研究价值，但只适合当辅助策略**：当前样本内较优参数是 `-3.5% / +2.0% / 5天`，`21` 笔、胜率 `76%`、总收益 `+18.3%`，但有明显样本内优化风险。
- **MA200 更像风控开关，不是 alpha 本身**：对盘中跌幅策略，加上 MA200 过滤后表现显著改善。
- **不能再下“与事件类型无关”的强结论**：修正后更合理的结论是，EWY 在半导体/AI 强势 regime 中确实存在短线反弹现象，但强度没有旧版文档写得那么夸张。

## 方法说明

这次版本最重要的修正，不是换参数，而是修正数据口径：

- 旧版研究把 `Polygon` 导出的 naive UTC 时间戳直接按自然日聚合，导致部分盘后数据被归到第二天。
- `Yahoo` 更新脚本又按美东时间写入，和历史数据口径不一致。
- 这会直接影响日线 `Open/High/Low/Close`、`IBS` 计算、以及“相对前日收盘跌幅”的边界判断。

现在仓库里的主要研究脚本都统一依赖 [`ewy_market_data.py`](ewy_market_data.py) 处理时区和交易时段。

## 策略研究文档

文档按阅读顺序编号，位于 `docs/` 目录：

| # | 文件 | 内容概要 |
| - | ---- | -------- |
| 01 | [EWY 底层研究](docs/01_ewy_research.md) | 价格历史、Spread、波动率、极端事件、股息、相关性分析 |
| 02 | [外部事件驱动分析](docs/02_ewy_event_drivers.md) | 2025-2026 异动日逐一溯源，分类为半导体/AI、地缘政治等 |
| 03 | [交易策略研究](docs/03_ewy_strategy_research.md) | 修正时区与常规交易时段后的策略回测、参数扫描、过滤器测试 |
| 04 | [税务分析](docs/04_ewy_tax_analysis.md) | EWY 交易策略的税务框架，需结合个人身份单独核对 |
| 05 | [策略操作手册](docs/05_ewy_strategy_playbook.md) | 当前推荐执行框架：IBS 主策略 + 盘中跌幅观察型辅助策略 |
| 06 | [执行追踪日志模板](docs/06_ewy_execution_log.md) | 交易记录、月度汇总、风控指标追踪模板 |

## 数据管道

### 获取历史数据

```bash
python download_ewy_polygon.py
```

### 每日更新

```bash
python update_ewy_today.py
```

### 盘中实时监控

```bash
python realtime_ewy.py
```

## 策略工具

### 每日信号

```bash
python ewy_signal.py
```

### 月度风控报告

```bash
python ewy_risk_monitor.py
```

## 配置

将 `config.py` 填入 API Key：
- `Polygon.io`：历史分钟数据下载
- `Finnhub.io`：实时 WebSocket 数据

## 安装依赖

```bash
uv pip install yfinance pandas requests websocket-client numpy python-dateutil
```

## 项目结构

```text
EWY/
├── docs/                           # 研究文档
├── ewy_market_data.py             # 时区 / 交易时段归一化
├── download_ewy_polygon.py        # Polygon 历史数据下载
├── update_ewy_today.py            # Yahoo Finance 每日更新
├── realtime_ewy.py                # Finnhub 实时监控 + 策略提醒
├── ewy_signal.py                  # 每日 IBS / 跌幅信号生成
├── ewy_strategy.py                # qbot 策略入口
├── ewy_intraday_monitor.py        # qbot 盘中监控入口
├── ewy_orchestrator.py            # qbot 调度入口
├── ewy_risk_monitor.py            # 月度风控指标报告
├── ewy_backtest.py                # 因子分析回测
├── ewy_intraday_backtest.py       # 盘中跌幅策略参数扫描
├── ewy_filter_backtest.py         # 过滤器对比测试
├── backtest_mean_reversion.py     # 日线均值回归回测
├── backtest_mean_reversion_intraday.py # 分钟级均值回归回测
├── ewy_minute_data.csv            # EWY 原始分钟级 OHLCV
├── strc_minute_data.csv           # STRC 对照数据
└── strf_minute_data.csv           # STRF 对照数据
```
