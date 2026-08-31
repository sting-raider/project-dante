/** DEMO ROOM SURFACE GUARD — readiness and the guided run stay dominant. */

import { expect, requireServers, test } from "./helpers";

test.describe("demo room surface", () => {
  test("shows readiness before collapsed manual controls", async ({ page, api }) => {
    await requireServers(api, test.skip);

    await page.goto("/demo", { waitUntil: "networkidle" });
    await expect(
      page.getByRole("heading", { name: /One click buys it, breaks it/ }),
    ).toBeVisible();
    await expect(page.getByText("Control room", { exact: true })).toBeVisible();
    await expect(page.getByText("Payment rail", { exact: true })).toBeVisible();
    await expect(page.getByText("Operator gate", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /RUN HERO SCENARIO/ })).toBeVisible();

    const manualControls = page.locator("details.demo-manual-controls");
    await expect(manualControls).toBeVisible();
    await expect(manualControls).not.toHaveAttribute("open", "");
    await expect(page.getByText("Reset, ship, deliver, or mark replacement unavailable.", { exact: true })).toBeVisible();
  });
});

