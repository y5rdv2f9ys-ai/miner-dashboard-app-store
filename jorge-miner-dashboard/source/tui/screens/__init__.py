"""Textual screens for the read-only dashboard client."""

from .miners import MinerDetailScreen, MinersScreen
from .overview import OverviewScreen
from .operations import EventsScreen, PerformanceScreen, PoolsScreen, SystemScreen, ThermalScreen

__all__ = [
    "EventsScreen", "MinerDetailScreen", "MinersScreen", "OverviewScreen",
    "PerformanceScreen", "PoolsScreen", "SystemScreen", "ThermalScreen",
]
