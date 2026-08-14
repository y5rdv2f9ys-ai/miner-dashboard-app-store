from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ..formatting import integer, is_online, number, text
from .base import MinerTableScreen


class MinersScreen(MinerTableScreen):
    def compose(self) -> ComposeResult:
        yield Static("JORGE MINER DASHBOARD · READ ONLY", classes="app-title")
        yield Static("MINERS", classes="screen-title")
        yield Static("All miners from the cached dashboard snapshot", classes="screen-subtitle")
        yield DataTable(id=self.table_id)
        yield Static("1 Overview  2 Miners  ↑/↓ or j/k Move  Enter Detail  r Refresh  ? Help  q Quit", classes="nav-line")
        yield Static("API: connecting…", id="api-status")


class MinerDetailScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back", show=False)]

    def __init__(self, miner: dict):
        super().__init__()
        self.miner = dict(miner)

    def compose(self) -> ComposeResult:
        name = text(self.miner.get("name"))
        state = "[green]ONLINE[/]" if is_online(self.miner) else "[red]OFFLINE[/]"
        rows = [
            ("Miner", name),
            ("Connection", state),
            ("Thermal/status", text(self.miner.get("thermal_status", self.miner.get("status")))),
            ("Hashrate", number(self.miner.get("th"), 2, " TH/s")),
            ("Expected hashrate", number(self.miner.get("expected_th"), 2, " TH/s")),
            ("ASIC temperature", number(self.miner.get("temp"), 1, "°C")),
            ("VR temperature", number(self.miner.get("vr_temp"), 1, "°C") if self.miner.get("vr_temp") not in (-1, "-1") else "—"),
            ("Thermal limit", number(self.miner.get("thermal_limit"), 1, "°C")),
            ("Frequency", integer(self.miner.get("freq"), " MHz")),
            ("Core voltage", integer(self.miner.get("volt"), " mV")),
            ("Rejected shares", number(self.miner.get("reject"), 2, "%")),
            ("Pool label", text(self.miner.get("pool"))),
            ("Coin", text(self.miner.get("coin"))),
            ("Session best difficulty", number(self.miner.get("best_session_diff"), 0)),
            ("Historic best difficulty", number(self.miner.get("best_diff"), 0)),
        ]
        yield Static("JORGE MINER DASHBOARD · READ ONLY", classes="app-title")
        yield Static(f"MINER DETAIL · {name}", classes="screen-title")
        with VerticalScroll(id="detail-scroll"):
            yield Static("\n".join(f"[dim]{label:<24}[/] {value}" for label, value in rows), id="detail-content")
        yield Static("Esc: back", classes="screen-subtitle")

    def action_back(self) -> None:
        self.app.pop_screen()
