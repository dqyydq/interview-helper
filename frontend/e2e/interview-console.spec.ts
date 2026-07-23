import { expect, test } from "@playwright/test";

const company = {
  id: "company-visual",
  name: "字节跳动",
  slug: "bytedance",
  description: "视觉基线公司",
  is_system: true,
  archived: false,
  latest_style_pack: {
    id: "style-visual",
    name: "技术深挖",
    pack_version: 1,
    supported_roles: ["llm_application_engineer"],
    default_interviewer_behavior: {},
    field_confidence: {},
    status: "active",
    visibility: "private",
    evidence_count: 3,
    evidence_label: "有来源支持",
    rounds: [
      {
        id: "round-visual-1",
        round_key: "round_1",
        name: "一面",
        sequence: 1,
        opening_style: "从项目背景切入，确认候选人的实际职责。",
        topic_weights: { Agent工程: 0.55, RAG与检索: 0.45 },
        follow_up_patterns: ["为什么选择这个方案？", "失败时如何降级？"],
        pressure_level: 3,
        answer_expectations: ["结论明确", "证据充分"],
        evaluation_weights: { technical_depth: 0.6, communication: 0.4 },
        duration_minutes: 45,
      },
      {
        id: "round-visual-2",
        round_key: "round_2",
        name: "二面",
        sequence: 2,
        opening_style: "深入工程落地与系统边界。",
        topic_weights: { 系统设计: 1 },
        follow_up_patterns: ["规模扩大十倍会怎样？"],
        pressure_level: 4,
        answer_expectations: ["说明取舍"],
        evaluation_weights: { system_design: 1 },
        duration_minutes: 45,
      },
      {
        id: "round-visual-3",
        round_key: "round_3",
        name: "三面",
        sequence: 3,
        opening_style: "综合判断技术决策与协作能力。",
        topic_weights: { 综合判断: 1 },
        follow_up_patterns: ["最大的技术风险是什么？"],
        pressure_level: 4,
        answer_expectations: ["边界清晰"],
        evaluation_weights: { communication: 1 },
        duration_minutes: 45,
      },
    ],
  },
};

const viewports = [
  { name: "desktop-1440", width: 1440, height: 1024 },
  { name: "laptop-1024", width: 1024, height: 900 },
  { name: "tablet-768", width: 768, height: 900 },
  { name: "mobile-390", width: 390, height: 844 },
];

test.beforeEach(async ({ page }) => {
  await page.route("**/api/companies", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([company]),
    }),
  );
});

test("keeps the Precision Console usable at all baseline viewports", async ({ page }, testInfo) => {
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/interviews");
    await expect(page.getByRole("heading", { name: "选择公司" })).toBeVisible();
    await expect(page.getByRole("button", { name: "配置本场模拟" })).toBeVisible();

    const layout = await page.evaluate(() => {
      const root = document.documentElement;
      const consoleElement = document.querySelector(".company-console");
      const visibleButtons = [...document.querySelectorAll("button")].filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      });
      return {
        hasGlobalOverflow: root.scrollWidth > root.clientWidth,
        consoleDisplay: consoleElement ? getComputedStyle(consoleElement).display : null,
        gridTemplateColumns: consoleElement
          ? getComputedStyle(consoleElement).gridTemplateColumns
          : null,
        smallestButton: Math.min(
          ...visibleButtons.map((element) => {
            const rect = element.getBoundingClientRect();
            return Math.min(rect.width, rect.height);
          }),
        ),
      };
    });

    expect(layout.hasGlobalOverflow).toBe(false);
    expect(layout.smallestButton).toBeGreaterThanOrEqual(32);
    if (viewport.width >= 1301) {
      await expect(page.getByRole("button", { name: /快速搜索/ })).toBeVisible();
      expect(layout.consoleDisplay).toBe("grid");
    }

    await testInfo.attach(`precision-console-${viewport.name}`, {
      body: await page.screenshot({ fullPage: true }),
      contentType: "image/png",
    });
  }
});
