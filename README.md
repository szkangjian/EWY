# STRC Data Acquisition & Monitoring

这是一个用于获取和监控 `STRC`（Strategy Variable Rate Series A Perpetual Stretch Preferred Stock）股票分钟级别数据的工具集。该项目包含从各大平台下载历史数据、补充当日数据以及通过 WebSocket 监控实时成交的功能。

## 包含文件

- `strc_data.py`: 多源数据获取和测试脚本（Yahoo, Polygon, Twelve Data, Alpha Vantage）。
- `download_strc_polygon.py`: 从 Polygon.io 批量下载 STRC 在过去几年（上市以来）的分钟级别 K 线数据，支持自动处理限速。
- `update_today.py`: 用于收盘后从 Yahoo Finance 下载当天分钟数据并且合并到本地历史数据集的脚本。
- `realtime_strc.py`: 通过 Finnhub 提供的免费 WebSocket 接口实时订阅 STRC 的逐笔交易，并在终端打印生成的 1 分钟实时 K 线。退出后自动将当天数据追加到本地数据集中。
- `strc_backtest.py`: 针对 STRC 特性（如面值锚定、增发天花板、历史除息日等因素）进行的区间交易及 Dividend Capture 策略回测脚手架。
- `config_example.py`: API 配置模板。

## 安装依赖

使用 [uv](https://github.com/astral-sh/uv) 安装依赖项：

```bash
uv pip install -r requirements.txt
```
或直接安装：
```bash
uv pip install requests pandas websocket-client
```

## 配置

请将 `config_example.py` 复制一份并重命名为 `config.py`，然后在其中填入你的 API Key：
- Polygon.io (处理历史数据下载)
- Finnhub.io (提供实时 WebSocket 数据订阅)
- Twelve Data / Alpha Vantage (可选备用)

```bash
cp config_example.py config.py
```

## 数据流管理

1. **获取历史基线**：
   运行 `uv run python download_strc_polygon.py` 下载上市至今的所有分钟 K 线。文件统一保存为 `strc_minute_data.csv`。
   
2. **盘中监控**：
   运行 `uv run python realtime_strc.py` 实时追踪分时走势和成交。退出(Ctrl+C)时自动追加 `strc_minute_data.csv`。

3. **收盘后更新**（可选，当未运行实时监控时）：
   运行 `uv run python update_today.py` 从 Yahoo Finance 补充当天数据并更新到由于没开监控没记录上的 `strc_minute_data.csv` 中。

## 策略研究文档 (Strategy & Research Documents)

本项目不仅包含数据管道，还沉淀了完整的关于 STRC 的机制拆解与量化策略文档。
文档按**由浅入深**的阅读顺序编号，位于 `docs/` 目录：

| # | 文件 | 内容概要 |
| - | ---- | -------- |
| 01 | [STRC 底层机制拆解](docs/01_strc_research.md) | 入门必读。$100 价格锚定原理、ATM 增发、股息调节机制、底层风险 |
| 02 | [关键因子深度分析](docs/02_strc_key_factors.md) | Bid-Ask Spread、除息日前后规律、股息率变更影响、BTC 闪崩事件 |
| 03 | [区间交易策略分析](docs/03_strc_strategy_analysis.md) | 基于分钟数据论证区间网格交易无效，Dividend Capture 胜率 100% |
| 04 | [**策略执行手册 (Playbook)**](docs/04_strc_strategy_playbook.md) | **核心纲要**。底仓 + Capture + 极端加仓三层引擎、风控铁律、执行清单 |
| 05 | [隔夜融资套利与压力测试](docs/05_strc_margin_arbitrage.md) | 进阶。IB Margin 套利数学模型、Alpha Stacking |
| 06 | [税务沙盘：Wash Sale 与 ROC](docs/06_strc_tax_wash_sale.md) | 美国居民专题。连环 Wash Sale、12 月斩链操作、Capital Loss 抵扣 |
| 07 | [跨境税务指南：非美居民 (NRA)](docs/07_strc_tax_nra.md) | 中国居民专题。零预扣、零资本利得税、W-8BEN、CRS 风险提示 |
| 08 | [执行追踪账本模版](docs/08_strc_execution_log_template.md) | 实操工具。息率追踪、Cost Basis 追踪、每月套利日志 |
