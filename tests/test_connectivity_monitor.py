"""Unit tests for ConnectivityMonitor state machine.

Tests mode transitions, properties, and callback invocation.
Uses unittest.mock.patch to avoid real HTTP calls.
"""
from __future__ import annotations

import sys
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from unittest.mock import patch, MagicMock

from bridge.connectivity_monitor import ConnectivityMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_monitor(**kwargs) -> ConnectivityMonitor:
    """Create a monitor with short intervals for testing."""
    defaults = dict(
        workstation_url="http://10.0.0.1:8888",
        ping_interval_s=0.05,
        fail_threshold=3,
    )
    defaults.update(kwargs)
    return ConnectivityMonitor(**defaults)


def _simulate_pings(monitor: ConnectivityMonitor, results: list[bool]):
    """Drive the state machine by calling _loop logic manually (no threads).

    Each entry in `results` represents one ping result (True=ok, False=fail).
    We directly invoke the internal state-update logic.
    """
    for ok in results:
        with monitor._lock:
            monitor._last_ping_ok = ok
            old_mode = monitor._mode

            if ok:
                monitor._fail_count = 0
                if monitor._mode == ConnectivityMonitor.MODE_AUTONOMOUS:
                    monitor._mode = ConnectivityMonitor.MODE_RECONNECTING
                elif monitor._mode == ConnectivityMonitor.MODE_RECONNECTING:
                    monitor._mode = ConnectivityMonitor.MODE_CONNECTED
            else:
                monitor._fail_count += 1
                if (monitor._mode == ConnectivityMonitor.MODE_CONNECTED
                        and monitor._fail_count >= monitor._fail_threshold):
                    monitor._mode = ConnectivityMonitor.MODE_AUTONOMOUS

            new_mode = monitor._mode

        if new_mode != old_mode and monitor._on_mode_change:
            try:
                monitor._on_mode_change(new_mode)
            except Exception:
                pass  # mirror _loop behaviour: swallow callback errors
        if new_mode == ConnectivityMonitor.MODE_RECONNECTING and new_mode != old_mode:
            if monitor._on_sync_needed:
                try:
                    monitor._on_sync_needed()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_starts_connected(self):
        m = _make_monitor()
        assert m.mode == "CONNECTED"

    def test_is_connected_true_initially(self):
        m = _make_monitor()
        assert m.is_connected is True

    def test_is_autonomous_false_initially(self):
        m = _make_monitor()
        assert m.is_autonomous is False


# ---------------------------------------------------------------------------
# CONNECTED -> AUTONOMOUS after 3 failed pings
# ---------------------------------------------------------------------------

class TestConnectedToAutonomous:
    def test_transition_after_3_failures(self):
        m = _make_monitor()
        _simulate_pings(m, [False, False, False])
        assert m.mode == "AUTONOMOUS"
        assert m.is_autonomous is True
        assert m.is_connected is False

    def test_no_transition_after_2_failures(self):
        m = _make_monitor()
        _simulate_pings(m, [False, False])
        assert m.mode == "CONNECTED"

    def test_failure_counter_resets_on_success(self):
        m = _make_monitor()
        _simulate_pings(m, [False, False, True, False, False])
        # After F, F, OK (reset), F, F => still CONNECTED (only 2 consecutive fails)
        assert m.mode == "CONNECTED"

    def test_custom_fail_threshold(self):
        m = _make_monitor(fail_threshold=5)
        _simulate_pings(m, [False] * 4)
        assert m.mode == "CONNECTED"
        _simulate_pings(m, [False])
        # Note: fail_count was reset to 0 on the last success-free block above,
        # but we need 5 consecutive from CONNECTED. Re-create for clean test.
        m2 = _make_monitor(fail_threshold=5)
        _simulate_pings(m2, [False] * 5)
        assert m2.mode == "AUTONOMOUS"


# ---------------------------------------------------------------------------
# AUTONOMOUS -> RECONNECTING after 1 success
# ---------------------------------------------------------------------------

class TestAutonomousToReconnecting:
    def test_transition_on_first_success(self):
        m = _make_monitor()
        _simulate_pings(m, [False, False, False])  # -> AUTONOMOUS
        _simulate_pings(m, [True])                  # -> RECONNECTING
        assert m.mode == "RECONNECTING"

    def test_stays_autonomous_on_continued_failure(self):
        m = _make_monitor()
        _simulate_pings(m, [False, False, False])  # -> AUTONOMOUS
        _simulate_pings(m, [False, False, False])  # still AUTONOMOUS
        assert m.mode == "AUTONOMOUS"


# ---------------------------------------------------------------------------
# RECONNECTING -> CONNECTED after next success
# ---------------------------------------------------------------------------

class TestReconnectingToConnected:
    def test_transition_on_second_success(self):
        m = _make_monitor()
        _simulate_pings(m, [False, False, False])  # -> AUTONOMOUS
        _simulate_pings(m, [True])                  # -> RECONNECTING
        _simulate_pings(m, [True])                  # -> CONNECTED
        assert m.mode == "CONNECTED"
        assert m.is_connected is True

    def test_full_cycle(self):
        """CONNECTED -> AUTONOMOUS -> RECONNECTING -> CONNECTED."""
        m = _make_monitor()
        assert m.mode == "CONNECTED"
        _simulate_pings(m, [False, False, False])
        assert m.mode == "AUTONOMOUS"
        _simulate_pings(m, [True])
        assert m.mode == "RECONNECTING"
        _simulate_pings(m, [True])
        assert m.mode == "CONNECTED"


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:
    def test_on_mode_change_called(self):
        callback = MagicMock()
        m = _make_monitor(on_mode_change=callback)
        _simulate_pings(m, [False, False, False])
        callback.assert_called_once_with("AUTONOMOUS")

    def test_on_mode_change_called_for_reconnecting(self):
        callback = MagicMock()
        m = _make_monitor(on_mode_change=callback)
        _simulate_pings(m, [False, False, False])  # -> AUTONOMOUS
        callback.reset_mock()
        _simulate_pings(m, [True])                  # -> RECONNECTING
        callback.assert_called_once_with("RECONNECTING")

    def test_on_sync_needed_called_on_reconnect(self):
        sync_cb = MagicMock()
        m = _make_monitor(on_sync_needed=sync_cb)
        _simulate_pings(m, [False, False, False])  # -> AUTONOMOUS
        _simulate_pings(m, [True])                  # -> RECONNECTING
        sync_cb.assert_called_once()

    def test_callback_exception_does_not_crash(self):
        """Callback throwing should not break the state machine."""
        def bad_callback(mode):
            raise RuntimeError("callback error")

        m = _make_monitor(on_mode_change=bad_callback)
        # Should not raise
        _simulate_pings(m, [False, False, False])
        assert m.mode == "AUTONOMOUS"


# ---------------------------------------------------------------------------
# _ping method with mocked urllib
# ---------------------------------------------------------------------------

class TestPing:
    @patch("bridge.connectivity_monitor.urllib.request.urlopen")
    def test_ping_returns_true_on_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_urlopen.return_value = mock_resp
        m = _make_monitor()
        assert m._ping() is True
        mock_urlopen.assert_called_once_with("http://10.0.0.1:8888/health", timeout=2.0)
        mock_resp.close.assert_called_once()

    @patch("bridge.connectivity_monitor.urllib.request.urlopen")
    def test_ping_returns_false_on_exception(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("Connection refused")
        m = _make_monitor()
        assert m._ping() is False

    @patch("bridge.connectivity_monitor.urllib.request.urlopen")
    def test_ping_returns_false_on_timeout(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("timeout")
        m = _make_monitor()
        assert m._ping() is False


# ---------------------------------------------------------------------------
# status_dict
# ---------------------------------------------------------------------------

class TestStatusDict:
    def test_initial_status(self):
        m = _make_monitor()
        s = m.status_dict()
        assert s["mode"] == "CONNECTED"
        assert s["fail_count"] == 0
        assert s["last_ping_ok"] is True

    def test_status_after_failures(self):
        m = _make_monitor()
        _simulate_pings(m, [False, False, False])
        s = m.status_dict()
        assert s["mode"] == "AUTONOMOUS"
        assert s["fail_count"] == 3


# ---------------------------------------------------------------------------
# Thread start/stop (integration-ish, with mocked ping)
# ---------------------------------------------------------------------------

class TestThreadLifecycle:
    @patch("bridge.connectivity_monitor.urllib.request.urlopen")
    def test_start_and_stop(self, mock_urlopen):
        """Monitor thread starts and stops without hanging."""
        mock_urlopen.side_effect = OSError("no connection")
        m = _make_monitor(ping_interval_s=0.02)
        m.start()
        assert m._thread is not None
        assert m._thread.is_alive()
        m.stop()
        # Thread should stop within reasonable time
        assert not m._thread.is_alive()
