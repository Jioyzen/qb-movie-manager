"""qBittorrent client module."""
import json
import time
import requests
from typing import Optional
from config import config


class QBClient:
    """Thin wrapper around qBittorrent Web API v2."""

    def __init__(self):
        self._session = requests.Session()
        self._base = config.qb_url
        self._logged_in = False

    def _ensure_login(self):
        if self._logged_in:
            return
        url = f"{self._base}/api/v2/auth/login"
        r = self._session.post(
            url,
            data={"username": config.get("qb_username"), "password": config.get("qb_password")},
            timeout=10,
        )
        r.raise_for_status()
        self._logged_in = True

    def _get(self, path, params=None):
        self._ensure_login()
        r = self._session.get(f"{self._base}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r

    def _post(self, path, data=None, files=None):
        self._ensure_login()
        r = self._session.post(f"{self._base}{path}", data=data, files=files, timeout=30)
        r.raise_for_status()
        return r

    def get_categories(self) -> list[str]:
        """Return list of category names."""
        r = self._get("/api/v2/torrents/categories")
        data = r.json()
        return sorted(data.keys())

    def get_torrents(self, category: str = "", filter: str = "all") -> list[dict]:
        """Fetch torrents, optionally filtered by category."""
        params = {"filter": filter, "sort": "name", "reverse": "false"}
        if category:
            params["category"] = category
        r = self._get("/api/v2/torrents/info", params=params)
        return r.json()

    def get_torrent_files(self, torrent_hash: str) -> list[dict]:
        """Get file list inside a torrent."""
        r = self._get("/api/v2/torrents/files", params={"hash": torrent_hash})
        return r.json()

    def delete_torrents(self, hashes: list[str], delete_files: bool = True):
        """Delete torrents from qBittorrent."""
        if not hashes:
            return
        self._post(
            "/api/v2/torrents/delete",
            data={"hashes": "|".join(hashes), "deleteFiles": "true" if delete_files else "false"},
        )

    def test_connection(self) -> tuple[bool, str]:
        """Test qBittorrent connectivity. Returns (ok, message)."""
        try:
            cats = self.get_categories()
            return True, f"Connected. Categories: {', '.join(cats[:10])}"
        except requests.exceptions.ConnectionError:
            return False, f"Cannot connect to {self._base}"
        except Exception as e:
            return False, str(e)
