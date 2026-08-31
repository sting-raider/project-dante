/**
 * CHECKOUT OPTIONS regression guard (finish plan §3.1 / §22 spec 2).
 *
 * The one bug class this suite must catch forever: passing `key_id` (the
 * option NAME) instead of `key` to Razorpay Standard Checkout. checkout.js
 * reads the option named `key`; a stray key_id silently breaks every live
 * payment while looking perfectly fine in code review.
 *
 * Method: intercept https://checkout.razorpay.com/v1/checkout.js and fulfil
 * with a stub that records the constructor argument object onto
 * window.__rzpCtorArgs, drive a contract to READY_TO_PAY through API mocks
 * shaped exactly like the real live-test-mode responses, click Pay, and
 * assert on what the app actually handed to new Razorpay(...).
 */

import { expect, requireServers, test } from "./helpers";

const STUB_CHECKOUT_JS = `(() => {
  // Recording stub for Razorpay Standard Checkout (test-only).
  window.__rzpCtorArgs = [];
  window.__rzpInstances = [];
  function Razorpay(options) {
    window.__rzpCtorArgs.push(options);
    const inst = {
      open() { window.__rzpOpened = (window.__rzpOpened || 0) + 1; },
      on(event, handler) {
        this._handlers = this._handlers || {};
        (this._handlers[event] = this._handlers[event] || []).push(handler);
      },
    };
    window.__rzpInstances.push(inst);
    return inst;
  }
  window.Razorpay = Razorpay;
})();`;

const MOCK_CONTRACT_ID = "e2e_checkout_contract";
const MOCK_ORDER_ID = "order_e2eN0key1d";
// Deliberately NOT key-id-shaped: underscores break the secrets scanner's
// `rzp_(live|test)_[A-Za-z0-9]{8,}` pattern so this dummy literal can never
// be mistaken for committed key material (tests/test_security_redteam.py).
const MOCK_KEY_ID = "rzp_test_DUMMY_E2E_KEY"; // dummy stand-in for a real test key id
const MOCK_AMOUNT_PAISE = 899900;

function contractPayload(
  status: string,
  overrides?: { authorized?: boolean; razorpay_order_id?: string | null },
) {
  const authorized = overrides?.authorized ?? status !== "AWAITING_BUYER_AUTH";
  return {
    contract: {
      id: MOCK_CONTRACT_ID,
      display_code: "E2E-0001",
      intent_id: "intent_e2e",
      offer_id: "off_e2e",
      buyer_authority: authorized
        ? {
            max_amount_paise: MOCK_AMOUNT_PAISE,
            currency: "INR",
            authorized_at: new Date().toISOString(),
            authorized_by: "demo-buyer",
            scope: "single_purchase",
            contract_hash_at_authorization: "deadbeef",
          }
        : null,
      offer_hash: "hash_offer",
      promise_set_hash: "hash_promises",
      contract_hash: authorized ? "deadbeef" : "hash_contract",
      razorpay_order_id:
        overrides?.razorpay_order_id !== undefined
          ? overrides.razorpay_order_id
          : status === "AWAITING_BUYER_AUTH"
            ? null
            : MOCK_ORDER_ID,
      razorpay_payment_id: null,
      amount_paise: MOCK_AMOUNT_PAISE,
      status,
      created_at: new Date().toISOString(),
      frozen_at: new Date().toISOString(),
      sandbox_mode: false,
    },
    promises: [
      {
        id: "prm_1",
        key: "price.amount_paise",
        value: MOCK_AMOUNT_PAISE,
        normalized_value: MOCK_AMOUNT_PAISE,
        source_artifact_id: null,
        extraction_method: "structured",
        verification_status: "verified",
        confidence: 1,
        material_to_intent: true,
        material_reason: "price is material",
      },
    ],
    entitlements: [],
  };
}

test.describe("razorpay checkout options", () => {
  test("Pay hands checkout.js `key` (never key_id) + order/amount/currency INR", async ({
    page,
    api,
  }) => {
    await requireServers(api, test.skip);

    // ---- route mocks: the whole page talks to mocked API shapes ----------
    let authorizeCalls = 0;
    let orderCalls = 0;
    let contractGets = 0;
    const orderResponse = {
      mode: "live-test-mode",
      razorpay_order: {
        id: MOCK_ORDER_ID,
        amount: MOCK_AMOUNT_PAISE,
        currency: "INR",
        status: "created",
      },
      checkout_config: {
        key_id: MOCK_KEY_ID,
        order_id: MOCK_ORDER_ID,
        amount_paise: MOCK_AMOUNT_PAISE,
        currency: "INR",
      },
      contract_status: "PAYMENT_ORDER_CREATED",
    };

    // Session handoff normally rides sessionStorage from /buy; seed it so the
    // dossier renders its offer panel exactly like a real frozen purchase.
    await page.addInitScript(
      ({ contractId, offerMemoKey, briefKey }) => {
        window.sessionStorage.setItem(
          `${offerMemoKey}${contractId}.offer`,
          JSON.stringify({
            offer: {
              id: "off_e2e",
              merchant_id: "aster-electronics",
              sku: "AST-E2E-001",
              title: "Zephyr QuietMax 45 Wireless ANC Over-Ear Headphones",
              brand: "Zephyr",
              category: "headphones",
              variant: {},
              unit_amount_paise: 899900,
              currency: "INR",
              inventory: 58,
              delivery_promise: {
                min_days: 1,
                max_days: 3,
                promised_by_date: null,
                service: "AsterExpress",
              },
              terms: {
                warranty_type: "manufacturer",
                warranty_duration_months: 24,
                warranty_region: "IN",
                return_window_days: 10,
                replacement_window_days: 10,
                condition: "new",
                region: "IN",
              },
            },
            explanation: "all hard constraints hold",
            softScores: [],
          }),
        );
        window.sessionStorage.setItem(
          briefKey,
          "Buy me over-ear ANC headphones under Rs 12,000.",
        );
      },
      {
        contractId: MOCK_CONTRACT_ID,
        offerMemoKey: "dante.contract.",
        briefKey: "dante.brief.raw",
      },
    );

    // NOTE route order: Playwright matches patterns LIFO, so the more
    // specific /authorize and /payment-order routes are registered AFTER the
    // bare contract-id route. Each mock flips the contract status the way the
    // real server would as the buyer walks Stage 1.
    await page.route("**/api/intents/**", (route) => route.fulfill({ status: 404, body: "{}" }));
    await page.route(`**/api/contracts/${MOCK_CONTRACT_ID}/authorize`, async (route) => {
      authorizeCalls += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          contractPayload("PAYMENT_ORDER_CREATED", {
            authorized: true,
            razorpay_order_id: MOCK_ORDER_ID,
          }),
        ),
      });
    });
    await page.route(`**/api/contracts/${MOCK_CONTRACT_ID}/payment-order`, async (route) => {
      orderCalls += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(orderResponse),
      });
    });
    await page.route(`**/api/contracts/${MOCK_CONTRACT_ID}`, async (route) => {
      contractGets += 1;
      const authorized = authorizeCalls > 0;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          contractPayload(authorized ? "PAYMENT_ORDER_CREATED" : "AWAITING_BUYER_AUTH", {
            authorized,
            razorpay_order_id: authorized ? MOCK_ORDER_ID : null,
          }),
        ),
      });
    });

    // ---- the point of the whole spec: stub checkout.js -------------------
    await page.route(
      "https://checkout.razorpay.com/v1/checkout.js*",
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/javascript",
          body: STUB_CHECKOUT_JS,
        }),
    );

    // ---- drive to READY_TO_PAY -------------------------------------------
    // Stage 1: authorize + create order (the §52 sticky card button).
    await page.goto(`/contract/${MOCK_CONTRACT_ID}`, { waitUntil: "domcontentloaded" });
    await page
      .getByRole("button", { name: "Authorize & create payment order" })
      .click();

    // Stage 2: the explicit Pay button appears once the order exists.
    const payButton = page.getByRole("button", {
      name: /Pay .* securely via Razorpay/i,
    });
    await expect(payButton).toBeVisible({ timeout: 30_000 });

    // checkout.js must be present before the click can succeed.
    await page.waitForFunction(() => typeof (window as { Razorpay?: unknown }).Razorpay === "function", undefined, {
      timeout: 30_000,
    });

    await payButton.click();

    // ---- assertions on the ACTUAL constructor args ------------------------
    await page.waitForFunction(
      () => ((window as { __rzpCtorArgs?: unknown[] }).__rzpCtorArgs?.length ?? 0) > 0,
    );
    const ctorArgs = (await page.evaluate(() => {
      const args = (window as { __rzpCtorArgs?: Record<string, unknown>[] })
        .__rzpCtorArgs;
      return args && args.length > 0 ? args[0] : null;
    })) as Record<string, unknown> | null;
    expect(ctorArgs).not.toBeNull();
    expect(ctorArgs as object).not.toBeNull();

    // THE regression: the public key VALUE must arrive under `key`.
    // A `key_id` property here is the exact historical bug — fail loudly.
    expect(ctorArgs!.key).toBe(MOCK_KEY_ID);
    expect(ctorArgs).not.toHaveProperty("key_id");

    expect(ctorArgs!.order_id).toBe(MOCK_ORDER_ID);
    expect(ctorArgs!.amount).toBe(MOCK_AMOUNT_PAISE);
    expect(Number.isInteger(ctorArgs!.amount)).toBe(true); // integer paise
    expect(ctorArgs!.currency).toBe("INR");

    // And the stub instance was actually opened inside the user gesture.
    const opened = await page.evaluate(
      () => (window as { __rzpOpened?: number }).__rzpOpened ?? 0,
    );
    expect(opened).toBeGreaterThan(0);
    expect(orderCalls).toBe(1);
    expect(contractGets).toBeGreaterThan(0);
  });

  test("cold-refresh reads back a pending live order and can reopen checkout", async ({
    page,
    api,
  }) => {
    await requireServers(api, test.skip);

    let contractGets = 0;
    let paymentOrderGets = 0;
    const orderResponse = {
      mode: "live-test-mode",
      razorpay_order: {
        id: MOCK_ORDER_ID,
        amount: MOCK_AMOUNT_PAISE,
        currency: "INR",
        status: "created",
      },
      checkout_config: {
        key_id: MOCK_KEY_ID,
        order_id: MOCK_ORDER_ID,
        amount_paise: MOCK_AMOUNT_PAISE,
        currency: "INR",
      },
      contract_status: "PAYMENT_PENDING",
    };

    // This test intentionally seeds no order snapshot. The only usable live
    // checkout key must therefore come from GET /payment-order.
    await page.addInitScript(() => window.sessionStorage.clear());
    await page.route("**/api/intents/**", (route) =>
      route.fulfill({ status: 404, body: "{}" }),
    );
    await page.route(`**/api/contracts/${MOCK_CONTRACT_ID}/payment-order`, async (route) => {
      expect(route.request().method()).toBe("GET");
      paymentOrderGets += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(orderResponse),
      });
    });
    await page.route(`**/api/contracts/${MOCK_CONTRACT_ID}`, async (route) => {
      contractGets += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(contractPayload("PAYMENT_PENDING")),
      });
    });
    await page.route(
      "https://checkout.razorpay.com/v1/checkout.js*",
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/javascript",
          body: STUB_CHECKOUT_JS,
        }),
    );

    await page.goto(`/contract/${MOCK_CONTRACT_ID}`, { waitUntil: "domcontentloaded" });
    const reopenButton = page.getByRole("button", {
      name: "Re-open Razorpay checkout",
    });
    await expect(reopenButton).toBeVisible({ timeout: 30_000 });
    await page.waitForFunction(
      () => typeof (window as { Razorpay?: unknown }).Razorpay === "function",
      undefined,
      { timeout: 30_000 },
    );

    await reopenButton.click();
    await page.waitForFunction(
      () => ((window as { __rzpCtorArgs?: unknown[] }).__rzpCtorArgs?.length ?? 0) > 0,
    );
    const ctorArgs = (await page.evaluate(() => {
      const args = (window as { __rzpCtorArgs?: Record<string, unknown>[] }).__rzpCtorArgs;
      return args && args.length > 0 ? args[0] : null;
    })) as Record<string, unknown> | null;

    expect(ctorArgs).not.toBeNull();
    expect(ctorArgs!.key).toBe(MOCK_KEY_ID);
    expect(ctorArgs).not.toHaveProperty("key_id");
    expect(ctorArgs!.order_id).toBe(MOCK_ORDER_ID);
    expect(ctorArgs!.amount).toBe(MOCK_AMOUNT_PAISE);
    expect(ctorArgs!.currency).toBe("INR");
    expect(paymentOrderGets).toBe(1);
    expect(contractGets).toBeGreaterThan(0);
  });

  test("initial dossier load failure shows an actionable retry instead of hanging", async ({
    page,
    api,
  }) => {
    await requireServers(api, test.skip);

    await page.route(`**/api/contracts/${MOCK_CONTRACT_ID}`, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "API temporarily unavailable" }),
      }),
    );

    await page.goto(`/contract/${MOCK_CONTRACT_ID}`, { waitUntil: "domcontentloaded" });

    await expect(
      page.getByRole("heading", { name: "Contract unavailable" }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("HTTP 503: API temporarily unavailable")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  test("authorized dossier keeps stacked side panels above the promise ledger", async ({
    page,
    api,
  }) => {
    await requireServers(api, test.skip);

    await page.route("**/api/intents/**", (route) =>
      route.fulfill({ status: 404, body: "{}" }),
    );
    await page.route(`**/api/contracts/${MOCK_CONTRACT_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          contractPayload("PAYMENT_ORDER_CREATED", {
            authorized: true,
            razorpay_order_id: MOCK_ORDER_ID,
          }),
        ),
      }),
    );

    await page.goto(`/contract/${MOCK_CONTRACT_ID}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "Material promises" })).toBeVisible();
    await expect(page.getByText("Authorization envelope")).toBeVisible();

    const bounds = await page.evaluate(() => {
      const grid = document.querySelector(".contract-primary-grid");
      const sidePanels = [
        ...document.querySelectorAll(".contract-primary-grid > div:last-child > section"),
      ];
      const authorization = sidePanels.find((panel) =>
        panel.textContent?.includes("Authorization envelope"),
      );
      const ledger = document.querySelector(".contract-ledger-section");
      if (!grid || !authorization || !ledger) return null;
      const gridRect = grid.getBoundingClientRect();
      const authorizationRect = authorization.getBoundingClientRect();
      const ledgerRect = ledger.getBoundingClientRect();
      return {
        gridBottom: gridRect.bottom,
        authorizationBottom: authorizationRect.bottom,
        ledgerTop: ledgerRect.top,
      };
    });

    expect(bounds).not.toBeNull();
    expect(bounds!.authorizationBottom).toBeLessThanOrEqual(bounds!.ledgerTop + 1);
    expect(bounds!.authorizationBottom).toBeLessThanOrEqual(bounds!.gridBottom + 1);
  });
});
