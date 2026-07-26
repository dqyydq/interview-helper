import { expect, test } from "@playwright/test";

const company = {
  id: "company-layout",
  name: "布局验证公司",
  slug: "layout-check",
  description: "用于验证主工作区的稳定几何。",
  is_system: true,
  archived: false,
  latest_style_pack: {
    id: "style-layout",
    name: "技术深挖",
    pack_version: 1,
    supported_roles: ["llm_application_engineer"],
    default_interviewer_behavior: {},
    field_confidence: {},
    status: "active",
    visibility: "private",
    evidence_count: 1,
    evidence_label: "有来源支持",
    rounds: [],
  },
};

async function frameMetrics(page: import("@playwright/test").Page) {
  return page.locator("[data-page-frame='primary']").evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      minHeight: Math.round(parseFloat(getComputedStyle(element).minHeight)),
      hasHorizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });
}

async function fontSize(page: import("@playwright/test").Page, selector: string) {
  return page.locator(selector).evaluate((element) => Math.round(parseFloat(getComputedStyle(element).fontSize)));
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/companies", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([company]) }),
  );
  await page.route("**/api/question-banks", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/questions?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [], count: 0, offset: 0, limit: 20 }),
    }),
  );
  await page.route("**/api/resumes", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/reports", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
});

test("keeps primary navigation inside one stable page frame", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/interviews");
  await expect(page.getByRole("heading", { name: "选择公司" })).toBeVisible();

  const baseline = await frameMetrics(page);
  expect(baseline.width).toBe(1500);
  expect(baseline.hasHorizontalOverflow).toBe(false);

  await page.getByRole("link", { name: "面试知识库" }).click();
  await expect(page.getByRole("heading", { name: "面试知识库" })).toBeVisible();
  const knowledge = await frameMetrics(page);

  await page.getByRole("link", { name: "评估报告" }).click();
  await expect(page.getByRole("heading", { name: "面试评估报告" })).toBeVisible();
  const reports = await frameMetrics(page);

  for (const metric of [knowledge, reports]) {
    expect(Math.abs(metric.x - baseline.x)).toBeLessThanOrEqual(1);
    expect(Math.abs(metric.y - baseline.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(metric.width - baseline.width)).toBeLessThanOrEqual(1);
    expect(metric.minHeight).toBeGreaterThan(0);
    expect(metric.hasHorizontalOverflow).toBe(false);
  }
});

test("uses the shared typography scale across primary workspaces", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1024 });

  await page.goto("/interviews");
  await expect(page.locator(".company-rail-heading h1")).toBeVisible();
  expect(await fontSize(page, '.primary-nav a[href="/interviews"]')).toBe(13);
  expect(await fontSize(page, ".statusbar")).toBe(12);
  expect(await fontSize(page, ".company-rail-heading h1")).toBe(18);

  await page.goto("/questions");
  await expect(page.locator(".knowledge-heading h1")).toBeVisible();
  expect(await fontSize(page, ".knowledge-heading h1")).toBe(48);
  expect(await fontSize(page, ".knowledge-tabs button.active")).toBe(14);
  expect(await fontSize(page, ".search-field input")).toBe(14);

  await page.goto("/reports");
  await expect(page.locator(".report-index h1")).toBeVisible();
  expect(await fontSize(page, ".report-index h1")).toBe(24);

  expect((await frameMetrics(page)).hasHorizontalOverflow).toBe(false);
});

for (const viewportWidth of [320, 375, 414, 768]) {
  test(`keeps primary navigation within the viewport at ${viewportWidth}px`, async ({ page }) => {
    await page.setViewportSize({ width: viewportWidth, height: 900 });
    await page.goto("/interviews");
    await expect(page.getByRole("heading", { name: "选择公司" })).toBeVisible();

    for (const [label, heading] of [
      ["模拟面试", "选择公司"],
      ["面试知识库", "面试知识库"],
      ["评估报告", "面试评估报告"],
      ["设置", "系统设置"],
    ]) {
      await page.getByRole("link", { name: label }).click();
      await expect(page.getByRole("heading", { name: heading })).toBeVisible();

      const metrics = await frameMetrics(page);
      expect(metrics.width).toBeLessThanOrEqual(viewportWidth);
      expect(metrics.hasHorizontalOverflow).toBe(false);
    }

    const wrappedNavigationLabels = await page.locator(".primary-nav .nav-link span").evaluateAll(
      (labels) =>
        labels.filter((label) => label.scrollHeight > label.clientHeight + 1).map((label) => label.textContent),
    );

    expect(wrappedNavigationLabels).toEqual([]);
  });
}
