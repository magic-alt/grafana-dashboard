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
