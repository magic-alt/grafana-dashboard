import { mkdir, readFile, writeFile } from "node:fs/promises";
import { chromium } from "playwright";

const baseUrl = process.env.GRAFANA_URL ?? "http://localhost:3000";
const reportPath = process.env.OBS_CASE_REPORT ?? "test-results/observability-case.json";
const stockDashboardUrl = `${baseUrl}/d/local-stock-market/local-stock-market-dashboard?from=now-1y&to=now`;
const observabilityDashboardUrl = `${baseUrl}/d/stock-observability-lab/stock-observability-lab?from=now-6h&to=now`;
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

async function updateBrowserTimingInGrafana(report, browserRenderMs, criticalPathMs) {
  const runId = report.run_id;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)) {
    throw new Error(`Invalid run_id in observability report: ${runId}`);
  }
  const duration = Number(browserRenderMs.toFixed(3));
  const rawSql = `
WITH updated AS (
  UPDATE observability_runs
  SET browser_render_ms = ${duration},
      total_ms = ${criticalPathMs},
      details = jsonb_set(
        jsonb_set(details, '{stage_timings_ms,browser_render}', to_jsonb(${duration}::double precision), true),
        '{critical_path_ms}', to_jsonb(${criticalPathMs}::double precision), true
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
  for (const requiredText of ["Critical Path Total ms", "Stage Duration", "Stock Indicators"]) {
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
  report.pipeline_total_ms = report.pipeline_total_ms ?? report.total_ms;
  report.critical_path_ms = measuredCriticalPathMs(report);
  report.total_ms = report.critical_path_ms;
  report.browser = {
    stock_dashboard_ms: stock.durationMs,
    observability_dashboard_ms: lab.durationMs,
    visible_symbols: visibleSymbols,
    screenshot: screenshotPath,
  };
  report.completed_at = new Date().toISOString();
  await updateBrowserTimingInGrafana(report, browserRenderMs, report.critical_path_ms);
  report.browser.database_updated = true;

  const refreshedLab = await renderAndRead(page, observabilityDashboardUrl, "Stock Observability Lab");
  if (!refreshedLab.bodyText.includes("browser_render")) {
    throw new Error("Observability dashboard did not show the browser_render stage after database update");
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
