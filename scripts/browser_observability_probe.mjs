import { mkdir, readFile, writeFile } from "node:fs/promises";
import { chromium } from "playwright";

const baseUrl = process.env.GRAFANA_URL ?? "http://localhost:3000";
const reportPath = process.env.OBS_CASE_REPORT ?? "test-results/observability-case.json";
const stockDashboardUrl = `${baseUrl}/d/local-stock-market/local-stock-market-dashboard?from=now-1y&to=now`;
const observabilityDashboardUrl = `${baseUrl}/d/stock-observability-lab/stock-observability-lab?from=now-6h&to=now`;
const p75DashboardUrl = `${baseUrl}/d/stock-observability-lab/stock-observability-lab?from=now-6h&to=now&var-percentile=p75`;
const screenshotPath = "test-results/observability-dashboard.png";
const criticalStages = [
  "download_prices",
  "normalize_prices",
  "analysis",
  "store_prices",
  "store_indicators",
  "grafana_query",
  "browser_render",
];
const pipelineStages = criticalStages.filter((stage) => stage !== "browser_render");
const stageLabels = {
  download_prices: "Yahoo Finance download",
  normalize_prices: "per-symbol normalization",
  analysis: "technical indicator analysis",
  store_prices: "price upsert",
  store_indicators: "indicator upsert",
  grafana_query: "Grafana datasource query",
  browser_render: "Grafana browser render",
};
const reasonCodes = {
  download_prices: "market_data_source_latency",
  normalize_prices: "symbol_normalization_latency",
  analysis: "analysis_cpu_latency",
  store_prices: "price_storage_latency",
  store_indicators: "indicator_storage_latency",
  grafana_query: "grafana_datasource_latency",
  browser_render: "grafana_browser_render_latency",
};
const reasonText = {
  download_prices: "真实 Yahoo Finance 行情下载阶段占比最高，主要受行情源响应和网络耗时影响。",
  normalize_prices: "按股票代码拆分和标准化行情数据阶段占比最高，通常和 symbol 数量及返回数据形状有关。",
  analysis: "技术指标分析阶段占比最高，主要来自 MA、波动率、回撤和可见 CPU 负载计算。",
  store_prices: "价格数据写入 Postgres 阶段占比最高，通常和 upsert 行数、索引维护和数据库 I/O 有关。",
  store_indicators: "指标数据写入 Postgres 阶段占比最高，通常和指标行数、索引维护和数据库 I/O 有关。",
  grafana_query: "Grafana datasource 查询阶段占比最高，说明展示层 SQL 查询或 datasource 往返耗时是主因。",
  browser_render: "Grafana 浏览器渲染阶段占比最高，说明前端加载、面板查询和页面绘制是主因。",
};

async function launchBrowser() {
  const channel = process.env.PLAYWRIGHT_CHANNEL ?? "chrome";
  try {
    return await chromium.launch({ channel, headless: true });
  } catch (channelError) {
    try {
      return await chromium.launch({ headless: true });
    } catch (bundledError) {
      throw new Error(
        `Unable to launch Playwright browser. Channel '${channel}' failed with: ${channelError.message}\n` +
          `Bundled Chromium failed with: ${bundledError.message}`
      );
    }
  }
}

function nowMs() {
  return Number(performance.now().toFixed(3));
}

async function renderAndRead(page, url, title) {
  const started = nowMs();
  const response = await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
  if (!response || !response.ok()) {
    throw new Error(`${title} navigation failed: ${response?.status()} ${response?.statusText()}`);
  }
  await page.getByText(title).waitFor({ timeout: 30000 });
  await page.waitForTimeout(5000);
  const bodyText = await page.locator("body").innerText({ timeout: 10000 });
  return { bodyText, durationMs: Number((nowMs() - started).toFixed(3)) };
}

function measuredCriticalPathMs(report) {
  return Number(
    criticalStages
      .reduce((total, stage) => total + Number(report.stage_timings_ms?.[stage] ?? 0), 0)
      .toFixed(3)
  );
}

function measuredPipelineMs(report) {
  return Number(
    pipelineStages
      .reduce((total, stage) => total + Number(report.stage_timings_ms?.[stage] ?? 0), 0)
      .toFixed(3)
  );
}

function explainLatency(stageTimings) {
  const values = criticalStages
    .map((stage) => [stage, Number(stageTimings?.[stage] ?? 0)])
    .filter(([, duration]) => duration > 0);
  if (values.length === 0) {
    return {
      dominantStage: null,
      dominantStageMs: 0,
      dominantStageShare: 0,
      reasonCode: "no_latency_data",
      reasonSummary: "没有可用于解释的阶段耗时数据。",
    };
  }
  values.sort((a, b) => b[1] - a[1]);
  const total = values.reduce((sum, [, duration]) => sum + duration, 0);
  const [dominantStage, dominantStageMs] = values[0];
  const dominantStageShare = total > 0 ? dominantStageMs / total : 0;
  if (dominantStageShare >= 0.4) {
    return {
      dominantStage,
      dominantStageMs: Number(dominantStageMs.toFixed(3)),
      dominantStageShare: Number(dominantStageShare.toFixed(4)),
      reasonCode: reasonCodes[dominantStage],
      reasonSummary: reasonText[dominantStage],
    };
  }
  const topLabels = values.slice(0, 2).map(([stage]) => stageLabels[stage]).join(" 和 ");
  return {
    dominantStage,
    dominantStageMs: Number(dominantStageMs.toFixed(3)),
    dominantStageShare: Number(dominantStageShare.toFixed(4)),
    reasonCode: "mixed_path_latency",
    reasonSummary: `延迟不是由单一阶段主导，主要由 ${topLabels} 共同造成。`,
  };
}

function sqlString(value) {
  if (value === null || value === undefined) {
    return "NULL";
  }
  return `'${String(value).replaceAll("'", "''")}'`;
}

async function updateBrowserTimingInGrafana(report, browserRenderMs, latency) {
  const runId = report.run_id;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)) {
    throw new Error(`Invalid run_id in observability report: ${runId}`);
  }
  const duration = Number(browserRenderMs.toFixed(3));
  const rawSql = `
WITH updated AS (
  UPDATE observability_runs
  SET browser_render_ms = ${duration},
      pipeline_total_ms = ${latency.pipelineTotalMs},
      critical_path_ms = ${latency.criticalPathMs},
      total_ms = ${latency.criticalPathMs},
      dominant_stage = ${sqlString(latency.dominantStage)},
      reason_code = ${sqlString(latency.reasonCode)},
      reason_summary = ${sqlString(latency.reasonSummary)},
      details = jsonb_set(
        jsonb_set(details, '{stage_timings_ms,browser_render}', to_jsonb(${duration}::double precision), true),
        '{critical_path_ms}', to_jsonb(${latency.criticalPathMs}::double precision), true
      )
  WHERE run_id = '${runId}'::uuid
  RETURNING browser_render_ms, total_ms
)
SELECT browser_render_ms::double precision AS browser_render_ms, total_ms::double precision AS total_ms FROM updated;
`.trim();
  const payload = {
    queries: [
      {
        refId: "A",
        datasource: { uid: "stock-postgres", type: "postgres" },
        rawSql,
        format: "table",
        rawQuery: true,
        intervalMs: 1000,
        maxDataPoints: 1,
      },
    ],
    from: "now-1h",
    to: "now",
    range: { from: "now-1h", to: "now", raw: { from: "now-1h", to: "now" } },
  };
  const response = await fetch(`${baseUrl}/api/ds/query`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`Unable to update browser_render_ms through Grafana: HTTP ${response.status}: ${body}`);
  }
  const parsed = JSON.parse(body);
  const values = parsed?.results?.A?.frames?.[0]?.data?.values?.[0] ?? [];
  if (values.length !== 1) {
    throw new Error(`browser_render_ms update did not affect exactly one run: ${body}`);
  }
}

const report = JSON.parse(await readFile(reportPath, "utf-8"));
const browser = await launchBrowser();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const consoleErrors = [];
  page.on("pageerror", (error) => {
    throw error;
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  const totalStarted = nowMs();
  const stock = await renderAndRead(page, stockDashboardUrl, "Local Stock Market Dashboard");
  if (stock.bodyText.includes("No data")) {
    throw new Error("Stock dashboard still shows 'No data'");
  }
  const expectedSymbols = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"];
  const visibleSymbols = expectedSymbols.filter((symbol) => stock.bodyText.includes(symbol));
  if (visibleSymbols.length < 3) {
    throw new Error(`Expected stock symbols are not visible. Found: ${visibleSymbols.join(", ") || "none"}`);
  }

  const lab = await renderAndRead(page, observabilityDashboardUrl, "Stock Observability Lab");
  for (const requiredText of [
    "Latest Critical Path Total ms",
    "Latency Percentiles",
    "Selected Percentile Reason",
    "Selected Percentile Stage Contribution",
    "Stock Indicators",
  ]) {
    if (!lab.bodyText.includes(requiredText)) {
      throw new Error(`Observability dashboard missing expected text: ${requiredText}`);
    }
  }

  const queryErrors = consoleErrors.filter((message) =>
    message.includes("runRequest.catchError") ||
    message.includes("default database") ||
    message.includes("datasource")
  );
  if (queryErrors.length > 0) {
    throw new Error(`Dashboard console query errors:\n${queryErrors.join("\n---\n")}`);
  }

  const browserRenderMs = Number((nowMs() - totalStarted).toFixed(3));
  report.stage_timings_ms = report.stage_timings_ms ?? {};
  report.stage_timings_ms.browser_render = browserRenderMs;
  report.pipeline_total_ms = measuredPipelineMs(report);
  report.critical_path_ms = measuredCriticalPathMs(report);
  const latency = explainLatency(report.stage_timings_ms);
  latency.pipelineTotalMs = report.pipeline_total_ms;
  latency.criticalPathMs = report.critical_path_ms;
  report.total_ms = latency.criticalPathMs;
  report.dominant_stage = latency.dominantStage;
  report.dominant_stage_ms = latency.dominantStageMs;
  report.dominant_stage_share = latency.dominantStageShare;
  report.reason_code = latency.reasonCode;
  report.reason_summary = latency.reasonSummary;
  report.latency_explanation = {
    critical_path_ms: latency.criticalPathMs,
    pipeline_total_ms: latency.pipelineTotalMs,
    dominant_stage: latency.dominantStage,
    dominant_stage_ms: latency.dominantStageMs,
    dominant_stage_share: latency.dominantStageShare,
    reason_code: latency.reasonCode,
    reason_summary: latency.reasonSummary,
  };
  report.browser = {
    stock_dashboard_ms: stock.durationMs,
    observability_dashboard_ms: lab.durationMs,
    visible_symbols: visibleSymbols,
    screenshot: screenshotPath,
  };
  report.completed_at = new Date().toISOString();
  await updateBrowserTimingInGrafana(report, browserRenderMs, latency);
  report.browser.database_updated = true;

  const refreshedLab = await renderAndRead(page, observabilityDashboardUrl, "Stock Observability Lab");
  if (!refreshedLab.bodyText.includes("browser_render")) {
    throw new Error("Observability dashboard did not show the browser_render stage after database update");
  }
  const p75Lab = await renderAndRead(page, p75DashboardUrl, "Stock Observability Lab");
  if (!p75Lab.bodyText.includes("p75") || !p75Lab.bodyText.includes("Selected Percentile Reason")) {
    throw new Error("Observability dashboard did not render the p75 drilldown view");
  }
  await mkdir("test-results", { recursive: true });
  await page.screenshot({ path: screenshotPath, fullPage: true });

  await writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", "utf-8");

  console.log(`ok - Browser rendered stock and observability dashboards in ${browserRenderMs} ms`);
  console.log("ok - browser_render_ms updated in observability_runs");
  console.log(`ok - Screenshot saved to ${screenshotPath}`);
  console.log(`ok - Updated ${reportPath}`);
} finally {
  await browser.close();
}
