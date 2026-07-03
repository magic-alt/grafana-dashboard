import { chromium } from "playwright";

const baseUrl = process.env.GRAFANA_URL ?? "http://localhost:3000";
const dashboardUrl = `${baseUrl}/d/lean-backtest-observability-lab/lean-backtest-observability-lab?from=now-6h&to=now`;
const p75DashboardUrl = `${dashboardUrl}&var-percentile=p75`;
const screenshotPath = "test-results/lean-observability-dashboard.png";

async function renderAndRead(page, url, expectedTitle) {
  const started = Date.now();
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 60000 }).catch(() => {});
  await page.getByText(expectedTitle).first().waitFor({ timeout: 60000 });
  await page.waitForTimeout(4000);
  let bodyText = await page.locator("body").innerText({ timeout: 30000 });
  for (const position of [0.35, 0.7, 1]) {
    await page.evaluate((ratio) => window.scrollTo(0, document.body.scrollHeight * ratio), position);
    await page.waitForTimeout(1200);
    bodyText += "\n" + await page.locator("body").innerText({ timeout: 30000 });
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  return { bodyText, durationMs: Date.now() - started };
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1600 } });
try {
  const lab = await renderAndRead(page, dashboardUrl, "Lean Backtest Observability Lab");
  for (const requiredText of [
    "Latest Lean Critical Path ms",
    "Latency Percentiles",
    "Selected Percentile Reason",
    "Recent Lean Backtest Runs",
    "LEAN Backtest Statistics",
  ]) {
    if (!lab.bodyText.includes(requiredText)) {
      throw new Error(`Lean observability dashboard missing expected text: ${requiredText}`);
    }
  }
  if (lab.bodyText.includes("No data")) {
    throw new Error("Lean observability dashboard still contains No data after a completed run");
  }
  await page.screenshot({ path: screenshotPath, fullPage: true });

  const p75Lab = await renderAndRead(page, p75DashboardUrl, "Lean Backtest Observability Lab");
  if (!p75Lab.bodyText.includes("p75") || !p75Lab.bodyText.includes("Selected Percentile Reason")) {
    throw new Error("Lean observability dashboard did not render the p75 drilldown view");
  }
  console.log(`ok - Browser rendered Lean observability dashboard in ${lab.durationMs} ms`);
  console.log(`ok - screenshot saved to ${screenshotPath}`);
} finally {
  await browser.close();
}
