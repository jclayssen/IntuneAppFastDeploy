// Regenerates the README screenshots from the demo server (simulated Intune).
//
//   uv run python -m tests.demo            # in one terminal, restart it for each language
//   node docs/screenshots/make.js en       # in another (needs Node.js and Playwright)
//   node docs/screenshots/make.js fr
//
// The demo starts from the same data every time, so the pictures are reproducible.

const path = require("node:path");
const { chromium } = require("playwright");

const lang = process.argv[2] || "en";
const base = process.argv[3] || "http://localhost:8080";
const out = path.join(__dirname, lang);
const PILOT = { en: "Pilot - IT team", fr: "Pilote - équipe informatique" }[lang];

(async () => {
  const texts = await (await fetch(`${base}/locales/${lang}.json`)).json();
  const t = (key) => texts[key];
  const browser = await chromium.launch();
  const context = await browser.newContext({
    // A short viewport: full-page captures then stop where the content stops.
    viewport: { width: 1100, height: 300 },
    deviceScaleFactor: 2,
    locale: lang,
  });
  await context.addInitScript((code) => localStorage.setItem("iafd_language", code), lang);
  const page = await context.newPage();
  const shot = async (name) => {
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true });
  };
  const button = (key) => page.locator(`button:has-text("${t(key)}")`);

  // 1. Sign-in page (the demo is always signed in, so pretend the session is missing).
  await page.route("**/api/me", (route) => route.fulfill({ status: 401, json: { error: {} } }));
  await page.goto(base);
  await page.waitForSelector("a.btn");
  await shot("01-sign-in");
  await page.unroute("**/api/me");

  // 2. Application list.
  await page.goto(base);
  await page.waitForSelector("table");
  await shot("02-applications");

  // 3. Application page with its pilot and production groups.
  await page.click("text=7-Zip 23.01 (x64 edition)");
  await page.waitForSelector("dl.facts");
  const pilot = page.locator(".stage").nth(0);
  await pilot.locator("input.mono").fill("3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f");
  await pilot.locator("input:not(.mono)").fill(PILOT);
  await pilot.locator(`button:has-text("${t("action.add_group")}")`).click();
  await page.locator(".stage").nth(1).locator(`button:has-text("${t("target.all_devices")}")`).click();
  await button("action.save_groups").click();
  // Saving reloads the page: with production groups configured the badge turns green.
  await page.waitForSelector("h1 .badge.ok");
  await page.waitForSelector(`.chip:has-text("${PILOT}")`);
  await shot("03-application");

  // 4. Update: give the link.
  await page.locator(".head").locator(`button:has-text("${t("action.update")}")`).click();
  await page.fill("input[type=url]", "https://www.7-zip.org/a/7z2408-x64.msi");
  await shot("04-link");

  // 5. Review before publishing.
  await button("action.analyze").click();
  await page.waitForSelector(`text=${t("review.title")}`, { timeout: 60000 });
  await shot("05-review");

  // 6. Published.
  await button("action.publish").click();
  await page.waitForSelector(".notice.ok", { timeout: 60000 });
  await shot("06-published");

  // 7. The new version is in its pilot phase.
  await button("done.open").click();
  await page.waitForSelector("dl.facts");
  await shot("07-pilot");

  // 8. New application from an EXE installer.
  await button("action.back").click();
  await page.waitForSelector("table");
  await button("action.new_app").click();
  await page.fill("input[type=url]", "https://www.7-zip.org/a/7z2408-x64.exe");
  await button("action.analyze").click();
  await page.waitForSelector(`text=${t("review.title")}`, { timeout: 60000 });
  const mono = page.locator(".panel").nth(1).locator("input.mono");
  await mono.nth(0).fill('"7z2408-x64.exe" /S');
  await mono.nth(1).fill('"%ProgramFiles%\\7-Zip\\Uninstall.exe" /S');
  const detection = page.locator(".panel").nth(2).locator("input.mono");
  await detection.nth(0).fill("%ProgramFiles%\\7-Zip");
  await detection.nth(1).fill("7zFM.exe");
  await shot("08-new-application");

  // 9. Device inventory: the software Intune found on the devices.
  await page.goto(base);
  await page.waitForSelector("table");
  await page.locator(`#nav button:has-text("${t("nav.inventory")}")`).click();
  await page.waitForSelector("main table");
  await shot("09-inventory");

  // 10. One device and its software.
  await page.locator(`main .tabs button:has-text("${t("inventory.by_device")}")`).click();
  await page.waitForFunction(() => document.querySelectorAll("main tbody tr").length === 8);
  await page.locator("main tbody button.linkish").nth(2).click();
  await page.waitForSelector("dl.facts");
  await shot("10-device");

  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
