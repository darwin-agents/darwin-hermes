---
name: stripe-2fa-handler
description: Recognize Stripe 2FA/OTP wall and escalate to human via clarify()
tags: [stripe, 2fa, otp, human-in-the-loop]
version: "1.0"
---

# Skill: Stripe 2FA Handler

## Trigger

Use when Stripe (or any service) presents an OTP / SMS verification screen
that requires a code sent to a phone you don't have access to.

## Recognition

`browser_snapshot()` followed by `browser_vision("is there an OTP or SMS code input visible?")`
→ If yes, proceed with this skill.

## Steps

1. Take a screenshot with `browser_snapshot()`
2. Call `clarify()` with:
   - A clear description of what is needed
   - The screenshot URL from step 1
   - `timeout=300` (give the human 5 minutes)
3. Immediately call `hibernate(wake_on_webhook=<clarify_webhook_key>, keep_browser_session=<session_id>)`
4. On wake-up, the human's OTP will be in the wake prompt
5. `browser_type()` the OTP into the input field
6. Continue the original workflow

## Notes

- Never attempt to guess or brute-force an OTP — you will get locked out
- The `keep_browser_session` parameter preserves the Browserbase session including any cookies/auth state
- If the OTP has expired by the time you wake, call `clarify()` again requesting a resend
