import { chromium } from "playwright-core";
import { globSync } from "node:fs";
const OUT = process.argv[2] || "./shots/";
const browser = await chromium.launch({
  executablePath: globSync(`${process.env.HOME}/.cache/ms-playwright/chromium-*/chrome-linux*/chrome`)[0],
  args: ["--no-sandbox"],
});
const pages = [["/", "record"], ["/replay", "replay"], ["/consent", "consent"], ["/share", "share"], ["/history", "history"], ["/refusals", "refusals"], ["/settings", "settings"]];
for (const [path, name] of pages) {
  const tab = await browser.newPage({ viewport: { width: 1000, height: 1000 }, deviceScaleFactor: 2 });
  await tab.goto("http://127.0.0.1:8412" + path, { waitUntil: "networkidle" });
  await tab.screenshot({ path: `${OUT}${name}.png`, fullPage: true });
  const m = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await m.goto("http://127.0.0.1:8412" + path, { waitUntil: "networkidle" });
  await m.screenshot({ path: `${OUT}${name}-phone.png`, fullPage: true });
  await tab.close(); await m.close();
  console.log("shot", name);
}
await browser.close();
