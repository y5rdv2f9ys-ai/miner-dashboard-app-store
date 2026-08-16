from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Static

from ..formatting import total_hashrate
from .base import MinerTableScreen, service_label


class OverviewScreen(MinerTableScreen):
    def visible_columns(self) -> tuple[str, ...]:
        if (self.size.width or self.app.size.width) >= 120:
            return ("State", "Miner", "TH/s", "ASIC", "VR", "MHz", "Thermal", "Pool")
        return ("State", "Miner", "TH/s", "ASIC", "Thermal")

    def compose(self) -> ComposeResult:
        yield Static("JORGE MINER DASHBOARD · READ ONLY", classes="app-title")
        yield Static("OVERVIEW", classes="screen-title")
        with Horizontal(id="summary-row"):
            yield Static("Hashrate\n—", id="total-hash", classes="summary-card")
            yield Static("Active\n—", id="online-count", classes="summary-card")
            yield Static("Health\n—", id="health", classes="summary-card")
            yield Static("Alerts\n—", id="alerts", classes="summary-card")
        yield Static("Services: —", id="services")
        yield Static("Fleet scope: —", id="fleet-scope")
        yield DataTable(id=self.table_id)
        yield Static("1 Overview  2 Miners  3 Performance  4 Thermal  5 Pools  6 Events  7 System", classes="nav-line")
        yield Static("API: connecting…", id="api-status")

    def render_snapshot(self, snapshot: dict) -> None:
        super().render_snapshot(snapshot)
        if not self.is_mounted:
            return
        miners = snapshot.get("miners", []) if isinstance(snapshot, dict) else []
        miners = miners if isinstance(miners, list) else []
        summary = snapshot.get("fleet_summary", {}) if isinstance(snapshot, dict) else {}
        summary = summary if isinstance(summary, dict) else {}
        online = summary.get("active")
        online = online if isinstance(online, int) and not isinstance(online, bool) else sum(
            1 for miner in miners if isinstance(miner, dict) and miner.get("online") is True
        )
        health = snapshot.get("health") if isinstance(snapshot, dict) else None
        alerts = snapshot.get("alert_count") if isinstance(snapshot, dict) else None
        health = health if isinstance(health, (int, float)) and not isinstance(health, bool) else None
        alerts = alerts if isinstance(alerts, int) and not isinstance(alerts, bool) else None
        system = snapshot.get("system_status", {}) if isinstance(snapshot, dict) else {}
        system = system if isinstance(system, dict) else {}
        self.query_one("#total-hash", Static).update(f"Hashrate\n[bold]{total_hashrate(miners):.2f} TH/s[/]")
        self.query_one("#online-count", Static).update(f"Active\n[bold]{online}/{len(miners)}[/]")
        self.query_one("#health", Static).update(f"Health\n[bold]{health if health is not None else '—'}{'%' if health is not None else ''}[/]")
        self.query_one("#alerts", Static).update(f"Alerts\n[bold]{alerts if alerts is not None else '—'}[/]")
        self.query_one("#services", Static).update(
            "Thermal service: " + service_label(system.get("thermal_management"))
            + "   History/logging: " + service_label(system.get("miner_logging"))
        )
        local = [miner for miner in miners if miner.get("location_scope", "LOCAL") == "LOCAL"]
        remote = [miner for miner in miners if miner.get("location_scope") == "OFF-SITE"]
        local_online = summary.get("local_online", sum(miner.get("online") is True and float(miner.get("th") or 0) > 0 for miner in local))
        remote_mining = summary.get("offsite_mining", sum(miner.get("online") is True for miner in remote))
        local_total = summary.get("local_total", len(local))
        remote_total = summary.get("offsite_total", len(remote))
        self.query_one("#fleet-scope", Static).update(
            f"Local [bold]{local_online}/{local_total} online[/]   Off-site [bold]{remote_mining}/{remote_total} mining[/]"
        )
