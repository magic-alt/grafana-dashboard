import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const baseUrl = process.env.GRAFANA_URL ?? "http://localhost:3000";
const dashboardUrl = `${baseUrl}/d/local-stock-market/local-stock-market-dashboard?from=now-1y&to=now`;
const screenshotPath = "test-results/stock-dashboard.png";

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

  const response = await page.goto(dashboardUrl, { waitUntil: "networkidle", timeout: 60000 });
  if (!response || !response.ok()) {
    throw new Error(`Dashboard navigation failed: ${response?.status()} ${response?.statusText()}`);
  }

  await page.getByText("Local Stock Market Dashboard").waitFor({ timeout: 30000 });
  await page.waitForTimeout(5000);

  const bodyText = await page.locator("body").innerText({ timeout: 10000 });
  await mkdir("test-results", { recursive: true });
  await page.screenshot({ path: screenshotPath, fullPage: true });

  const queryErrors = consoleErrors.filter((message) =>
    message.includes("runRequest.catchError") ||
    message.includes("SceneVariableSet") ||
    message.includes("default database") ||
    message.includes("datasource")
  );
  if (queryErrors.length > 0) {
    throw new Error(`Dashboard console query errors:\n${queryErrors.join("\n---\n")}`);
  }

  if (bodyText.includes("No data")) {
    throw new Error("Dashboard still shows 'No data'");
  }

  const expectedSymbols = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"];
  const visibleSymbols = expectedSymbols.filter((symbol) => bodyText.includes(symbol));
  if (visibleSymbols.length < 3) {
    throw new Error(`Expected stock symbols are not visible. Found: ${visibleSymbols.join(", ") || "none"}`);
  }

  const expectedPanels = ["Close Price", "Latest Close", "Latest Change %", "Volume", "Daily Return %", "Latest Summary"];
  const missingPanels = expectedPanels.filter((title) => !bodyText.includes(title));
  if (missingPanels.length > 0) {
    throw new Error(`Missing dashboard panels: ${missingPanels.join(", ")}`);
  }

  console.log(`ok - Dashboard rendered with symbols: ${visibleSymbols.join(", ")}`);
  console.log(`ok - Screenshot saved to ${screenshotPath}`);
} finally {
  await browser.close();
}
