---
name: browser-screenshot-loop
description: Standard vision-locate-click loop for any web interaction
tags: [browser, vision, navigation]
version: "1.0"
---

# Skill: Browser Screenshot → Vision → Click Loop

## Trigger

Use for any visual interaction where you need to locate and click an element
whose position you don't know in advance.

## The Pattern

```
browser_navigate(url)          → open the page
browser_snapshot()             → capture state
browser_vision("locate X")     → get coordinates
browser_click(x, y)            → interact
browser_snapshot()             → verify result
```

## Steps

1. `browser_navigate(url)` if not already on target page
2. `browser_snapshot()` → note full screenshot in context
3. `browser_vision(prompt)` where prompt describes exactly what to find:
   - Good: "Find the blue 'Sign Up' button and return its pixel coordinates"
   - Bad: "Find the button"
4. Parse the (x, y) coordinates from the vision response
5. `browser_click(x=X, y=Y)` — use exact pixel values
6. `browser_snapshot()` to confirm the action worked (page changed, modal opened, etc.)
7. If result is unexpected, repeat from step 3 with an updated prompt

## Notes

- Always verify with a second snapshot after clicking — never assume click succeeded
- For text input fields: use `browser_type()` after clicking the field
- For payment fields: use `inject_payment()` — never `browser_type()`
- If the element is not visible in the viewport, ask vision to scroll first
