import httpx
from pathlib import Path


def build_files_tools(settings, db, telemetry):
    async def _backend_get(path: str, params: dict = None) -> dict:
        url = settings.backend_url.rstrip("/") + "/api/v1" + path
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {settings.agent_jwt}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def _backend_post(path: str, body: dict) -> dict:
        url = settings.backend_url.rstrip("/") + "/api/v1" + path
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {settings.agent_jwt}"},
            )
            resp.raise_for_status()
            return resp.json()

    async def upload_file(
        filepath: str,
        description: str,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """
        Upload a file you have generated (CSV, PDF, image) to permanent storage.
        The file will appear in the frontend's "Assets" tab for the human creator to download.

        Args:
            filepath: Local path to the file inside the container
            description: Human-readable description of the file contents
            content_type: MIME type (e.g., 'text/csv', 'application/pdf')

        Returns: { "asset_id": str, "public_url": str }
        """
        filename = Path(filepath).name
        url_resp = await _backend_get("/assets/presigned-url", params={
            "darwin_id": settings.darwin_id,
            "filename": filename,
            "content_type": content_type,
        })
        async with httpx.AsyncClient() as client:
            with open(filepath, "rb") as f:
                await client.put(
                    url_resp["upload_url"],
                    content=f.read(),
                    headers={"Content-Type": content_type},
                )
        await _backend_post("/assets/index", {
            "asset_id": url_resp["asset_id"],
            "darwin_id": settings.darwin_id,
            "description": description,
        })
        return {"asset_id": url_resp["asset_id"], "public_url": url_resp["public_url"]}

    return [upload_file]
