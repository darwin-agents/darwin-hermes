#!/usr/bin/env python3
"""
Local Darwin smoke test runner.

Runs one deterministic end-to-end flow against a local backend:
  1) check_ledger
  2) execute_code("echo local-smoke-ok")
  3) hibernate(wake_after_hours=0.02)

Exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys
import uuid
import json
import hmac
import hashlib
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mint_hs256_jwt(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':'), sort_keys=True).encode('utf-8'))}."
        f"{_b64url(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8'))}"
    )
    sig = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def main() -> int:
    agent_dir = Path(__file__).resolve().parents[1]
    repo = agent_dir.parent
    backend_dir = repo / "darwin-backend"

    b_env = _read_env(backend_dir / ".env")
    a_env = _read_env(agent_dir / ".env")

    darwin_id = os.environ.get("SMOKE_DARWIN_ID", "554c01fe-8db7-4065-bb60-02139d9b9b49")
    session_id = str(uuid.uuid4())
    backend_port = os.environ.get("SMOKE_BACKEND_PORT", "8010")
    backend_url = f"http://127.0.0.1:{backend_port}"

    secret = b_env.get("AGENT_JWT_SECRET")
    if not secret:
        raise SystemExit("AGENT_JWT_SECRET missing in darwin-backend/.env")

    now = datetime.now(UTC)
    agent_jwt = _mint_hs256_jwt(
        payload={
            "darwin_id": darwin_id,
            "session_id": session_id,
            "type": "agent",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=12)).timestamp()),
        },
        secret=secret,
    )

    openrouter_key = (
        os.environ.get("OPENROUTER_API_KEY")
        or b_env.get("OPENROUTER_API_KEY_2")
        or b_env.get("OPENROUTER_API_KEY_1")
        or a_env.get("OPENROUTER_API_KEY")
    )
    if not openrouter_key:
        raise SystemExit("No OpenRouter key found (OPENROUTER_API_KEY or backend OPENROUTER_API_KEY_{1,2})")

    for k, v in a_env.items():
        os.environ.setdefault(k, v)

    hermes_home = Path("/tmp") / f"darwin-local-smoke-{darwin_id}"
    hermes_home.mkdir(parents=True, exist_ok=True)

    tpl = (agent_dir / "config" / "config.yaml.j2").read_text()
    rendered = (
        tpl.replace("{{ darwin_id }}", darwin_id)
        .replace("{{ session_id }}", session_id)
        .replace("/root/darwin_skills", str(agent_dir / "darwin_skills"))
    )
    (hermes_home / "config.yaml").write_text(rendered)
    (hermes_home / "SOUL.md").write_text((agent_dir / "prompts" / "SOUL.md").read_text())

    os.environ["HERMES_HOME"] = str(hermes_home)
    os.environ["DARWIN_ID"] = darwin_id
    os.environ["DARWIN_SESSION_ID"] = session_id
    os.environ["DARWIN_AGENT_JWT"] = agent_jwt
    os.environ["DARWIN_BACKEND_URL"] = backend_url
    os.environ["DARWIN_WAKE_PROMPT"] = "Local smoke test run"
    os.environ["TERMINAL_ENV"] = "local"
    os.environ["OPENROUTER_API_KEY"] = openrouter_key
    os.environ["OPENROUTER_MODEL"] = os.environ.get("SMOKE_MODEL", "openai/gpt-4o-mini")
    os.environ["OPENROUTER_MAX_TOKENS"] = "1024"
    os.environ["DARWIN_HERMES_VERBOSE"] = "1"

    sys.path.insert(0, str(agent_dir))
    sys.path.insert(0, str(agent_dir / "hermes-core"))

    from darwin_tools.lifecycle import DarwinHibernateSignal
    from modal_app import _run_session_via_aiagent

    prompt = (
        "Local smoke test. Mandatory sequence only: "
        "1) call check_ledger; "
        "2) call execute_code with command 'echo local-smoke-ok'; "
        "3) call hibernate(reason='local smoke complete', wake_after_hours=0.02)."
    )

    print(f"SMOKE darwin_id={darwin_id} session_id={session_id} backend={backend_url}")
    try:
        _run_session_via_aiagent(prompt)
        raise SystemExit("SMOKE FAILED: session returned without hibernate signal")
    except DarwinHibernateSignal as sig:
        print("SMOKE PASSED: hibernate signal raised")
        print(f"SMOKE wake_after_hours={sig.wake_after_hours}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
