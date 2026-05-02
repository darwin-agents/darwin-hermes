---
name: shopify-checkout-2026
description: Navigate Shopify 2026 checkout including GDPR cookie consent banners
tags: [shopify, checkout, ecommerce, browser]
version: "1.0"
---

# Skill: Shopify 2026 Checkout Navigation

## Trigger

Use when navigating Shopify checkout and a cookie consent banner may block interaction.

## Preconditions

- A Browserbase session is open via `browser_navigate`
- Current page is a Shopify cart, product, or checkout page

## Steps

1. `browser_snapshot()` — capture current state and identify any cookie banner
2. If a GDPR/cookie banner is visible:
   - `browser_vision("locate Accept All button coordinates")`
   - `browser_click(x, y)` on the Accept All button
   - Wait ~500ms for the banner animation
   - `browser_snapshot()` to verify banner is gone
3. `browser_vision("locate Add to Cart or Checkout button")` — get coordinates
4. `browser_click(x, y)` on the checkout button
5. At the payment step, call `inject_payment()` — never `browser_type()` for card fields
6. After successful confirmation page: call `record_expense()`

## Notes

- Never click "Reject All" — some Shopify themes break session tracking
- If the banner reappears, repeat step 2 up to 3 times before escalating to `clarify()`
- The checkout button may be below the fold — check y-coordinates exceed viewport height

## Cost estimate

~$0.06 per checkout (2 snapshots + vision + 2 clicks + inject_payment)
