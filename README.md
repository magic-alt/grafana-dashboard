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

启动完整环境：

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d --build
```

运行一次端到端观测案例：

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml run --rm observability-case
npm run test:observability
python3 scripts/observability_backend_check.py test-results/observability-case.json
python3 scripts/critical_path_report.py test-results/observability-case.json
```

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
