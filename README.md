# 本地 Grafana 股票 Dashboard

这是一套本地 Docker Compose 环境，包含：

- Grafana：预置股票行情 dashboard
- Postgres：保存股票价格时间序列
- Python collector：从 Yahoo Finance 拉取日线数据并写入 Postgres

## 启动

```bash
cd /Users/kaermax/stock-grafana-dashboard
docker compose up -d --build
```

打开 Grafana：

- 地址：http://localhost:3000
- Dashboard：http://localhost:3000/d/local-stock-market/local-stock-market-dashboard
- 本地匿名只读访问已开启，打开 dashboard 不需要登录

Dashboard 会自动出现在 `Stock / Local Stock Market Dashboard`。如果你创建了新的 Grafana volume，默认管理员账号仍是 `admin` / `admin`；已有 volume 里的管理员密码不会被 Compose 环境变量覆盖。

## 修改股票池

默认股票池：

```text
AAPL,MSFT,NVDA,TSLA,SPY,QQQ
```

临时指定股票池：

```bash
TICKERS="AAPL,MSFT,GOOGL,AMZN,META" docker compose up -d --build
```

也可以创建 `.env`：

```bash
TICKERS=AAPL,MSFT,NVDA,TSLA,SPY,QQQ
YAHOO_PERIOD=1y
YAHOO_INTERVAL=1d
REFRESH_SECONDS=3600
GRAFANA_PORT=3000
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=admin
```

## 验证数据链路

```bash
python3 scripts/smoke_check.py
npm run test:browser
```

`smoke_check.py` 会检查 Grafana 健康状态、Postgres datasource 查询和 dashboard provisioning 内容。浏览器测试会真实打开 dashboard，并把截图保存到 `test-results/stock-dashboard.png`。

## Flame Graph 与 Critical Path 实验

观测实验在原链路上增加：

- Tempo：保存 OpenTelemetry trace，用于看股票数据获取、分析、写库、Grafana 查询的调用路径
- Pyroscope：保存 Python CPU profile，用于看分析阶段的 flame graph
- Stock Observability Lab：Grafana 中的观测学习 dashboard

启动基础观测环境：

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d --build
```

运行一次端到端观测案例：

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml run --rm observability-case
npm run test:observability
python3 scripts/control_smoke.py
python3 scripts/observability_backend_check.py test-results/observability-case.json
python3 scripts/critical_path_report.py test-results/observability-case.json
```

也可以打开控制页手动触发真实刷新：

- 控制页：http://localhost:18080
- 点击 `Refresh Real Data` 后，会重新拉取真实 Yahoo Finance 数据、运行股票指标分析、写入 Postgres，并刷新 Grafana 中的 p50/p75/p95 延迟分析。
- 控制页会拒绝并发刷新；已有刷新运行中时，重复点击会返回当前任务状态。

输出文件：

- `test-results/observability-case.json`：单次实验的 trace id、阶段耗时、股票行数、指标行数和 Grafana 查询结果
- `test-results/critical-path-report.md`：关键路径报告
- `test-results/observability-dashboard.png`：浏览器实际渲染截图

`observability_backend_check.py` 会用 report 里的 trace id 验证 Tempo trace，并查询 Pyroscope flame graph，确认能看到 `fetch_prices.py run_once`、`analysis.py analyze_records` 和 `analysis.py _visible_cpu_analysis`。

打开 Grafana：

- 股票 dashboard：http://localhost:3000/d/local-stock-market/local-stock-market-dashboard
- 观测 dashboard：http://localhost:3000/d/stock-observability-lab/stock-observability-lab
- Tempo Explore：http://localhost:3000/explore?schemaVersion=1&panes=%7B%7D&orgId=1&left=%7B%22datasource%22:%22tempo%22%7D
- Pyroscope Explore：http://localhost:3000/explore?schemaVersion=1&panes=%7B%7D&orgId=1&left=%7B%22datasource%22:%22pyroscope%22%7D

如果 flame graph 不够明显，可以提高分析负载：

```bash
OBS_ANALYSIS_LOAD_FACTOR=30 docker compose -f docker-compose.yml -f docker-compose.observability.yml run --rm observability-case
```

`Stock Observability Lab` 的 `Latency Percentiles` 面板会按真实刷新 run 的 `critical_path_ms` 计算 p50、p75 和 p95。点击 p50/p75/p95 会切换 dashboard 变量，并显示该分位附近真实 runs 的聚合原因、阶段贡献、Tempo trace 链接和 Pyroscope 入口。

## LEAN 回测 Flame Graph 与 Critical Path 实验

`Lean Backtest Observability Lab` 以本地 `/Users/kaermax/lean-platform` 的真实 QuantConnect LEAN Docker 回测为案例。控制页会按参数准备真实行情数据、运行 `quantconnect/lean:latest`、解析回测结果、渲染报告、写入 Postgres，并把整条链路写入 Tempo 和 Pyroscope。

启动完整环境：

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d --build
```

打开控制页：

- Lean 控制页：http://localhost:18081
- Lean dashboard：http://localhost:3000/d/lean-backtest-observability-lab/lean-backtest-observability-lab

控制页支持运行时选择数据源：

- `local`：使用本地真实 LEAN daily zip，默认 `SPY`、`2013-01-01` 到 `2013-06-30`
- `yahoo`：从 Yahoo Finance 拉取日线并写入 LEAN daily zip
- `stooq`：从 Stooq 拉取日线并写入 LEAN daily zip
- `alpha_vantage`：从 Alpha Vantage 拉取日线，需要 API key

运行和验证：

```bash
python3 scripts/lean_control_smoke.py --run
python3 scripts/lean_observability_backend_check.py test-results/lean-observability-case.json
python3 scripts/lean_critical_path_report.py test-results/lean-observability-case.json
npm run test:lean-observability
```

输出文件：

- `test-results/lean-observability-case.json`：单次 LEAN 回测的 trace id、阶段耗时、数据源、统计结果和 Python runner profile query
- `test-results/lean-critical-path-report.md`：关键路径报告
- `test-results/lean-observability-dashboard.png`：浏览器实际渲染截图

当前本机实验不执行 LEAN 引擎容器内部 tracing/profiling，只记录 LEAN Docker 回测阶段耗时，并用 Python runner flame graph 展示数据准备、容器调度、结果解析和 Grafana 查询链路。

重要限制记录：当前 Mac Apple Silicon / Docker Desktop 的 LEAN 镜像是 `linux/arm64`。Alloy eBPF 日志会提示 `.NET tracer is currently not supported on ARM64`，Pyroscope .NET managed profiler 也会在 arm64 上退出为 unsupported architecture。因此这台机器不采集 LEAN 引擎内部 .NET 调用栈；若需要可读 .NET 引擎 flame graph，需要在 Linux amd64 环境或 amd64 LEAN 容器实验路径上运行。

## 常用命令

```bash
docker compose ps
docker compose logs -f collector
docker compose restart collector
docker compose down
```

清空所有本地数据：

```bash
docker compose down -v
```

## 说明

collector 使用 `yfinance` 拉取 Yahoo Finance 数据，不需要 API key。Yahoo Finance 偶尔会限流或返回空结果，collector 会保留已有数据并在下一轮继续重试。
