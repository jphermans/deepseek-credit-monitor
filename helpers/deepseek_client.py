"""Server-side DeepSeek HTTP client for the Credit Monitor plugin.

All requests to DeepSeek happen here, inside the Agent Zero framework
runtime, using async httpx so polls never block the event loop.
Credentials never reach browser-side JavaScript.

Endpoint (official docs: https://api-docs.deepseek.com/api/get-user-balance/):
  GET https://api.deepseek.com/user/balance
  Authorization: Bearer <DEEPSEEK_API_KEY>

Response shape:
  {
    "is_available": true,
    "balance_infos": [
      {
        "currency": "CNY",
        "total_balance": "110.00",
        "granted_balance": "10.00",
        "topped_up_balance": "100.00"
      }
    ]
  }

Balance values are strings; granted_balance and topped_up_balance may be
"-" when not applicable. Multiple currencies may be returned.
"""

from typing import Any

import httpx

BALANCE_URL = "https://api.deepseek.com/user/balance"

# Reasonable bounded timeouts (connect, read, write, pool)
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class DeepSeekError(Exception):
    """Base error for DeepSeek communication failures."""

    def __init__(self, reason: str, detail: str = "", status_code: int | None = None):
        self.reason = reason
        self.detail = detail
        self.status_code = status_code
        super().__init__(reason)


class AuthenticationError(DeepSeekError):
    """Raised for HTTP 401/403 responses."""


class RateLimitError(DeepSeekError):
    """Raised for HTTP 429 responses."""


class ServerError(DeepSeekError):
    """Raised for HTTP 5xx responses."""


def _http_error(status_code: int, body: str) -> DeepSeekError:
    detail = (body or "").strip()[:300]
    if status_code in (401, 403):
        return AuthenticationError("authentication_failed", detail=detail, status_code=status_code)
    if status_code == 429:
        return RateLimitError("rate_limited", detail=detail, status_code=status_code)
    if status_code >= 500:
        return ServerError("server_error", detail=detail, status_code=status_code)
    return DeepSeekError(f"http_error_{status_code}", detail=detail, status_code=status_code)


def _classify_request_error(error: Exception) -> DeepSeekError:
    if isinstance(error, httpx.TimeoutException):
        return DeepSeekError("timeout", detail=str(error)[:200])
    if isinstance(error, httpx.ConnectError):
        return DeepSeekError("connect_failed", detail=str(error)[:200])
    if isinstance(error, httpx.HTTPError):
        return DeepSeekError("network_error", detail=str(error)[:200])
    return DeepSeekError("unexpected_error", detail=str(error)[:200])


def _get_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception as exc:  # malformed JSON
        raise DeepSeekError("malformed_json", detail=str(exc)[:200]) from exc
    if not isinstance(data, dict):
        raise DeepSeekError("unexpected_response", detail="top-level JSON is not an object")
    return data


def _to_float(value: Any) -> float | None:
    """Balance values arrive as strings; '-' (or empty) means not applicable."""
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _normalize_balances(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalize balance_infos entries; unknown shapes are skipped."""
    entries: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return entries
    for item in raw:
        if not isinstance(item, dict):
            continue
        currency = str(item.get("currency") or "").strip().upper()
        if not currency:
            continue
        entries.append(
            {
                "currency": currency,
                "total_balance": _to_float(item.get("total_balance")),
                "granted_balance": _to_float(item.get("granted_balance")),
                "topped_up_balance": _to_float(item.get("topped_up_balance")),
            }
        )
    return entries


async def _get(url: str, bearer: str) -> httpx.Response:
    """Async GET with Bearer auth; normalizes transport errors."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {bearer}",
                    "Accept": "application/json",
                },
            )
    except Exception as exc:
        raise _classify_request_error(exc) from exc


async def fetch_balance(api_key: str) -> dict[str, Any]:
    """GET /user/balance with the DeepSeek API key.

    Returns {"is_available": bool, "balance_infos": [normalized entries]}.
    Raises DeepSeekError subclasses on failure.
    """
    response = await _get(BALANCE_URL, api_key)

    if response.status_code != 200:
        raise _http_error(response.status_code, response.text)

    data = _get_json(response)
    is_available = data.get("is_available") is True
    balances = _normalize_balances(data.get("balance_infos"))
    if not balances:
        raise DeepSeekError("unexpected_response", detail="balance_infos missing or empty")
    return {"is_available": is_available, "balance_infos": balances}
