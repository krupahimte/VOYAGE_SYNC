"""
Registry of the real agents (Phase 4).

``build_agent_registry()`` is what the app uses: real agents backed by the
provider bundle (mock providers in mock mode). ``build_default_registry()``
in app.agents.mocks keeps the simple Phase 2 mock agents for unit tests.
"""

from __future__ import annotations

from typing import Any

from app.agents.base import AgentRegistry
from app.agents.budget import BudgetAgent
from app.agents.flight import FlightAgent
from app.agents.hotel import HotelAgent
from app.agents.itinerary import DayPlanAgent, TransferAgent
from app.agents.providers import Providers, build_providers
from app.agents.weather import WeatherAgent
from app.core.models import NodeType


def build_agent_registry(settings: Any = None, *, providers: Providers | None = None) -> AgentRegistry:
    providers = providers or build_providers(settings)
    registry = AgentRegistry()
    registry.register_by_type(NodeType.FLIGHT, FlightAgent(providers))
    registry.register_by_type(NodeType.STAY, HotelAgent(providers))
    registry.register_by_type(NodeType.TRANSFER, TransferAgent(providers))
    registry.register_by_type(NodeType.DAY_PLAN, DayPlanAgent(providers))
    registry.register_by_type(NodeType.WEATHER, WeatherAgent(providers))
    registry.register_by_type(NodeType.BUDGET, BudgetAgent(providers))
    registry.providers = providers
    return registry
