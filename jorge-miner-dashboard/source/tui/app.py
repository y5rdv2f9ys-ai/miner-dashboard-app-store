"""On-demand, read-only Textual client for cached dashboard state."""

from __future__ import annotations

import argparse
import asyncio
import os
import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Static

from .api_client import DashboardAPIClient, DashboardAPIError
from .screens import (
    EventsScreen, MinerDetailScreen, MinersScreen, OverviewScreen, PerformanceScreen,
    PoolsScreen, SystemScreen, ThermalScreen,
)


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close", show=False), Binding("?", "dismiss", "Close", show=False)]

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]Keyboard reference[/]\n\n"
            "1 Overview   2 Miners   3 Performance   4 Thermal\n"
            "5 Pools      6 Events   7 System\n\n"
            "↑/↓ or j/k  Navigate miners\nEnter       Miner detail\n"
            "Esc         Back\nr           Refresh\n?           Help\nq           Quit",
            id="help-box",
        )

    def action_dismiss(self) -> None:
        self.dismiss()


class MinerDashboardApp(App):
    TITLE = "Jorge Miner Dashboard"
    SUB_TITLE = "Read-only terminal dashboard"
    ENABLE_COMMAND_PALETTE = False
    REFRESH_SECONDS = 10
    STALE_SECONDS = 30

    CSS = """
    Screen { background: #09090b; color: #e4e4e7; }
    Header { background: #18181b; }
    .app-title { height: 2; padding: 0 2; background: #18181b; color: #d4d4d8; text-style: bold; }
    .screen-title { height: 3; padding: 1 2 0 2; text-style: bold; color: #f4f4f5; }
    .screen-subtitle { height: 2; padding: 0 2; color: #a1a1aa; }
    #summary-row { height: 5; padding: 0 1; }
    .summary-card { width: 1fr; height: 4; margin: 0 1; padding: 0 1; border: round #3f3f46; text-align: center; }
    #services, #fleet-scope { height: 1; padding: 0 2; }
    DataTable { height: 1fr; margin: 0 1; border: round #3f3f46; }
    DataTable > .datatable--header { background: #27272a; color: #fafafa; text-style: bold; }
    .content-scroll { height: 1fr; margin: 0 1; padding: 0 1; }
    .section { width: 1fr; height: auto; min-height: 3; margin-bottom: 1; padding: 0 1; border: round #3f3f46; }
    .section-title { text-style: bold; color: #fafafa; }
    .compact-table { height: auto; max-height: 12; margin: 0; }
    #allocation { height: auto; min-height: 6; }
    #pool-details { height: auto; min-height: 8; }
    #pool-workers { height: auto; min-height: 4; }
    #events-table { height: 1fr; }
    #detail-scroll { margin: 0 2; padding: 1 2; border: round #3f3f46; }
    #detail-content { width: 1fr; }
    .placeholder { margin: 2; padding: 2; border: round #3f3f46; color: #a1a1aa; }
    .nav-line { dock: bottom; height: 2; padding: 0 1; background: #18181b; color: #d4d4d8; }
    #api-status { dock: bottom; height: 1; padding: 0 1; background: #18181b; color: #a1a1aa; }
    HelpScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #help-box { width: 64; height: 19; padding: 2 3; border: round #71717a; background: #18181b; }
    """

    BINDINGS = [
        Binding("1", "show_screen('overview')", "Overview", show=True),
        Binding("2", "show_screen('miners')", "Miners", show=True),
        Binding("3", "show_screen('performance')", "Performance", show=False),
        Binding("4", "show_screen('thermal')", "Thermal", show=False),
        Binding("5", "show_screen('pools')", "Pools", show=False),
        Binding("6", "show_screen('events')", "Events", show=False),
        Binding("7", "show_screen('system')", "System", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("?", "help", "Help", show=True),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "quit", "Quit", show=True),
    ]

    SCREENS = {
        "overview": OverviewScreen,
        "miners": MinersScreen,
        "performance": PerformanceScreen,
        "thermal": ThermalScreen,
        "pools": PoolsScreen,
        "events": EventsScreen,
        "system": SystemScreen,
    }

    def __init__(
        self,
        api_client: DashboardAPIClient | None = None,
        enable_periodic_refresh: bool = True,
        threaded_requests: bool = True,
    ):
        super().__init__()
        self.api_client = api_client or DashboardAPIClient(
            os.environ.get("MINER_DASHBOARD_URL", "http://127.0.0.1:5057")
        )
        self.snapshot: dict = {"miners": [], "system_status": {}}
        self.last_success_at: float | None = None
        self.last_error: str | None = None
        self.enable_periodic_refresh = enable_periodic_refresh
        self.threaded_requests = threaded_requests

    async def on_mount(self) -> None:
        await self.push_screen("overview")
        await self.refresh_data()
        if self.enable_periodic_refresh:
            self.set_interval(self.REFRESH_SECONDS, self.action_refresh)

    async def refresh_data(self) -> None:
        try:
            snapshot = (
                await asyncio.to_thread(self._fetch_data)
                if self.threaded_requests
                else self._fetch_data()
            )
        except DashboardAPIError as error:
            self.last_error = str(error)
        except Exception as error:
            self.last_error = f"Dashboard API unavailable: {error}"
        else:
            self.snapshot = snapshot
            self.last_success_at = time.monotonic()
            self.last_error = None
            for screen in self.screen_stack:
                render = getattr(screen, "render_snapshot", None)
                if render:
                    render(snapshot)
        self.update_api_status()

    def _fetch_data(self) -> dict:
        fetch = getattr(self.api_client, "get_dashboard_data", None)
        return fetch() if fetch else self.api_client.get_miners()

    def update_api_status(self) -> None:
        try:
            status = self.screen.query_one("#api-status", Static)
        except NoMatches:
            return
        updated = self.snapshot.get("updated") or "unknown"
        stale = self.last_success_at is None or time.monotonic() - self.last_success_at > self.STALE_SECONDS
        if self.last_error:
            status.update(f"[red]API ERROR[/] · showing last cached data · {self.last_error}")
        elif stale:
            status.update(f"[yellow]STALE[/] · dashboard timestamp {updated}")
        else:
            status.update(f"[green]CONNECTED[/] · dashboard timestamp {updated} · refresh 10s")

    def action_refresh(self) -> None:
        self.run_worker(self.refresh_data(), group="api-refresh", exclusive=True)

    def action_show_screen(self, name: str) -> None:
        self.switch_screen(name)
        render = getattr(self.screen, "render_snapshot", None)
        if render:
            self.screen.call_after_refresh(render, self.snapshot)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_back(self) -> None:
        if isinstance(self.screen, (MinerDetailScreen, HelpScreen)):
            self.pop_screen()
        elif not isinstance(self.screen, OverviewScreen):
            self.action_show_screen("overview")

    def open_miner_detail(self, name: str) -> None:
        miners = self.snapshot.get("miners", [])
        miner = next((item for item in miners if isinstance(item, dict) and str(item.get("name", "")) == name), None)
        if miner is not None:
            self.push_screen(MinerDetailScreen(miner))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Jorge Miner Dashboard TUI")
    parser.add_argument(
        "--url",
        default=os.environ.get("MINER_DASHBOARD_URL", "http://127.0.0.1:5057"),
        help="Dashboard base URL (default: http://127.0.0.1:5057)",
    )
    args = parser.parse_args()
    MinerDashboardApp(DashboardAPIClient(args.url)).run()


if __name__ == "__main__":
    main()
