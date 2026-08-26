/**
 * HERO E2E — the full buyer-owned arc in the SANDBOX rail (finish plan §22).
 *
 * Brief → compile → constraint panel → offer spread → freeze → contract
 * dossier (MATERIAL PROMISES) → authorize → sandbox simulate capture →
 * PAID banner → synthetic ship + wrong-variant delivery (API-driven) →
 * MATERIAL BREACH page → remedy policy/execute via the gated pipeline →
 * REMEDIATED state reachable.
 *
 * Everything money-adjacent stays server-truth: PAID only ever comes from a
 * real signed webhook delivered through /api/demo/razorpay/simulate-event,
 * never from client-side fabrication — this spec polls for exactly that.
 */

import {
  API_URL,
  apiGet,
  apiPost,
  expect,
  requireServers,
  test,
} from "./helpers";

const HERO_BRIEF =
  "Buy me over-ear ANC headphones under Rs 12,000, with an Indian manufacturer warranty, arriving within 3 days. Do not show me anything over Rs 12,000.";

test.describe("sandbox hero arc", () => {
  test("brief to breach to remedy on the sandbox rail", async ({ page, api }) => {
    test.setTimeout(240_000);
    await requireServers(api, test.skip);

    // ---- reset the demo store so the arc starts from the seeded catalog ---
    const reset = await apiPost(api, "/api/demo/reset");
    if (reset.status === 403) {
      test.skip(true, "demo endpoints locked (live-test-mode without operator token) — run with DANTE_DEMO_OPERATOR_TOKEN or unset RAZORPAY keys");
    }
    expect(reset.status, "POST /api/demo/reset").toBe(200);

    // ---- 1. the brief -----------------------------------------------------
    await page.goto("/buy", { waitUntil: "domcontentloaded" });
    const brief = page.getByLabel("Your buying brief, in your own words");
    await expect(brief).toBeVisible();
    await brief.fill(HERO_BRIEF);

    // ---- 2. compile + search ---------------------------------------------
    await page.getByRole("button", { name: "Compile intent" }).click();

    // The parsed typed constraints appear BEFORE any product is shown.
    const briefPanel = page.getByText("Buying brief", { exact: true }).first();
    await expect(briefPanel).toBeVisible({ timeout: 30_000 });

    // ---- 3. comparison spread with feasible + rejected rows ---------------
    await expect(
      page.getByRole("region", { name: "Offer comparison" }),
    ).toBeVisible({ timeout: 30_000 });

    const feasibleRadio = page
      .getByRole("radio", { name: /Select .+/ })
      .first();
    await expect(feasibleRadio).toBeAttached({ timeout: 15_000 });
    await feasibleRadio.check();

    // ---- 4. freeze into a Dante Contract ----------------------------------
    await page.getByRole("button", { name: "Freeze & open contract" }).click();

    // Redirect to /contract/[id] and wait for the dossier to load.
    await page.waitForURL(/\/contract\/[^/]+$/, { timeout: 30_000 });
    const contractUrl = new URL(page.url());
    const contractId = contractUrl.pathname.split("/").pop() as string;
    expect(contractId).toBeTruthy();

    // ---- 5. MATERIAL PROMISES section renders ------------------------------
    await expect(
      page.getByRole("heading", { name: "Material promises" }),
    ).toBeVisible({ timeout: 30_000 });

    // ---- 6. authorize -> sandbox order ------------------------------------
    await page
      .getByRole("button", { name: "Authorize & create payment order" })
      .click();

    // Sandbox rail: no Razorpay keys configured — the simulate affordance
    // must appear (either in the §7 panel or the sticky hand-off bar).
    const simulate = page.getByRole("button", {
      name: /Simulate test payment/i,
    });
    await expect(simulate).toBeVisible({ timeout: 30_000 });

    // ---- 7. simulate capture; PAID arrives ONLY via signed webhook --------
    await simulate.click();
    const paidBanner = page.getByText(/Paid — verified by webhook truth/i);
    await expect(paidBanner).toBeVisible({ timeout: 20_000 });

    // Server truth agrees.
    const paidDetail = await apiGet(api, `/api/contracts/${contractId}`);
    expect((paidDetail.contract as Record<string, unknown>).status).toBe("PAID");

    // ---- 8. synthetic fulfillment: ship, then deliver the WRONG variant ---
    expect((await apiPost(api, `/api/demo/contracts/${contractId}/ship`)).status).toBe(200);
    const delivered = await apiPost(
      api,
      `/api/demo/contracts/${contractId}/deliver`,
      { scenario: "wrong_variant" },
    );
    expect(delivered.status).toBe(200);
    const breaches = (delivered.json?.breaches ?? []) as unknown[];
    expect(breaches.length).toBeGreaterThan(0);

    // ---- 9. breach visible in the browser ---------------------------------
    await page.goto(`/contract/${contractId}/breach`, {
      waitUntil: "domcontentloaded",
    });
    await expect(
      page.getByRole("heading", { name: /MATERIAL BREACH/i }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/MISMATCH/).first()).toBeVisible();

    // ---- 10. gated remedy pipeline -> REMEDIATED ---------------------------
    await page.goto(`/contract/${contractId}/remedy`, {
      waitUntil: "domcontentloaded",
    });
    const evaluateBtn = page.getByRole("button", { name: /Evaluate policy/i });
    await expect(evaluateBtn).toBeVisible({ timeout: 30_000 });
    await evaluateBtn.click();

    // ALLOW auto-executes; REQUIRE_APPROVAL surfaces the human gate first.
    const approveOrDone = page
      .getByRole("button", { name: /Approve refund|Approve action/i })
      .or(page.getByText(/REMEDIATED — refund resolved green/i));
    await expect(approveOrDone.first()).toBeVisible({ timeout: 30_000 });

    const approveBtn = page.getByRole("button", {
      name: /Approve refund|Approve action/i,
    });
    if (await approveBtn.isVisible().catch(() => false)) {
      await approveBtn.click();
    }

    await expect(
      page.getByText(/REMEDIATED — refund resolved green/i),
    ).toBeVisible({ timeout: 60_000 });

    // Server truth: the contract walked the remedy family to REMEDIATED and
    // a real (sandbox-adapter) refund id exists on the money action.
    for (let i = 0; i < 30; i++) {
      const detail = await apiGet(api, `/api/contracts/${contractId}`);
      if ((detail.contract as Record<string, unknown>).status === "REMEDIATED") break;
      await page.waitForTimeout(1000);
    }
    const finalDetail = await apiGet(api, `/api/contracts/${contractId}`);
    expect((finalDetail.contract as Record<string, unknown>).status).toBe(
      "REMEDIATED",
    );
  });
});
