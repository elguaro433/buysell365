"""
BuySell365 Pro — Heartbeat Helper
=================================
Provides a simple, reliable liveness signal for bot.py and signal_copier.py
that the launcher's watchdog reads to decide whether each process is alive.

Why heartbeat instead of just PID files:
  - PID files only tell you a process EXISTS, not that it's HEALTHY.
  - A process can be hung (waiting on lock, deadlocked, blocked on I/O)
    with a valid PID but doing nothing useful — heartbeat catches this.
  - On Windows, PIDs can be reused by other programs after termination.
  - PID file writes are not atomic (race with watchdog reads).

Heartbeat format:
  File contains a single Unix timestamp (float, seconds since epoch).
  Written atomically (write to .tmp, then os.replace).

Watchdog rule:
  - If file mtime AND content timestamp are both <90s old → alive.
  - If either >90s → process hung or dead → restart.

Usage:
  from heartbeat import start_heartbeat
  start_heartbeat(".bot.heartbeat")   # spawns daemon thread, returns immediately
"""
import os
import time
import threading
import logging

_log = logging.getLogger("BuySell365.Heartbeat")

# Default interval: write every 20s. Watchdog tolerates up to 90s old.
# This gives ~4 writes worth of slack before declaring dead — robust against
# transient I/O hiccups, GC pauses, or temporary main-thread blocks.
DEFAULT_INTERVAL = 20

_threads_started = {}  # filename -> Thread (no double start)


def _heartbeat_loop(path: str, interval: int):
    """Daemon loop that writes timestamp to path every `interval` seconds.

    FIX 2026-04-28d: tolerante a WinError 32 (race con watchdog leyendo el
    archivo mientras hacemos os.replace). Retry hasta 5 veces con backoff
    exponencial corto. Si todos fallan, log a debug (ruido innecesario en
    warning) y continua — el siguiente tick lo arregla.
    """
    while True:
        _written = False
        for _att in range(5):
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
                os.replace(tmp, path)  # atomic replace
                _written = True
                break
            except OSError as e:
                # WinError 32 (file in use), 5 (access denied), 13 (permission)
                # son transientes en Windows — pequena pausa y reintento.
                if _att < 4:
                    time.sleep(0.05 * (_att + 1))  # 50ms, 100ms, 150ms, 200ms
                else:
                    _log.debug(f"Heartbeat write retry exhausted for {path}: {e}")
            except Exception as e:
                _log.debug(f"Heartbeat write error {path}: {e}")
                break
        # Continue loop regardless — next tick will retry
        time.sleep(interval)


def start_heartbeat(path: str, interval: int = DEFAULT_INTERVAL) -> threading.Thread:
    """Spawn a daemon thread that maintains a heartbeat file.

    Args:
        path: absolute or relative path to heartbeat file.
        interval: seconds between writes (default 20).

    Returns:
        The thread object (already running). Idempotent — calling twice with
        the same path returns the existing thread.
    """
    if path in _threads_started:
        existing = _threads_started[path]
        if existing.is_alive():
            return existing
    t = threading.Thread(
        target=_heartbeat_loop, args=(path, interval),
        daemon=True, name=f"heartbeat-{os.path.basename(path)}",
    )
    t.start()
    _threads_started[path] = t
    # Write first beat synchronously so watchdog never sees a stale/missing file
    # at startup race. FIX 2026-04-28d: retry tolerante para WinError 32.
    for _att_init in range(5):
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
            os.replace(tmp, path)
            break
        except OSError:
            if _att_init < 4:
                time.sleep(0.05 * (_att_init + 1))
        except Exception:
            break
    _log.info(f"Heartbeat started: {path} (every {interval}s)")
    return t


def is_alive_by_heartbeat(path: str, max_age: int = 90) -> bool:
    """Returns True if the heartbeat file exists and is recent (<max_age seconds).

    Used by the watchdog to determine if the process is alive AND responsive.
    Reads timestamp from file content; falls back to mtime if content is bad.
    """
    if not os.path.exists(path):
        return False
    now = time.time()
    # 1) Try content-based check (more authoritative than mtime)
    try:
        with open(path, "r", encoding="utf-8") as f:
            ts = float(f.read().strip())
        if (now - ts) < max_age:
            return True
    except Exception:
        pass
    # 2) Fallback to mtime (in case write was partial / corrupt)
    try:
        mt = os.path.getmtime(path)
        if (now - mt) < max_age:
            return True
    except Exception:
        pass
    return False
