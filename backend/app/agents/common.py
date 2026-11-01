"""
Helpers shared by the real agents.
"""

from __future__ import annotations

from typing import Any

from app.integrations.geocoding import Location
from app.integrations.http import ProviderError

# Parent-output helpers are shared with the mock agents.
from app.agents.mocks import _currency as currency_of  # noqa: F401
from app.agents.mocks import _parents_of_type as parents_of_type  # noqa: F401
from app.agents.mocks import _shift as shift_iso  # noqa: F401
from app.agents.mocks import _travelers as travelers_of  # noqa: F401
from app.agents.mocks import stable_hash  # noqa: F401


async def locate(city: str, providers: Any) -> Location:
    """
    Coordinates for a destination. The destination airport (when known)
    anchors the geocoder so ambiguous names resolve to the right place.
    """
    near = None
    try:
        airport = await providers.flights.find_airport(city)
        if airport.latitude is not None and airport.longitude is not None:
            near = (airport.latitude, airport.longitude)
    except ProviderError:
        pass
    return await providers.geocoder.geocode(city, near=near)
