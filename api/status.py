"""Plugin API handler: DeepSeek balance status.

Route: /api/plugins/deepseek_credit_monitor/status (GET or POST)
Returns normalized balance data. Never returns credentials.
"""

from datetime import datetime, timezone

from helpers.api import ApiHandler, Input, Output, Request

from usr.plugins.deepseek_credit_monitor.helpers import state
from usr.plugins.deepseek_credit_monitor.helpers.deepseek_client import (
    DeepSeekError,
    fetch_balance,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_threshold(balance: float | None, currency: str, target: str, low: float, critical: float) -> str:
    """Map a balance to a display state, only within one currency.

    Thresholds are defined in the configured display currency. When the
    balance entry speaks a different currency, the status is 'unknown'
    instead of comparing values across currencies.
    """
    if balance is None:
        return "unknown"
    if currency != target:
        return "unknown"
    if balance <= critical:
        return "critical"
    if balance <= low:
        return "warning"
    return "normal"


def _build_payload(settings: dict, data: dict) -> dict:
    balances = data.get("balance_infos", [])
    target = settings["balance_currency"]

    # Display priority: configured currency first, else the first entry.
    primary = next((b for b in balances if b["currency"] == target), balances[0] if balances else None)
    primary_balance = primary.get("total_balance") if primary else None
    primary_currency = primary.get("currency") if primary else None

    available = bool(data.get("is_available"))
    if not available:
        # API explicitly reports the account as not available.
        threshold = "unavailable"
    else:
        threshold = _classify_threshold(
            primary_balance,
            primary_currency or "",
            target,
            settings["low_balance_warning"],
            settings["critical_balance_warning"],
        )

    return {
        "provider": "DeepSeek",
        "configured": True,
        "available": available,
        "status": threshold,
        "show_balance_in_ui": settings["show_balance_in_ui"],
        "balance_infos": balances,
        "display_currency": target,
        "primary_balance": primary_balance,
        "primary_currency": primary_currency,
        "updated_at": _iso_now(),
        "refresh_interval_seconds": settings["refresh_interval_seconds"],
    }


def _base_payload(settings: dict) -> dict:
    return {
        "provider": "DeepSeek",
        "configured": bool(settings.get("api_key")),
        "available": False,
        "status": "not_configured" if not settings.get("api_key") else "unavailable",
        "show_balance_in_ui": settings.get("show_balance_in_ui", True),
        "balance_infos": [],
        "display_currency": settings.get("balance_currency", state.DEFAULT_CURRENCY),
        "primary_balance": None,
        "primary_currency": None,
        "updated_at": _iso_now(),
        "refresh_interval_seconds": settings.get("refresh_interval_seconds", state.DEFAULT_INTERVAL),
    }


class Status(ApiHandler):
    """Return the current DeepSeek account balance status (monitor only)."""

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        # Read-only status; keep auth but skip CSRF so GET polling works.
        return False

    async def process(self, input: Input, request: Request) -> Output:
        try:
            settings = state.load_settings()
        except Exception:
            # Never break Agent Zero because of a config read.
            return {
                "provider": "DeepSeek",
                "configured": False,
                "available": False,
                "status": "not_configured",
                "error": "settings_unavailable",
                "balance_infos": [],
                "updated_at": _iso_now(),
                "refresh_interval_seconds": state.DEFAULT_INTERVAL,
            }

        # Manual refresh bypasses the cache.
        force = bool((input or {}).get("force")) if isinstance(input, dict) else False

        # Serve from in-memory cache when fresh enough.
        cached = state.get_cached_success(max_age=settings["refresh_interval_seconds"])
        if cached and not force:
            cached["cached"] = True
            # Refresh show/hide flag, thresholds and currency from current settings.
            cached["show_balance_in_ui"] = settings["show_balance_in_ui"]
            cached["refresh_interval_seconds"] = settings["refresh_interval_seconds"]
            target = settings["balance_currency"]
            cached["display_currency"] = target
            balances = cached.get("balance_infos") or []
            primary = next((b for b in balances if b.get("currency") == target), balances[0] if balances else None)
            primary_balance = primary.get("total_balance") if primary else None
            primary_currency = primary.get("currency") if primary else None
            cached["primary_balance"] = primary_balance
            cached["primary_currency"] = primary_currency
            if not cached.get("available", False):
                cached["status"] = "unavailable"
            else:
                cached["status"] = _classify_threshold(
                    primary_balance,
                    primary_currency or "",
                    target,
                    settings["low_balance_warning"],
                    settings["critical_balance_warning"],
                )
            return cached

        # Not configured: no API key present.
        if not settings["api_key"]:
            payload = _base_payload(settings)
            payload["status"] = "not_configured"
            return payload

        # Fetch fresh balance data from DeepSeek.
        try:
            data = await fetch_balance(settings["api_key"])
        except DeepSeekError as exc:
            error_reason = exc.reason
        except Exception:
            error_reason = "unexpected_error"
        else:
            payload = _build_payload(settings, data)
            state.set_last_success(payload)
            payload["cached"] = False
            return payload

        # Failure path: keep last success (stale) when available.
        state.set_last_error(error_reason)
        stale = state.get_cached_success()
        if stale:
            stale["cached"] = True
            stale["stale"] = True
            stale["error"] = error_reason or "unavailable"
            stale["show_balance_in_ui"] = settings["show_balance_in_ui"]
            stale["refresh_interval_seconds"] = settings["refresh_interval_seconds"]
            return stale

        payload = _base_payload(settings)
        payload["configured"] = True
        payload["error"] = error_reason or "unavailable"
        return payload
