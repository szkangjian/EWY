# EWY 量化交易研究

iShares MSCI South Korea ETF (`EWY`) 的量化分析与交易策略研究项目。

基于 `194,125` 条分钟数据、`507` 个交易日（`2024-03-25` 至 `2026-04-01`），所有数据统一为 `US/Eastern` 时区、仅使用常规交易时段 `09:30-16:00 ET`。

## 核心结论

- **IBS 是最强信号**：`29` 笔交易，胜率 `69%`，总收益 `+31.1%`，均收益 `+1.07%/笔`，平均持有 `2.5` 交易日。
- **盘中跌幅反弹适合做辅助策略**：当前样本内最优参数为 `-4.5% / +2.5% / 5天`，`11` 笔、胜率 `82%`、总收益 `+16.6%`，但样本较少，实盘应保守看待。
- **MA200 是风控开关**：加上 MA200 过滤后，跌幅策略表现显著改善，说明反弹信号对趋势环境敏感。
- **反弹效应依赖半导体/AI 强势 regime**：EWY 在强势周期中确实存在短线均值回归，但不能假设”什么原因跌都会反弹”。

所有研究脚本统一依赖 [`ewy_market_data.py`](ewy_market_data.py) 处理时区与交易时段归一化。

## 策略研究文档

文档按阅读顺序编号，位于 `docs/` 目录：

| # | 文件 | 内容概要 |
| - | ---- | -------- |
| 01 | [EWY 底层研究](docs/01_ewy_research.md) | 价格历史、Spread、波动率、极端事件、股息、相关性分析 |
| 02 | [外部事件驱动分析](docs/02_ewy_event_drivers.md) | 2025-2026 异动日逐一溯源，分类为半导体/AI、地缘政治等 |
| 03 | [交易策略研究](docs/03_ewy_strategy_research.md) | IBS、盘中跌幅触发策略回测，参数扫描，过滤器测试 |
| 04 | [税务分析](docs/04_ewy_tax_analysis.md) | EWY 交易策略的税务框架，需结合个人身份单独核对 |
| 05 | [策略操作手册](docs/05_ewy_strategy_playbook.md) | 执行框架：IBS 主策略 + 盘中跌幅辅助策略 |
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
