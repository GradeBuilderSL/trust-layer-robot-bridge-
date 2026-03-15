"""License Manager — robot-side license verification and activation.

Handles hardware fingerprint collection, JWT token verification (Ed25519),
online/offline activation flows, and license state management.

States:
  UNLICENSED — no token present, robot runs LITE mode only
  ACTIVE     — token valid, profession available
  EXPIRED    — token past expiry, fallback to LITE
  INVALID    — signature mismatch or hardware mismatch → LITE

Storage (on robot):
  /data/license_token.jwt  — signed LicenseToken (Ed25519 JWT)
  /data/public_key.pem     — Partenit Ed25519 public key (not secret)

The private key is NEVER on the robot. Only on activation.partenit.ai.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import time
import base64
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedded public key — rotate via firmware update if compromised.
# The corresponding private key lives only on activation.partenit.ai.
# ---------------------------------------------------------------------------
_EMBEDDED_PUBLIC_KEY_PEM = b"""\
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA+kMTkCEfCFOlmWwqBqaoYVevlkDCiVj7/CT7uqERmK0=
-----END PUBLIC KEY-----
"""

# Salt: anti-trivial-collision, NOT secret.
_HW_SALT = "partenit-hw-v1"


# ---------------------------------------------------------------------------
# Hardware Fingerprint
# ---------------------------------------------------------------------------

class HardwareFingerprint:
    """Collects a stable hardware identity for license binding.

    Sources (in priority order):
      1. Robot serial — from robot API GET /api/state or /health field serial_number
      2. MAC address  — primary network interface (eth0 or wlan0)
      3. CPU serial   — /proc/cpuinfo (Linux ARM)

    fingerprint = SHA-256(serial + "|" + mac + "|" + cpu_serial + "|" + SALT)
    """

    def collect(self, robot_api_url: str | None = None) -> str:
        """Collect and return hex SHA-256 fingerprint."""
        serial = self._get_robot_serial(robot_api_url)
        mac    = self._get_mac_address()
        cpu    = self._get_cpu_serial()
        raw    = f"{serial}|{mac}|{cpu}|{_HW_SALT}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_robot_serial(self, robot_api_url: str | None) -> str:
        if not robot_api_url:
            return "unknown"
        try:
            import urllib.request
            with urllib.request.urlopen(f"{robot_api_url}/health", timeout=3) as r:
                data = json.loads(r.read())
                return data.get("serial_number", data.get("serial", "unknown"))
        except Exception:
            return "unknown"

    def _get_mac_address(self) -> str:
        for iface in ("eth0", "wlan0", "en0", "ens3"):
            path = f"/sys/class/net/{iface}/address"
            try:
                with open(path) as f:
                    mac = f.read().strip()
                    if mac and mac != "00:00:00:00:00:00":
                        return mac
            except OSError:
                pass
        # Fallback: hostname-based (less stable but better than "unknown")
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    def _get_cpu_serial(self) -> str:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("Serial"):
                        return line.split(":")[-1].strip()
        except OSError:
            pass
        return "unknown"


# ---------------------------------------------------------------------------
# License state
# ---------------------------------------------------------------------------

class LicenseState(str, Enum):
    UNLICENSED = "UNLICENSED"
    ACTIVE     = "ACTIVE"
    EXPIRED    = "EXPIRED"
    INVALID    = "INVALID"


@dataclass
class LicenseStatus:
    state:         LicenseState = LicenseState.UNLICENSED
    profession_id: str          = ""
    tier:          str          = "LITE"   # LITE | STANDARD | PREMIUM
    days_remaining: int         = 0
    hardware_id:   str          = ""
    error:         str          = ""


# ---------------------------------------------------------------------------
# JWT helpers (no PyJWT dependency — manual base64url)
# ---------------------------------------------------------------------------

def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid JWT: expected 3 parts, got {len(parts)}")
    return json.loads(_b64url_decode(parts[1]))


def _verify_jwt_ed25519(token: str, public_pem: bytes) -> dict:
    """Verify Ed25519 JWT signature and return payload. Raises on failure."""
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.exceptions import InvalidSignature

    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")

    signing_input = f"{parts[0]}.{parts[1]}".encode()
    sig = _b64url_decode(parts[2])

    pub = load_pem_public_key(public_pem)
    try:
        pub.verify(sig, signing_input)
    except InvalidSignature:
        raise ValueError("JWT signature verification failed")

    return json.loads(_b64url_decode(parts[1]))


# ---------------------------------------------------------------------------
# License Manager
# ---------------------------------------------------------------------------

class LicenseManager:
    """Robot-side license lifecycle manager.

    Verifies the stored JWT token against the hardware fingerprint and
    the embedded Ed25519 public key. No network required for verification.

    Usage:
        mgr = LicenseManager()
        status = mgr.verify()
        if not mgr.is_licensed:
            # Run in LITE mode
    """

    def __init__(
        self,
        data_dir: str = "/data",
        public_key_pem: bytes | None = None,
        robot_api_url: str | None = None,
    ):
        self._data_dir = Path(data_dir)
        self._token_path = self._data_dir / "license_token.jwt"
        self._pubkey_pem = public_key_pem or self._load_pubkey_pem()
        self._robot_api_url = robot_api_url
        self._hw = HardwareFingerprint()
        self._status = LicenseStatus()
        self._fingerprint: str = ""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def verify(self) -> LicenseStatus:
        """Verify the stored license token. Returns LicenseStatus."""
        if not self._fingerprint:
            self._fingerprint = self._hw.collect(self._robot_api_url)

        if not self._token_path.exists():
            self._status = LicenseStatus(state=LicenseState.UNLICENSED)
            return self._status

        token = self._token_path.read_text(encoding="utf-8").strip()
        self._status = self._parse_and_verify(token)
        return self._status

    def activate_online(
        self,
        key: str,
        activation_server_url: str = "https://activate.partenit.ai",
    ) -> LicenseStatus:
        """Activate online: send fingerprint to server, receive signed JWT."""
        import urllib.request, urllib.error

        if not self._fingerprint:
            self._fingerprint = self._hw.collect(self._robot_api_url)

        payload = json.dumps({
            "key": key.strip(),
            "hardware_fingerprint": self._fingerprint,
        }).encode()

        try:
            req = urllib.request.Request(
                f"{activation_server_url}/activate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            return LicenseStatus(
                state=LicenseState.INVALID,
                error=f"Activation server error {exc.code}: {body[:200]}",
            )
        except Exception as exc:
            return LicenseStatus(
                state=LicenseState.INVALID,
                error=f"Activation request failed: {exc}",
            )

        token_jwt = data.get("license_token") or data.get("token", "")
        if not token_jwt:
            return LicenseStatus(
                state=LicenseState.INVALID,
                error=f"Server returned no token: {data}",
            )

        return self.apply_activation_response(token_jwt)

    def generate_activation_request(self, key: str) -> dict:
        """Offline activation step 1 — generate request payload for manual upload."""
        if not self._fingerprint:
            self._fingerprint = self._hw.collect(self._robot_api_url)
        return {
            "key": key.strip(),
            "hardware_fingerprint": self._fingerprint,
            "timestamp": int(time.time()),
            "bridge_version": "1.0",
        }

    def apply_activation_response(self, token_jwt: str) -> LicenseStatus:
        """Offline activation step 3 — apply token received from server."""
        status = self._parse_and_verify(token_jwt)
        if status.state == LicenseState.ACTIVE:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(token_jwt.strip(), encoding="utf-8")
            logger.info("License activated: profession=%s tier=%s days=%d",
                        status.profession_id, status.tier, status.days_remaining)
        self._status = status
        return status

    @property
    def is_licensed(self) -> bool:
        return self._status.state == LicenseState.ACTIVE

    @property
    def profession_id(self) -> str | None:
        return self._status.profession_id or None

    @property
    def tier(self) -> str:
        return self._status.tier if self.is_licensed else "LITE"

    @property
    def days_remaining(self) -> int:
        return self._status.days_remaining

    @property
    def hardware_fingerprint(self) -> str:
        if not self._fingerprint:
            self._fingerprint = self._hw.collect(self._robot_api_url)
        return self._fingerprint

    def status_dict(self) -> dict:
        return {
            "state":          self._status.state.value,
            "profession_id":  self._status.profession_id,
            "tier":           self.tier,
            "days_remaining": self._status.days_remaining,
            "hardware_id":    self._status.hardware_id or self._fingerprint,
            "error":          self._status.error,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_pubkey_pem(self) -> bytes:
        """Load from file if present, otherwise use embedded key."""
        path = self._data_dir / "public_key.pem"
        if path.exists():
            return path.read_bytes()
        return _EMBEDDED_PUBLIC_KEY_PEM

    def _parse_and_verify(self, token: str) -> LicenseStatus:
        """Parse and verify JWT. Returns LicenseStatus."""
        # Step 1: verify signature
        try:
            payload = _verify_jwt_ed25519(token, self._pubkey_pem)
        except ImportError:
            logger.warning("cryptography not installed — skipping signature check")
            try:
                payload = _decode_jwt_payload(token)
            except Exception as exc:
                return LicenseStatus(
                    state=LicenseState.INVALID, error=f"JWT decode failed: {exc}"
                )
        except Exception as exc:
            return LicenseStatus(
                state=LicenseState.INVALID, error=f"Signature invalid: {exc}"
            )

        # Step 2: check hardware binding
        hw_id = payload.get("hardware_id", "")
        if hw_id and hw_id != self._fingerprint:
            return LicenseStatus(
                state=LicenseState.INVALID,
                error=f"Hardware mismatch: token={hw_id[:16]}... mine={self._fingerprint[:16]}...",
                hardware_id=hw_id,
            )

        # Step 3: check expiry
        exp = payload.get("exp", 0)
        now = time.time()
        if exp and now > float(exp):
            days_expired = int((now - float(exp)) / 86400)
            return LicenseStatus(
                state=LicenseState.EXPIRED,
                profession_id=payload.get("profession_id", ""),
                tier=payload.get("tier", "STANDARD"),
                error=f"Expired {days_expired} days ago",
                hardware_id=hw_id,
            )

        # Step 4: all checks passed
        days_remaining = max(0, int((float(exp) - now) / 86400)) if exp else 9999
        return LicenseStatus(
            state=LicenseState.ACTIVE,
            profession_id=payload.get("profession_id", ""),
            tier=payload.get("tier", "STANDARD"),
            days_remaining=days_remaining,
            hardware_id=hw_id,
        )
