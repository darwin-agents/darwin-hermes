import httpx
import time


class BrowserbaseClient:
    def __init__(self, api_key: str = "", project_id: str = ""):
        self.api_key = api_key
        self.project_id = project_id
        self._base = "https://api.browserbase.com/v1"

    async def fetch(self, url: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base}/fetch",
                json={"url": url, "projectId": self.project_id},
                headers={"X-BB-API-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json()

    async def create_session(self, url: str = None) -> dict:
        body = {"projectId": self.project_id}
        if url:
            body["startUrl"] = url
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base}/sessions",
                json=body,
                headers={"X-BB-API-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json()

    async def resume_session(self, session_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._base}/sessions/{session_id}",
                headers={"X-BB-API-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json()

    async def screenshot(self, session_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._base}/sessions/{session_id}/screenshot",
                headers={"X-BB-API-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.content

    async def click(self, session_id: str, x: int, y: int) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base}/sessions/{session_id}/click",
                json={"x": x, "y": y},
                headers={"X-BB-API-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json()

    async def type(self, session_id: str, x: int, y: int, text: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base}/sessions/{session_id}/type",
                json={"x": x, "y": y, "text": text},
                headers={"X-BB-API-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json()


def build_browser_tools(settings, db, telemetry):
    async def _backend_get(path: str, params: dict = None) -> dict:
        url = settings.backend_url.rstrip("/") + "/api/v1" + path
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {settings.agent_jwt}"},
            )
            resp.raise_for_status()
            return resp.json()

    _bb = BrowserbaseClient(
        api_key=settings.browserbase_api_key,
        project_id=settings.browserbase_project_id,
    )

    async def browser_fetch(url: str) -> str:
        """
        Fetch the text content of a URL using a lightweight, stealthy HTTP request.
        Use this for: reading articles, scraping text-heavy pages, querying public APIs,
        reading RSS feeds, checking prices on simple HTML pages.

        Do NOT use for: JavaScript-heavy apps, pages requiring login, checkouts,
        anything requiring a visual interaction.

        Cost: ~$0.001 per call (text only, no vision tokens)

        Args:
            url: Full URL to fetch (must begin with https://)

        Returns: Markdown-formatted page content wrapped in <external_data> tags
        """
        bb_resp = await _bb.fetch(url)
        markdown = bb_resp.get("markdown", bb_resp.get("content", ""))
        await telemetry.push_event("BROWSER_FETCH", {
            "url": url,
            "content_length": len(markdown),
            "status": "success",
        })
        return f"<external_data source='{url}'>\n{markdown}\n</external_data>"

    async def browser_session(url: str = None, existing_session_id: str = None) -> dict:
        """
        Open a full stealth browser session for visual interaction.
        Use for: logging into accounts, completing checkouts, posting content,
        any page requiring JavaScript execution or user interaction.

        Cost: ~$0.05 per session (proxy bandwidth + session time)

        Args:
            url: URL to open (for new sessions)
            existing_session_id: Resume a session kept alive from a previous run

        Returns: { "session_id": str, "url": str }
        """
        if existing_session_id:
            session = await _bb.resume_session(existing_session_id)
        else:
            session = await _bb.create_session(url)
        await telemetry.push_event("BROWSER_SESSION_OPEN", {
            "session_id": session["id"],
            "url": session.get("url", url or ""),
            "resumed": existing_session_id is not None,
        })
        return {"session_id": session["id"], "url": session.get("url", url or "")}

    async def take_screenshot(session_id: str) -> dict:
        """
        Capture a screenshot of the current browser viewport.
        Always call this before click_element or type_text to understand what is on screen.

        Returns: { "screenshot_url": str, "session_id": str }
        The screenshot_url can be passed to ask_human() for context.
        """
        img_bytes = await _bb.screenshot(session_id)
        upload_url_resp = await _backend_get(
            "/assets/presigned-url",
            params={
                "darwin_id": settings.darwin_id,
                "filename": f"screenshot_{session_id}_{int(time.time())}.jpg",
                "content_type": "image/jpeg",
            },
        )
        async with httpx.AsyncClient() as client:
            await client.put(
                upload_url_resp["upload_url"],
                content=img_bytes,
                headers={"Content-Type": "image/jpeg"},
            )
        screenshot_url = upload_url_resp["public_url"]
        await telemetry.push_event("BROWSER_SCREENSHOT", {
            "screenshot_url": screenshot_url,
            "session_id": session_id,
            "coordinates_mapped": False,
        })
        return {"screenshot_url": screenshot_url, "session_id": session_id}

    async def click_element(
        session_id: str,
        x: int,
        y: int,
        description: str,
    ) -> dict:
        """
        Click at a specific pixel coordinate in the browser.
        Always take a screenshot first to identify the correct coordinates.

        Args:
            session_id: Active Browserbase session ID
            x: Horizontal pixel coordinate
            y: Vertical pixel coordinate
            description: What you are clicking (for the canvas log)

        Returns: { "success": bool, "url_after": str }
        """
        result = await _bb.click(session_id, x=x, y=y)
        status = "success" if result.get("success") else "failure"
        await telemetry.push_event("BROWSER_CLICK", {
            "session_id": session_id,
            "x": x,
            "y": y,
            "description": description,
            "status": status,
        })
        return result

    async def type_text(
        session_id: str,
        x: int,
        y: int,
        text: str,
        field_description: str,
    ) -> dict:
        """
        Click a text field at (x, y) and type text into it.
        Never use this for credit card numbers — use inject_payment() instead.

        Args:
            session_id: Active Browserbase session ID
            x, y: Coordinates of the input field (from screenshot analysis)
            text: Text to type
            field_description: What this field is (for the canvas log — never log sensitive values)

        Returns: { "success": bool }
        """
        result = await _bb.type(session_id, x=x, y=y, text=text)
        await telemetry.push_event("BROWSER_TYPE", {
            "session_id": session_id,
            "field_description": field_description,
            "status": "success" if result.get("success") else "failure",
        })
        return result

    return [browser_fetch, browser_session, take_screenshot, click_element, type_text]
