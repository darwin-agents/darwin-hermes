"""
Files tool — darwin_tools/files.py

upload_file: request a presigned URL from the backend, upload directly
to storage (no file content goes through backend RAM), then index it.
The file appears in the frontend's Assets tab for the creator to download.
"""

from __future__ import annotations

from pathlib import Path
import httpx


def register_files_tools(ctx) -> None:
    ctx.register_tool("upload_file", _schema_upload_file, _handle_upload_file)


_schema_upload_file = {
    "name": "upload_file",
    "description": (
        "Upload a file you have generated (CSV, PDF, image, report) to permanent storage. "
        "The file appears in the frontend's Assets tab for your creator to download."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Local path to the file inside the container",
            },
            "description": {
                "type": "string",
                "description": "Human-readable description of the file contents",
            },
            "content_type": {
                "type": "string",
                "default": "application/octet-stream",
                "description": "MIME type (e.g. 'text/csv', 'application/pdf')",
            },
        },
        "required": ["filepath", "description"],
    },
}


async def _handle_upload_file(params: dict) -> dict:
    from .backend_client import get_backend_client
    client = get_backend_client()

    filepath = params["filepath"]
    description = params["description"]
    content_type = params.get("content_type", "application/octet-stream")
    filename = Path(filepath).name

    url_resp = await client.get("/assets/presigned-url", params={
        "darwin_id": client.darwin_id,
        "filename": filename,
        "content_type": content_type,
    })

    async with httpx.AsyncClient(timeout=30.0) as http:
        with open(filepath, "rb") as f:
            await http.put(
                url_resp["upload_url"],
                content=f.read(),
                headers={"Content-Type": content_type},
            )

    await client.post("/assets/index", {
        "asset_id": url_resp["asset_id"],
        "darwin_id": client.darwin_id,
        "description": description,
    })
    return {"asset_id": url_resp["asset_id"], "public_url": url_resp["public_url"]}
