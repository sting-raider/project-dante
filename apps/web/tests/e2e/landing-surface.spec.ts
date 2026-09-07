/** LANDING SURFACE GUARD — the overview routes buyers into the proof. */

import { expect, requireServers, test } from "./helpers";

test.describe("landing overview surface", () => {
  test("explains the runtime and exposes the guided demo", async ({ page, api }) => {
    await requireServers(api, test.skip);

    await page.goto("/", { waitUntil: "networkidle" });
    await expect(
      page.getByRole("heading", { name: "Commerce that remembers the promise." }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: /Start a buying brief/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Open demo room/ }).first()).toBeVisible();
    await expect(page.getByRole("region", { name: "Dante surfaces" })).toBeVisible();
    await expect(page.getByText("Purchase Rights Graph", { exact: true })).toBeVisible();
    await expect(page.getByText("Append-only audit", { exact: true })).toBeVisible();

    // The overview remains usable in reduced-motion mobile mode.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.keyboard.press("Tab");
    await expect(page.locator(":focus")).toBeVisible();
  });
});
