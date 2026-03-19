"""
BuySell365 Pro — Web Sync Module
Sends bot state to the web app every SYNC_INTERVAL seconds.
Picks up pending TradingView webhook signals from the web.

Resilience features:
- Exponential backoff on consecutive failures (30s -> 5min)
- Health-check log every 10 minutes
- Warnings throttled to every 10 consecutive failures
"""
import os
import time
import threading
import logging

logger = logging.getLogger("BuySell365.WebSync")

# ============================================================
#  CONFIGURATION
# ============================================================
WEB_URL = os.getenv("WEB_URL", "https://buysell365.pro").rstrip("/")
SYNC_SECRET = os.getenv("SYNC_SECRET", "buysell365_sync_2026").strip()
SYNC_INTERVAL = 30          # base interval on success (seconds)
BACKOFF_MAX = 300            # max interval on failure (5 minutes)
HEALTH_LOG_INTERVAL = 600   # log sync health every 10 minutes
FAILURE_LOG_EVERY = 10       # log a warning every N consecutive failures


def _do_sync(get_state_fn, on_signals_fn):
    """Push current state to web and pick up pending signals.

    Returns True on success, False on failure.
    """
    import requests
    try:
        state = get_state_fn()
        resp = requests.post(
            f"{WEB_URL}/api/sync",
            json=state,
            headers={"X-Sync-Secret": SYNC_SECRET, "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            pending = data.get("pending_signals", [])
            if pending and on_signals_fn:
                for signal in pending:
                    try:
                        on_signals_fn(signal)
                    except Exception as e:
                        logger.error(f"Error processing pending signal: {e}")
            if pending:
                logger.info(f"Web sync OK — {len(pending)} señales enviadas")
            return True
        else:
            # Logged by the caller based on failure count
            return False
    except Exception:
        # Connection errors, timeouts, etc. — logged by the caller
        return False


def start_sync_loop(get_state_fn, on_signals_fn=None):
    """Start background thread that syncs with the web every SYNC_INTERVAL seconds.

    Args:
        get_state_fn: Callable that returns dict with:
            - operaciones_activas
            - historial_operaciones
            - estadisticas_diarias
            - winning_trades
            - bot_active
            - auto_trading
            - assets_count
            - active_ops_detail
        on_signals_fn: Callable(signal_dict) called for each pending webhook signal.
    """
    def _loop():
        logger.info(f"Web sync started -> {WEB_URL} (base interval {SYNC_INTERVAL}s)")
        time.sleep(10)  # Wait for bot to initialize

        current_interval = SYNC_INTERVAL
        consecutive_failures = 0
        total_successes = 0
        total_failures = 0
        last_health_log = time.monotonic()

        while True:
            try:
                success = _do_sync(get_state_fn, on_signals_fn)
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
                success = False

            now = time.monotonic()

            if success:
                # Reset backoff on success
                if consecutive_failures > 0:
                    logger.info(
                        f"Web sync recovered after {consecutive_failures} consecutive failure(s)"
                    )
                consecutive_failures = 0
                current_interval = SYNC_INTERVAL
                total_successes += 1
            else:
                consecutive_failures += 1
                total_failures += 1

                # Exponential backoff: double interval, capped at BACKOFF_MAX
                current_interval = min(current_interval * 2, BACKOFF_MAX)

                # Only log a warning every FAILURE_LOG_EVERY failures
                if consecutive_failures % FAILURE_LOG_EVERY == 1 or consecutive_failures == 1:
                    logger.warning(
                        f"Web sync failing (attempt #{consecutive_failures}, "
                        f"next retry in {current_interval}s)"
                    )

            # Health check log every 10 minutes
            if now - last_health_log >= HEALTH_LOG_INTERVAL:
                status = "healthy" if consecutive_failures == 0 else f"degraded ({consecutive_failures} consecutive failures)"
                logger.info(
                    f"[Health] Web sync {status} | "
                    f"successes={total_successes} failures={total_failures} "
                    f"interval={current_interval}s"
                )
                last_health_log = now

            time.sleep(current_interval)

    t = threading.Thread(target=_loop, daemon=True, name="web_sync")
    t.start()
    return t
