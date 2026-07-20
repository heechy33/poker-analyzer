import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const appUrl = "http://127.0.0.1:3100";
const handId = "00000000-0000-4000-8000-000000000008";

const user = {
  id: "00000000-0000-4000-8000-000000000001",
  aud: "authenticated",
  role: "authenticated",
  email: "study@example.com",
  phone: "",
  app_metadata: { provider: "email", providers: ["email"] },
  user_metadata: {},
  identities: [],
  created_at: "2026-01-01T00:00:00.000Z",
  updated_at: "2026-01-01T00:00:00.000Z",
};

const handSummary = {
  id: handId,
  coinpoker_hand_id: 8008,
  played_at: "2026-07-18T20:00:00.000Z",
  table_name: "P0 Acceptance",
  table_format: "hu_2max",
  stake_sb: "0.50",
  stake_bb: "1.00",
  hero_position: "BTN/SB",
  hero_cards: ["Ah", "Kh"],
  hero_net: "-3.00",
  hero_net_bb: "-3.00",
  went_to_showdown: false,
  total_pot: "6.00",
};

const handDetail = {
  ...handSummary,
  upload_id: "00000000-0000-4000-8000-000000000009",
  session_id: null,
  button_seat: 1,
  hero_seat: 1,
  flop: ["As", "7d", "2c"],
  turn: null,
  river: null,
  rake: "0.00",
  splash_fee: "0.00",
  hero_invested: "3.00",
  hero_collected: "0.00",
  won_at_showdown: null,
  flags: {},
  raw_text: null,
  players: [
    {
      seat: 1,
      screen_name: "Hero",
      position: "BTN/SB",
      starting_stack: "100.00",
      is_hero: true,
      final_cards: ["Ah", "Kh"],
    },
    {
      seat: 2,
      screen_name: "Villain",
      position: "BB",
      starting_stack: "100.00",
      is_hero: false,
      final_cards: null,
    },
  ],
  actions: [
    {
      street: "preflop",
      action_order: 1,
      seat: 1,
      screen_name: "Hero",
      action: "post_sb",
      amount: "0.50",
      raise_to: null,
      is_all_in: false,
    },
    {
      street: "preflop",
      action_order: 2,
      seat: 2,
      screen_name: "Villain",
      action: "post_bb",
      amount: "1.00",
      raise_to: null,
      is_all_in: false,
    },
    {
      street: "preflop",
      action_order: 3,
      seat: 1,
      screen_name: "Hero",
      action: "raise",
      amount: "2.50",
      raise_to: "3.00",
      is_all_in: false,
    },
    {
      street: "preflop",
      action_order: 4,
      seat: 2,
      screen_name: "Villain",
      action: "call",
      amount: "2.00",
      raise_to: null,
      is_all_in: false,
    },
    {
      street: "flop",
      action_order: 5,
      seat: 2,
      screen_name: "Villain",
      action: "check",
      amount: null,
      raise_to: null,
      is_all_in: false,
    },
    {
      street: "flop",
      action_order: 6,
      seat: 1,
      screen_name: "Hero",
      action: "check",
      amount: null,
      raise_to: null,
      is_all_in: false,
    },
  ],
};

function authCookieValue(): string {
  const session = {
    access_token: "p0-acceptance-access-token",
    refresh_token: "p0-acceptance-refresh-token",
    expires_in: 2_147_483_647,
    expires_at: 4_102_444_800,
    token_type: "bearer",
    user,
  };
  return `base64-${Buffer.from(JSON.stringify(session)).toString("base64url")}`;
}

async function authenticate(context: BrowserContext): Promise<void> {
  await context.addCookies([
    {
      name: "sb-127-auth-token",
      value: authCookieValue(),
      url: appUrl,
      httpOnly: false,
      sameSite: "Lax",
      secure: false,
    },
  ]);
}

async function mockProductApi(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/stats/summary") {
      await route.fulfill({
        json: {
          hands_count: 1,
          vpip_pct: 100,
          pfr_pct: 100,
          three_bet_pct: 0,
          wtsd_pct: 0,
          wsd_pct: 0,
          bb_per_100: -300,
        },
      });
      return;
    }
    if (path === "/api/stats/by-position") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/stats/leaks") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/hands/losers") {
      await route.fulfill({ json: [handSummary] });
      return;
    }
    if (path === "/api/hands/filter-options") {
      await route.fulfill({
        json: { stakes: [{ sb: "0.50", bb: "1.00", label: "0.50/1.00" }], table_formats: ["hu_2max"] },
      });
      return;
    }
    if (path === `/api/hands/${handId}`) {
      await route.fulfill({ json: handDetail });
      return;
    }
    if (path === `/api/hands/${handId}/analyses`) {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/hands") {
      await route.fulfill({ json: [handSummary] });
      return;
    }

    await route.fulfill({ status: 404, json: { detail: "P0 acceptance API mock: route not found" } });
  });
}

async function expectNoLegacyDecisionOutput(page: Page): Promise<void> {
  await expect(page.getByRole("tab", { name: /^solver$/i })).toHaveCount(0);
  await expect(page.locator('[aria-label$=" strategy"]')).toHaveCount(0);

  const body = page.locator("body");
  await expect(body).not.toContainText("Action Overview");
  await expect(body).not.toContainText("GTO Analysis");
  await expect(body).not.toContainText("Approximate GTO");
  await expect(body).not.toContainText("Hand Score");
  await expect(body).not.toContainText("Solver line:");
  await expect(body).not.toContainText("solver frequencies");
  await expect(body).not.toContainText("Action frequency");
  await expect(body).not.toContainText(/range\s+\d+%/i);
  await expect(body).not.toContainText(/\d+\s+(?:of\s+\d+\s+)?(?:decisions?\s+)?graded/i);
  await expect(body).not.toContainText(/\d+\s+solid\s*\/\s*\d+\s+close\s*\/\s*\d+\s+mistakes?/i);
  await expect(body).not.toContainText(/\b(?:hand\s+)?score\s*:?\s*\d+(?:\.\d+)?(?:\s*\/\s*100)?\b/i);
  const visibleGradeLabels = await page
    .getByText(/^(Solid|Close|Mistake|Unmatched)$/)
    .evaluateAll((nodes) =>
      nodes.filter((node) => {
        const style = window.getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return (
          !node.classList.contains("sr-only") &&
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      }).length,
    );
  expect(visibleGradeLabels).toBe(0);
}

test("public login has no legacy solver or grading surface", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expectNoLegacyDecisionOutput(page);
});

test.describe("authenticated deployed-app matrix", () => {
  test.beforeEach(async ({ context, page }) => {
    await authenticate(context);
    await mockProductApi(page);
  });

  for (const surface of [
    { path: "/dashboard", heading: "Dashboard" },
    { path: "/upload", heading: "Upload" },
    { path: "/hands", heading: "Hands" },
    { path: "/leaks", heading: "Leaks" },
  ]) {
    test(`${surface.path} exposes no legacy decision output`, async ({ page }) => {
      await page.goto(surface.path);
      await expect(page.getByRole("heading", { name: surface.heading })).toBeVisible();
      await expectNoLegacyDecisionOutput(page);
    });
  }

  test("review modal contains replay and general coaching only", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    const handRow = page.getByRole("row").filter({ hasText: "Ah Kh" });
    await expect(handRow).toBeVisible();
    await handRow.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("tab", { name: "Replayer" })).toBeVisible();
    await expect(dialog.getByRole("tab", { name: "Coach" })).toBeVisible();
    await expectNoLegacyDecisionOutput(page);

    await dialog.getByRole("tab", { name: "Coach" }).click();
    await expect(dialog.getByText("General coaching—no verified solver result")).toBeVisible();
    await expectNoLegacyDecisionOutput(page);
  });

  for (const path of ["/solver", "/solver/flop", "/dev/range-grid"]) {
    test(`${path} is not a deployed route`, async ({ page }) => {
      const response = await page.goto(path, { timeout: 10_000, waitUntil: "domcontentloaded" });
      expect(response?.status()).toBe(404);
      await expect(page.getByText("This page could not be found.")).toBeVisible();
      await expectNoLegacyDecisionOutput(page);
    });
  }
});
