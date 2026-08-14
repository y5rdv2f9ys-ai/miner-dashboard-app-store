from __future__ import annotations

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ..formatting import integer, is_offsite, is_online, number, text


class MinerTableScreen(Screen):
    """Shared responsive, read-only miner table behavior."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "open_detail", "Detail", show=False),
    ]

    table_id = "miner-table"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshot: dict = {"miners": []}
        self._column_signature: tuple[str, ...] = ()

    @property
    def table(self) -> DataTable:
        return self.query_one(f"#{self.table_id}", DataTable)

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.render_snapshot(getattr(self.app, "snapshot", {"miners": []}))

    def on_resize(self) -> None:
        self.rebuild_table()

    def render_snapshot(self, snapshot: dict) -> None:
        self.snapshot = snapshot if isinstance(snapshot, dict) else {"miners": []}
        self.rebuild_table()

    def visible_columns(self) -> tuple[str, ...]:
        width = self.size.width or self.app.size.width
        columns = ["State", "Miner", "TH/s", "ASIC", "VR", "MHz", "mV", "Reject", "Thermal", "Pool"]
        if width < 110:
            columns.remove("Pool")
        if width < 95:
            columns.remove("Reject")
        if width < 85:
            columns.remove("mV")
        if width < 75:
            columns.remove("VR")
        return tuple(columns)

    def row_values(self, miner: dict) -> dict[str, str]:
        offsite = is_offsite(miner)
        remote_telemetry = miner.get("telemetry_source") == "BRAIINS"
        unmanaged = miner.get("management") == "UNMANAGED"
        state = (
            "[cyan]● OFF-SITE[/]" if is_online(miner) else "[dim]● REMOTE IDLE[/]"
        ) if offsite else ("[green]● ON[/]" if is_online(miner) else "[red]● OFF[/]")
        return {
            "State": state,
            "Miner": text(miner.get("name")),
            "TH/s": number(miner.get("th"), 2),
            "ASIC": "—" if remote_telemetry else number(miner.get("temp"), 1, "°"),
            "VR": "—" if remote_telemetry else (number(miner.get("vr_temp"), 1, "°") if miner.get("vr_temp") not in (-1, "-1") else "—"),
            "MHz": "—" if remote_telemetry else integer(miner.get("freq")),
            "mV": "—" if remote_telemetry else integer(miner.get("volt")),
            "Reject": "—" if remote_telemetry else number(miner.get("reject"), 2, "%"),
            "Thermal": "UNMANAGED" if unmanaged else text(miner.get("thermal_status", miner.get("status"))),
            "Pool": text(miner.get("pool")),
        }

    def selected_name(self) -> str | None:
        table = self.table
        if table.row_count and table.cursor_row is not None:
            try:
                return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            except Exception:
                return None
        return None

    def rebuild_table(self) -> None:
        if not self.is_mounted:
            return
        table = self.table
        selected = self.selected_name()
        columns = self.visible_columns()
        table.clear(columns=columns != self._column_signature)
        if columns != self._column_signature:
            table.add_columns(*columns)
            self._column_signature = columns
        miners = self.snapshot.get("miners", [])
        miners = miners if isinstance(miners, list) else []
        target_row = None
        for index, miner in enumerate(miners):
            if not isinstance(miner, dict):
                continue
            name = text(miner.get("name"))
            values = self.row_values(miner)
            table.add_row(*(values[column] for column in columns), key=name)
            if name == selected:
                target_row = index
        if target_row is not None:
            table.move_cursor(row=target_row)

    def action_cursor_down(self) -> None:
        self.table.action_cursor_down()

    def action_cursor_up(self) -> None:
        self.table.action_cursor_up()

    def action_open_detail(self) -> None:
        name = self.selected_name()
        if name:
            self.app.open_miner_detail(name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.open_miner_detail(str(event.row_key.value))


def service_label(value) -> str:
    if value is True:
        return "[green]ONLINE[/]"
    if value is False:
        return "[red]OFFLINE[/]"
    return "[dim]UNKNOWN[/]"


class SummaryStatic(Static):
    pass
