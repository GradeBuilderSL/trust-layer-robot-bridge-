"""Unit tests for bridge.watchdog.EdgeWatchdog.

Drives ``_check()`` directly rather than starting the background
thread — that keeps each test deterministic and fast. Two helpers
(``_fast_forward`` and ``_reset_grace``) let us move the virtual
wall clock forward by manipulating the watchdog's internal start
timestamp.
"""
from __future__ import annotations

import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

from bridge.watchdog import EdgeWatchdog


# ── Fixtures ────────────────────────────────────────────────────────────────


def _no_grace_watchdog(timeout_ms: int = 500, **kwargs) -> EdgeWatchdog:
    """Watchdog with zero startup grace so _check fires immediately."""
    wd = EdgeWatchdog(timeout_ms=timeout_ms, grace_ms=0, **kwargs)
    return wd


# ── Basics ──────────────────────────────────────────────────────────────────


def test_initial_state_not_in_fallback():
    wd = _no_grace_watchdog()
    assert wd.in_fallback is False
    status = wd.status()
    assert status["in_fallback"] is False
    assert status["timeout_ms"] == 500
    assert status["last_beat_age_ms"] is None


def test_heartbeat_records_time():
    wd = _no_grace_watchdog()
    wd.heartbeat()
    status = wd.status()
    # Age right after beat should be very small.
    assert status["last_beat_age_ms"] is not None
    assert status["last_beat_age_ms"] < 50


def test_no_beat_triggers_fallback():
    """After timeout_ms with no heartbeat, _check() flips to fallback."""
    wd = _no_grace_watchdog(timeout_ms=100)
    time.sleep(0.15)
    wd._check()
    assert wd.in_fallback is True


def test_beat_before_timeout_prevents_fallback():
    wd = _no_grace_watchdog(timeout_ms=200)
    wd.heartbeat()
    time.sleep(0.1)
    wd._check()
    assert wd.in_fallback is False


# ── Recovery ──────────────────────────────────────────────────────────────


def test_heartbeat_recovers_from_fallback():
    wd = _no_grace_watchdog(timeout_ms=100)
    time.sleep(0.15)
    wd._check()
    assert wd.in_fallback is True
    wd.heartbeat()
    assert wd.in_fallback is False


def test_on_fallback_callback_fires_once():
    calls = {"n": 0}

    def fired():
        calls["n"] += 1

    wd = _no_grace_watchdog(timeout_ms=100, on_fallback=fired)
    time.sleep(0.15)
    wd._check()
    wd._check()  # second check — still in fallback, must not re-fire
    assert calls["n"] == 1


def test_on_recover_callback_fires_on_heartbeat():
    calls = {"n": 0}

    def recovered():
        calls["n"] += 1

    wd = _no_grace_watchdog(timeout_ms=100, on_recover=recovered)
    time.sleep(0.15)
    wd._check()
    wd.heartbeat()
    assert calls["n"] == 1


def test_on_recover_does_not_fire_without_prior_fallback():
    calls = {"n": 0}

    def recovered():
        calls["n"] += 1

    wd = _no_grace_watchdog(timeout_ms=500, on_recover=recovered)
    wd.heartbeat()
    wd.heartbeat()
    wd.heartbeat()
    assert calls["n"] == 0


def test_fallback_callback_exception_is_swallowed():
    def boom():
        raise RuntimeError("crash")

    wd = _no_grace_watchdog(timeout_ms=100, on_fallback=boom)
    time.sleep(0.15)
    # Must not raise — exception inside callback is logged and ignored.
    wd._check()
    assert wd.in_fallback is True


# ── Grace period ──────────────────────────────────────────────────────────


def test_grace_period_suppresses_fallback_at_startup():
    wd = EdgeWatchdog(timeout_ms=50, grace_ms=500)
    time.sleep(0.1)  # well past timeout, but inside grace window
    wd._check()
    assert wd.in_fallback is False


def test_grace_expires_and_fallback_fires():
    wd = EdgeWatchdog(timeout_ms=50, grace_ms=100)
    time.sleep(0.2)  # past both the timeout and the grace window
    wd._check()
    assert wd.in_fallback is True


# ── Status dict ──────────────────────────────────────────────────────────


def test_status_reports_age():
    wd = _no_grace_watchdog()
    wd.heartbeat()
    time.sleep(0.05)
    status = wd.status()
    assert status["last_beat_age_ms"] >= 30
    assert status["last_beat_age_ms"] < 200


def test_status_reports_timeout_value():
    wd = _no_grace_watchdog(timeout_ms=1234)
    assert wd.status()["timeout_ms"] == 1234


# ── start() reentrancy ──────────────────────────────────────────────────


def test_start_twice_does_not_spawn_second_thread():
    wd = _no_grace_watchdog()
    wd.start()
    t1 = wd._thread
    wd.start()
    t2 = wd._thread
    assert t1 is t2
    assert t1.is_alive()


def test_background_poll_runs_heartbeat_check():
    """Spawn the real thread with a short timeout and verify the
    background loop flips to fallback without manual _check calls."""
    wd = _no_grace_watchdog(timeout_ms=100)
    wd.start()
    time.sleep(0.5)  # plenty of time for 2+ poll iterations
    assert wd.in_fallback is True


def test_heartbeat_after_background_fallback_recovers():
    wd = _no_grace_watchdog(timeout_ms=100)
    wd.start()
    time.sleep(0.3)
    assert wd.in_fallback is True
    wd.heartbeat()
    assert wd.in_fallback is False
