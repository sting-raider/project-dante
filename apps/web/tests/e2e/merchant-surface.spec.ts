/** MERCHANT SURFACE GUARD — capability evidence stays visible and honest. */

import { expect, requireServers, test } from "./helpers";

test.describe("merchant profile surface", () => {
  test("shows catalog evidence and computed runtime capabilities", async ({ page, api }) => {
    await requireServers(api, test.skip);

    await page.goto("/merchant", { waitUntil: "networkidle" });
    await expect(
      page.getByRole("heading", { name: /What your AI buyers.*verify/ }),
    ).toBeVisible();
    await expect(page.getByRole("region", { name: "Merchant runtime profile" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      page.getByText("The catalog behind the promise.", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Computed merchant capability profile" }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole("region", { name: "Machine-readable catalog completeness" }),
    ).toBeVisible({ timeout: 30_000 });

    // The surface must remain usable at a narrow viewport and with keyboard focus.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.keyboard.press("Tab");
    await expect(page.locator(":focus")).toBeVisible();
  });
});
