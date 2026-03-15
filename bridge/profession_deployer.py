"""Profession Deployer — downloads profession files from workstation to robot.

Called when:
  1. A profession is activated for the first time (via license activate)
  2. Profession changes (user switches profession)
  3. Profession update detected (version mismatch)

Flow:
  1. GET {workstation}/professions/{id}/files   → list of file names
  2. GET {workstation}/professions/{id}/file/{name} → file content
  3. Save to /data/active_profession/
  4. GET {workstation}/rules/base               → base YAML rules (ISO/OSHA/EU)
  5. Save to /data/rules/
  6. Trigger local_brain.load() for hot-reload

If workstation is unreachable: keeps using whatever is already in /data/.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class ProfessionDeployer:
    """Downloads and installs profession packs onto the robot's local storage."""

    def __init__(
        self,
        workstation_url: str,
        data_dir: str = "/data",
        timeout_s: float = 10.0,
    ):
        self._ws_url   = workstation_url.rstrip("/")
        self._data_dir = Path(data_dir)
        self._timeout  = timeout_s

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def deploy(self, profession_id: str) -> dict:
        """Download and install a profession pack. Returns deployment report."""
        report: dict = {
            "profession_id": profession_id,
            "status":        "ok",
            "files_downloaded": [],
            "files_failed":   [],
            "rules_updated":  False,
            "timestamp":      time.time(),
        }

        # 1. Download profession files
        try:
            files = self._list_files(profession_id)
        except Exception as exc:
            report["status"] = "partial"
            report["error"]  = f"Cannot list files: {exc} — using cached"
            logger.warning("ProfessionDeployer: %s", report["error"])
            return report

        profession_dir = self._data_dir / "active_profession"
        profession_dir.mkdir(parents=True, exist_ok=True)

        for filename in files:
            try:
                content = self._fetch_file(profession_id, filename)
                (profession_dir / filename).write_bytes(content)
                report["files_downloaded"].append(filename)
                logger.debug("Deployed: %s", filename)
            except Exception as exc:
                report["files_failed"].append(filename)
                logger.warning("Failed to deploy %s: %s", filename, exc)

        # 2. Download base rules
        try:
            rules_content = self._fetch_base_rules()
            rules_dir = self._data_dir / "rules"
            rules_dir.mkdir(parents=True, exist_ok=True)
            (rules_dir / "base_rules.yaml").write_bytes(rules_content)
            report["rules_updated"] = True
            logger.info("Base rules updated (%d bytes)", len(rules_content))
        except Exception as exc:
            logger.warning("Base rules download failed: %s — using cached", exc)

        if report["files_failed"]:
            report["status"] = "partial"
        logger.info("Profession '%s' deployed: %d files, %d failed",
                    profession_id,
                    len(report["files_downloaded"]),
                    len(report["files_failed"]))
        return report

    def check_update_available(self, profession_id: str) -> bool:
        """Return True if workstation has a newer version than local."""
        try:
            resp = self._get_json(
                f"/professions/{profession_id}/version"
            )
            remote_version = resp.get("version", "")
        except Exception:
            return False

        local_manifest = self._data_dir / "active_profession" / "manifest.yaml"
        if not local_manifest.exists():
            return True

        try:
            import yaml
            with local_manifest.open() as f:
                local_data = yaml.safe_load(f)
            local_version = local_data.get("version", "")
            return remote_version != local_version
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _list_files(self, profession_id: str) -> List[str]:
        data = self._get_json(f"/professions/{profession_id}/files")
        return data.get("files", [])

    def _fetch_file(self, profession_id: str, filename: str) -> bytes:
        url = f"{self._ws_url}/professions/{profession_id}/file/{filename}"
        with urllib.request.urlopen(url, timeout=self._timeout) as resp:
            return resp.read()

    def _fetch_base_rules(self) -> bytes:
        url = f"{self._ws_url}/rules/base"
        with urllib.request.urlopen(url, timeout=self._timeout) as resp:
            return resp.read()

    def _get_json(self, path: str) -> dict:
        url = f"{self._ws_url}{path}"
        with urllib.request.urlopen(url, timeout=self._timeout) as resp:
            return json.loads(resp.read())
