"""Read-only operational screens backed exclusively by dashboard GET APIs."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ..formatting import integer, number, text, total_hashrate


NAV = "1 Overview  2 Miners  3 Performance  4 Thermal  5 Pools  6 Events  7 System"


def nested(snapshot: dict, key: str) -> dict:
    value = snapshot.get(key, {}) if isinstance(snapshot, dict) else {}
    return value if isinstance(value, dict) else {}


def rows(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def difficulty(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    for suffix in ("", "K", "M", "G", "T", "P"):
        if abs(amount) < 1000:
            return f"{amount:.2f}{suffix}"
        amount /= 1000
    return f"{amount:.2f}E"


def odds(value) -> str:
    try:
        amount = float(value)
        return f"1 in {difficulty(amount)}" if amount > 0 else "—"
    except (TypeError, ValueError):
        return "—"


class SnapshotScreen(Screen):
    def on_mount(self) -> None:
        self.render_snapshot(getattr(self.app, "snapshot", {}))

    def render_snapshot(self, snapshot: dict) -> None:
        pass

    def chrome(self, title: str, subtitle: str) -> ComposeResult:
        yield Static("JORGE MINER DASHBOARD · READ ONLY", classes="app-title")
        yield Static(title, classes="screen-title")
        yield Static(subtitle, classes="screen-subtitle")


class PerformanceScreen(SnapshotScreen):
    def compose(self) -> ComposeResult:
        yield from self.chrome("PERFORMANCE", "Live telemetry and rolling history from /api/performance")
        yield Static("Fleet: —", id="performance-summary", classes="section")
        yield DataTable(id="performance-table")
        yield Static(NAV, classes="nav-line")
        yield Static("API: connecting…", id="api-status")

    def render_snapshot(self, snapshot: dict) -> None:
        miners = rows(snapshot.get("miners", []))
        live = {str(item.get("name", "")): item for item in miners}
        perf = rows(nested(snapshot, "performance_data").get("performance", []))
        if not perf:
            perf = [{"name": item.get("name")} for item in miners]
        table = self.query_one("#performance-table", DataTable)
        table.clear(columns=True)
        columns = ("Miner", "Now TH/s", "60m", "12h", "24h", "ASIC/VR", "MHz")
        if self.size.width < 100:
            columns = tuple(item for item in columns if item not in ("24h", "MHz"))
        table.add_columns(*columns)
        table.zebra_stripes = True
        for item in perf:
            name = str(item.get("name", ""))
            now = live.get(name, {})
            values = {
                "Miner": text(name), "Now TH/s": number(now.get("th"), 2),
                "60m": number(item.get("th_60m"), 2), "12h": number(item.get("th_12h"), 2),
                "24h": number(item.get("th_24h"), 2),
                "ASIC/VR": f"{number(now.get('temp'), 1)}/{number(now.get('vr_temp'), 1) if now.get('vr_temp') not in (-1, '-1') else '—'}°",
                "MHz": integer(now.get("freq")),
            }
            table.add_row(*(values[column] for column in columns))
        online = [item for item in miners if item.get("online") is True]
        avg60 = sum(float(item.get("th_60m") or 0) for item in perf)
        temps = [float(item.get("temp")) for item in online if isinstance(item.get("temp"), (int, float))]
        self.query_one("#performance-summary", Static).update(
            f"[bold]Fleet[/]  Live {total_hashrate(miners):.2f} TH/s  ·  60m {avg60:.2f} TH/s  ·  "
            f"Active {len(online)}/{len(miners)}  ·  Avg ASIC {sum(temps)/len(temps):.1f}°C" if temps else
            f"[bold]Fleet[/]  Live {total_hashrate(miners):.2f} TH/s  ·  60m {avg60:.2f} TH/s  ·  Active {len(online)}/{len(miners)}"
        )


class ThermalScreen(SnapshotScreen):
    def compose(self) -> ComposeResult:
        yield from self.chrome("THERMAL", "Configured thermal profiles and current state · monitoring only")
        yield DataTable(id="thermal-table")
        yield Static(NAV, classes="nav-line")
        yield Static("API: connecting…", id="api-status")

    def render_snapshot(self, snapshot: dict) -> None:
        configured = rows(nested(snapshot, "thermal_data").get("miners", []))
        table = self.query_one("#thermal-table", DataTable)
        table.clear(columns=True)
        columns = ("Miner", "Control", "Now °C", "Now MHz", "Status", "Base", "Hot", "Critical", "Recover/Warn/Crit", "Base/Hot/Crit mV")
        if self.size.width < 105:
            columns = tuple(item for item in columns if item not in ("Base/Hot/Crit mV", "Now MHz"))
        table.add_columns(*columns)
        table.zebra_stripes = True
        for item in configured:
            values = {
                "Miner": text(item.get("name")),
                "Control": "[green]ENABLED[/]" if item.get("enabled") is True else "[dim]DISABLED[/]",
                "Now °C": number(item.get("current_temp"), 1), "Now MHz": integer(item.get("current_freq")),
                "Status": text(item.get("status")), "Base": integer(item.get("base_freq")),
                "Hot": integer(item.get("hot_freq")), "Critical": integer(item.get("critical_freq")),
                "Recover/Warn/Crit": "/".join(number(item.get(key), 1) for key in ("recover_temp", "warn_temp", "critical_temp")),
                "Base/Hot/Crit mV": "/".join(integer(item.get(key)) for key in ("base_volt", "hot_volt", "critical_volt")),
            }
            table.add_row(*(values[column] for column in columns))


ALLOCATIONS = (("Braiins", "braiins", "#60a5fa"), ("Umbrel Solo", "btc_solo", "#f59e0b"), ("BCH Solo", "bch_solo", "#22c55e"))


def allocation_values(pool_data: dict) -> list[tuple[str, float, float, str]]:
    total = float(pool_data.get("total_th") or 0)
    result = []
    for label, key, color in ALLOCATIONS:
        rate = float(nested(pool_data, key).get("hashrate_th") or 0)
        result.append((label, rate, rate / total * 100 if total else 0, color))
    return result


def allocation_bars(pool_data: dict, width: int = 36, unicode: bool = True) -> str:
    full, edge = ("█", "│") if unicode else ("#", "|")
    lines = []
    for label, rate, pct, color in allocation_values(pool_data):
        filled = round(width * pct / 100)
        bar = full * filled + " " * (width - filled)
        lines.append(f"{label:<12} {rate:>6.2f} TH/s  [dim]{edge}[/][{color}]{bar}[/][dim]{edge}[/] {pct:>5.1f}%")
    return "\n".join(lines)


class PoolsScreen(SnapshotScreen):
    def compose(self) -> ComposeResult:
        yield from self.chrome("POOLS", "Pool allocation, solo odds, and Braiins workers · monitoring only")
        with VerticalScroll(classes="content-scroll"):
            yield Static("Allocation: —", id="allocation", classes="section")
            yield Static("Solo pools: —", id="pool-details", classes="section")
            yield Static("Braiins: —", id="pool-workers", classes="section")
        yield Static(NAV, classes="nav-line")
        yield Static("API: connecting…", id="api-status")

    def render_snapshot(self, snapshot: dict) -> None:
        data = nested(snapshot, "pools_data")
        visual = allocation_bars(data, 28 if self.size.width < 100 else 36)
        self.query_one("#allocation", Static).update(f"[bold]HASHRATE ALLOCATION[/]\n{visual}")
        details = []
        for key, label in (("btc_solo", "BTC / Umbrel Solo"), ("bch_solo", "BCH / SoloPool")):
            item = nested(data, key)
            item_odds = nested(item, "odds")
            miners = ", ".join(str(x) for x in item.get("miners", []) if x) or "none"
            details.append(
                f"[bold]{label}[/]  {number(item.get('hashrate_th'), 2)} TH/s  · miners {miners}\n"
                f"  best session {difficulty(item.get('session_best'))}  historic {difficulty(item.get('historic_best'))}  "
                f"network {difficulty(item_odds.get('difficulty'))}  best/net {number(item.get('best_network_pct'), 8, '%')}\n"
                f"  odds daily {odds(item_odds.get('day_den'))}  monthly {odds(item_odds.get('month_den'))}"
            )
        self.query_one("#pool-details", Static).update("[bold]SOLO POOLS[/]\n" + "\n".join(details))
        braiins = nested(data, "braiins")
        workers = rows(braiins.get("workers", []))
        worker_lines = [
            f"  {text(w.get('name')):<18} {text(w.get('scope')):<8} {text(w.get('state')):<9} "
            f"{number(w.get('hash_rate_5m_th'), 2):>6} TH/s (5m)  {number(w.get('hash_rate_60m_th'), 2):>6} TH/s (60m)"
            for w in workers
        ]
        self.query_one("#pool-workers", Static).update(
            "[bold]BRAIINS[/]  " + number(braiins.get("hashrate_th"), 2, " TH/s")
            + "  · 60m " + number(braiins.get("pool_60m_th"), 2, " TH/s")
            + "  · today " + number(braiins.get("today_reward"), 8, " BTC")
            + "  · balance " + number(braiins.get("balance"), 8, " BTC")
            + f"  · active workers {len(workers)}\n" + ("\n".join(worker_lines) if worker_lines else "  No active worker data")
        )


class EventsScreen(SnapshotScreen):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield from self.chrome("EVENTS", "Bounded recent thermal events · newest first")
        yield DataTable(id="events-table")
        yield Static(NAV + "  ↑/↓ or j/k Scroll", classes="nav-line")
        yield Static("API: connecting…", id="api-status")

    def render_snapshot(self, snapshot: dict) -> None:
        events = rows(nested(snapshot, "events_data").get("events", []))
        table = self.query_one("#events-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Level", "Recent event (newest first)")
        table.zebra_stripes = True
        table.cursor_type = "row"
        for event in events:
            level = str(event.get("level", "INFO")).upper()
            styled = f"[red]{level}[/]" if level == "ERROR" else f"[yellow]{level}[/]" if level == "WARNING" else f"[dim]{level}[/]"
            table.add_row(styled, text(event.get("message")))
        if not events:
            table.add_row("[dim]INFO[/]", "No recent thermal events")

    def action_cursor_down(self) -> None:
        self.query_one("#events-table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#events-table", DataTable).action_cursor_up()


def duration(value) -> str:
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return "—"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m"


def bytes_size(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    for suffix in ("B", "KB", "MB", "GB"):
        if amount < 1024 or suffix == "GB":
            return f"{amount:.1f} {suffix}"
        amount /= 1024
    return "—"


def timestamp(value) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(value)))
    except (TypeError, ValueError, OSError):
        return "—"


class SystemScreen(SnapshotScreen):
    def compose(self) -> ComposeResult:
        yield from self.chrome("SYSTEM", "Jorge Miner Dashboard application health and diagnostics")
        with VerticalScroll(classes="content-scroll"):
            yield Static("Diagnostics: —", id="system-content", classes="section")
        yield Static(NAV, classes="nav-line")
        yield Static("API: connecting…", id="api-status")

    def render_snapshot(self, snapshot: dict) -> None:
        diagnostics = nested(snapshot, "diagnostics_data")
        system = nested(snapshot, "system_status")
        storage = nested(diagnostics, "storage")
        thermal = nested(diagnostics, "thermal")
        history = nested(diagnostics, "history")
        freshness = number(diagnostics.get("snapshot_age_seconds"), 0, "s")
        api = "[red]OFFLINE[/]" if getattr(self.app, "last_error", None) else "[green]● ONLINE[/]"
        self.query_one("#system-content", Static).update(
            "[bold]APPLICATION[/]\n"
            f"Dashboard API          {api}\n"
            f"Thermal Service        {'[green]● ONLINE[/]' if system.get('thermal_management') else '[red]● OFFLINE[/]'}\n"
            f"History / Logging      {'[green]● ONLINE[/]' if system.get('miner_logging') else '[red]● OFFLINE[/]'}\n"
            f"Data Freshness         {freshness}\nUptime                 {duration(diagnostics.get('uptime_seconds'))}\n"
            f"Version                {text(diagnostics.get('version'))}\n\n"
            "[bold]DATA[/]\n"
            f"Last Miner Update      {text(diagnostics.get('snapshot_updated'))}\n"
            f"Last Thermal Update    {timestamp(thermal.get('updated_epoch'))}\n"
            f"Last History Write     {timestamp(history.get('updated_epoch'))}\n"
            f"API Response           {number(snapshot.get('api_response_ms'), 1, ' ms')}\n\n"
            "[bold]STORAGE · APP DATA ONLY[/]\n"
            f"History Data           {bytes_size(storage.get('history_bytes'))}\n"
            f"Thermal Log            {bytes_size(storage.get('thermal_log_bytes'))}\n"
            f"Benchmark Data         {bytes_size(storage.get('benchmark_bytes'))}"
        )
