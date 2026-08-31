/**
 * BUYER DESK BUNDLE GUARD — a multi-item brief must stay a visible bundle all
 * the way through comparison. This protects the primary UI promise: one
 * server-compiled brief, one line card per request, and one aggregate budget
 * guard before the buyer freezes anything.
 */

import { expect, requireServers, test, apiPost } from "./helpers";

const BUNDLE_BRIEF =
  "Buy me a 27-inch QHD monitor under ₹25,000 and a mechanical keyboard under " +
  "₹8,000. The monitor must have an IPS panel, at least a 144 Hz refresh rate, " +
  "DisplayPort, and an Indian manufacturer warranty. The keyboard should be 75% " +
  "or TKL, hot-swappable, wireless, and also have an Indian manufacturer warranty. " +
  "I prefer tactile switches, but linear switches are acceptable. Both items must " +
  "arrive within 5 days. Do not show me any monitor over ₹25,000 or any keyboard " +
  "over ₹8,000. Keep the total order under ₹33,000.";

test.describe("buyer desk bundle surface", () => {
  test("renders separate accountable lines and an aggregate budget guard", async ({
    page,
    api,
  }) => {
    await requireServers(api, test.skip);

    const reset = await apiPost(api, "/api/demo/reset");
    if (reset.status === 403) {
      if (process.env.CI) {
        throw new Error(
          "demo endpoints are locked in CI; the bundle browser gate requires the sandbox rail or an operator token",
        );
      }
      test.skip(
        true,
        "demo endpoints locked (live-test-mode without operator token) — run with DANTE_DEMO_OPERATOR_TOKEN or unset RAZORPAY keys",
      );
    }
    expect(reset.status, "POST /api/demo/reset").toBe(200);

    await page.goto("/buy", { waitUntil: "networkidle" });
    await page.getByLabel("Your buying brief, in your own words").fill(BUNDLE_BRIEF);
    await page.getByRole("button", { name: "Compile intent" }).click();

    await expect(
      page.getByLabel("Buyer desk status").getByText("2 lines", { exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Build your bundle", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("Use recommended bundle", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    const bundleBoard = page.locator(".buy-offer-board-bundle");
    await expect(bundleBoard.getByText("Budget cap", { exact: false })).toBeVisible();
    await expect(bundleBoard.getByText("₹33,000", { exact: true })).toBeVisible();

    const lineCards = page.locator('section[aria-label$=" offers"]');
    await expect(lineCards).toHaveCount(2);
    await expect(page.getByRole("radio", { name: /Select / })).toHaveCount(
      2,
    );
    await expect(
      page.getByText("Choose one eligible offer for every line.", { exact: false }),
    ).toBeVisible();
  });
});
