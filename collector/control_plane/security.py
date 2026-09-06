from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

ROLE_LEVEL = {"viewer": 10, "operator": 20, "admin": 30}


@dataclass(frozen=True, slots=True)
class Principal:
    name: str
    role: str


def _configured_keys() -> dict[str, str]:
    raw = os.getenv("CONTROL_API_KEYS_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CONTROL_API_KEYS_JSON must be a JSON object mapping API keys to roles") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("CONTROL_API_KEYS_JSON must be a JSON object")
    result = {str(key): str(role).lower() for key, role in payload.items()}
    invalid = sorted(set(result.values()) - set(ROLE_LEVEL))
    if invalid:
        raise RuntimeError(f"unsupported control-plane roles: {', '.join(invalid)}")
    return result


def authenticate(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Principal:  # noqa: B008
    keys = _configured_keys()
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="control plane has no configured credentials",
        )
    supplied = x_api_key or ""
    for candidate, role in keys.items():
        if hmac.compare_digest(candidate, supplied):
            return Principal(name="api-key", role=role)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")


AuthenticatedPrincipal = Annotated[Principal, Depends(authenticate)]


def require_role(required: str):
    if required not in ROLE_LEVEL:
        raise ValueError(f"unknown role {required!r}")

    def dependency(principal: AuthenticatedPrincipal) -> Principal:
        if ROLE_LEVEL[principal.role] < ROLE_LEVEL[required]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{required} role required")
        return principal

    return dependency
