"""Auto-update client for the connector agent."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from api.cloud_client import CloudAPIError, CloudClient
from app.constants import APP_VERSION
from packaging.version import Version

logger = logging.getLogger("brixigo")


class AutoUpdater:
    def __init__(self, cloud: CloudClient, *, install_dir: Path) -> None:
        self._cloud = cloud
        self.install_dir = install_dir

    def check(self) -> dict:
        try:
            info = self._cloud.check_update()
        except CloudAPIError as exc:
            return {"update_available": False, "error": str(exc), "current": APP_VERSION}
        except Exception as exc:
            return {"update_available": False, "error": str(exc), "current": APP_VERSION}

        latest = str(info.get("version") or info.get("latest_version") or "")
        if not latest:
            return {"update_available": False, "current": APP_VERSION, "info": info}
        try:
            available = Version(latest) > Version(APP_VERSION)
        except Exception:
            available = latest != APP_VERSION
        return {
            "update_available": available,
            "current": APP_VERSION,
            "latest": latest,
            "download_url": info.get("download_url") or info.get("installer_url"),
            "sha256": info.get("sha256") or info.get("checksum_sha256") or "",
            "info": info,
        }

    def download_and_install(
        self,
        download_url: str,
        *,
        silent: bool = True,
        expected_sha256: str = "",
    ) -> None:
        if not download_url.lower().startswith("https://"):
            raise ValueError("Update download must use HTTPS")

        suffix = ".exe" if download_url.lower().endswith(".exe") else ".msi"
        dest = Path(tempfile.gettempdir()) / f"brixigo-connector-update{suffix}"
        logger.info("Downloading update from %s", download_url)
        digest = hashlib.sha256()
        with httpx.stream("GET", download_url, follow_redirects=True, timeout=120.0, verify=True) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes():
                    digest.update(chunk)
                    fh.write(chunk)

        actual = digest.hexdigest().lower()
        expected = (expected_sha256 or "").strip().lower()
        if expected:
            if actual != expected:
                dest.unlink(missing_ok=True)
                raise ValueError(
                    f"Update checksum mismatch — expected {expected}, got {actual}"
                )
            logger.info("Update checksum verified sha256=%s", actual)
        else:
            # Production installs should always publish sha256 from the update catalog.
            logger.warning(
                "Update catalog did not provide sha256 — installing without integrity check "
                "(sha256=%s)",
                actual,
            )

        logger.info("Installing update %s", dest)
        if suffix == ".msi":
            args = ["msiexec", "/i", str(dest), "/qn", "/norestart"] if silent else ["msiexec", "/i", str(dest)]
        else:
            args = [str(dest), "/VERYSILENT", "/NORESTART"] if silent else [str(dest)]

        subprocess.Popen(args, close_fds=True)
        # Exit so installer can replace binaries
        if getattr(sys, "frozen", False):
            os._exit(0)

    def check_and_apply(self) -> dict:
        result = self.check()
        url = result.get("download_url")
        if result.get("update_available") and url:
            self.download_and_install(
                str(url),
                silent=True,
                expected_sha256=str(result.get("sha256") or ""),
            )
            result["installing"] = True
        return result
