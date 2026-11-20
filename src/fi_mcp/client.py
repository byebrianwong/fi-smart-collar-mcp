"""A small async client for the Fi GraphQL API.

What it does:
- Logs in with email and password. Fi answers with a session cookie, which
  httpx keeps for later requests.
- Sends GraphQL queries as POST requests.
- Logs in again once if Fi says the session has expired.
- Keeps answers in memory for a few minutes, so that several tool calls in a
  row do not each hit Fi's servers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from . import queries

log = logging.getLogger(__name__)

USER_AGENT = "fi-mcp/0.1 (personal read-only client)"

# Words in a GraphQL error message that mean "not allowed" rather than
# "bad query". Fi returns HTTP 200 with these in the errors list.
_AUTH_WORDS = ("unauthorized", "unauthenticated", "authentication", "not logged in")


class FiError(RuntimeError):
    """Fi returned an error or an answer we could not read."""


class FiLoginError(FiError):
    """The email or password was refused."""


class FiNotAuthorized(FiError):
    """Fi refused one query while the login itself worked.

    This happens with the health trends query on collars or plans that do not
    include behavior tracking.
    """


class FiClient:
    def __init__(
        self,
        email: str,
        password: str,
        *,
        cache_seconds: float = 300,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._login_lock = asyncio.Lock()
        self._logged_in = False
        self._http = httpx.AsyncClient(
            base_url=queries.API_HOST,
            timeout=timeout,
            headers={"user-agent": USER_AGENT},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def login(self) -> dict[str, Any]:
        async with self._login_lock:
            self._http.cookies.clear()
            resp = await self._http.post(
                queries.LOGIN_PATH,
                data={"email": self._email, "password": self._password},
            )
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code == 401:
                # Fi sends 401 "Unauthorized" for any wrong email/password pair.
                raise FiLoginError("Fi says the email or password is wrong (HTTP 401).")
            if resp.status_code >= 400 or "error" in body or "userId" not in body:
                message = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
                raise FiLoginError(
                    f"Fi refused the login (HTTP {resp.status_code})"
                    + (f": {message}" if message else ".")
                )
            self._logged_in = True
            return {"userId": body.get("userId")}

    async def query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Run a GraphQL query and return its `data` object."""
        key = query + "\n" + json.dumps(variables or {}, sort_keys=True)
        if use_cache:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < self._cache_seconds:
                return hit[1]

        if not self._logged_in:
            await self.login()

        try:
            data = await self._post(query, variables)
        except FiNotAuthorized:
            # The session may have expired. Log in again and retry once. If it
            # still fails, the refusal is real and the caller gets it.
            log.info("Fi refused a query; logging in again and retrying once")
            await self.login()
            data = await self._post(query, variables)

        self._cache[key] = (time.monotonic(), data)
        return data

    async def _post(self, query: str, variables: dict[str, Any] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = await self._http.post(queries.GRAPHQL_PATH, json=payload)

        if resp.status_code in (401, 403):
            raise FiNotAuthorized(f"HTTP {resp.status_code} from Fi")
        try:
            body = resp.json()
        except ValueError as exc:
            raise FiError(f"Fi sent a non-JSON answer (HTTP {resp.status_code})") from exc

        errors = body.get("errors") or []
        if errors:
            message = "; ".join(e.get("message", "unknown error") for e in errors)
            if any(word in message.lower() for word in _AUTH_WORDS):
                raise FiNotAuthorized(message)
            raise FiError(f"Fi GraphQL error: {message}")
        if resp.status_code >= 400:
            raise FiError(f"Fi returned HTTP {resp.status_code}")
        data = body.get("data")
        if data is None:
            raise FiError("Fi answer had no data")
        return data
