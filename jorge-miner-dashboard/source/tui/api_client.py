"""Small GET-only client for the dashboard's cached HTTP API."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class DashboardAPIError(RuntimeError):
    """The dashboard snapshot could not be retrieved or decoded."""


class DashboardAPIClient:
    """Read cached dashboard state without exposing write-capable methods."""

    def __init__(self, base_url: str = "http://127.0.0.1:5057", timeout: float = 4.0):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def _get_json(self, path: str) -> dict:
        url = urljoin(self.base_url, path.lstrip("/"))
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise DashboardAPIError(f"Dashboard API unavailable: {error}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DashboardAPIError("Dashboard API returned malformed JSON") from error

        if not isinstance(payload, dict):
            raise DashboardAPIError("Dashboard API response must be a JSON object")
        return payload

    def get_miners(self) -> dict:
        payload = self._get_json("api/miners")
        miners = payload.get("miners", [])
        if not isinstance(miners, list):
            raise DashboardAPIError("Dashboard API miners field must be a list")
        payload["miners"] = [miner for miner in miners if isinstance(miner, dict)]
        if not isinstance(payload.get("system_status", {}), dict):
            payload["system_status"] = {}
        return payload

    def get_dashboard_data(self) -> dict:
        """Fetch the dashboard's read-only cached views in parallel."""
        started = time.monotonic()
        paths = {
            "snapshot": "api/miners",
            "performance_data": "api/performance",
            "thermal_data": "api/thermal-settings",
            "pools_data": "api/page3",
            "events_data": "api/events?limit=100",
            "diagnostics_data": "api/diagnostics",
        }
        with ThreadPoolExecutor(max_workers=len(paths)) as executor:
            futures = {key: executor.submit(self._get_json, path) for key, path in paths.items()}
            results = {}
            for key, future in futures.items():
                results[key] = future.result()
        results["api_response_ms"] = (time.monotonic() - started) * 1000

        snapshot = results.pop("snapshot")
        miners = snapshot.get("miners", [])
        if not isinstance(miners, list):
            raise DashboardAPIError("Dashboard API miners field must be a list")
        snapshot["miners"] = [miner for miner in miners if isinstance(miner, dict)]
        if not isinstance(snapshot.get("system_status", {}), dict):
            snapshot["system_status"] = {}
        snapshot.update(results)
        return snapshot
