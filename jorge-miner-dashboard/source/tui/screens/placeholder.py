from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class PlaceholderScreen(Screen):
    def __init__(self, title: str):
        super().__init__()
        self.title = title

    def compose(self) -> ComposeResult:
        yield Static("JORGE MINER DASHBOARD · READ ONLY", classes="app-title")
        yield Static(self.title.upper(), classes="screen-title")
        yield Static("Planned read-only screen — not implemented in Phase 1A.", classes="placeholder")
        yield Static("1 Overview  2 Miners  3 Performance  4 Thermal  5 Pools  6 Events  7 System", classes="nav-line")
        yield Static("API: connecting…", id="api-status")
