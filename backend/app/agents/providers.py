"""
Provider bundle for the real agents, chosen from settings.

  mock mode  → every provider is the mock (recorded fixtures / synthetic data).
  live mode  → keyless providers (Open-Meteo, Frankfurter, OSRM) go live;
               Duffel and Geoapify go live only when their key is set,
               otherwise a warning is logged and the mock is used.
               Every live provider is wrapped so that a failing call
               (network, HTTP error, bad payload) falls back to its mock.
Booking always uses our own mock booking provider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.integrations.booking import MockBookingProvider
from app.integrations.drift import Drift
from app.integrations.flights import DuffelFlights, MockFlights
from app.integrations.fx import FrankfurterFX, RecordedFX
from app.integrations.geocoding import MockGeocoder, OpenMeteoGeocoder
from app.integrations.http import HttpJson, ProviderError, UsageMeter
from app.integrations.places import GeoapifyPlaces, MockPlaces
from app.integrations.routing import MockRouting, OSRMRouting
from app.integrations.weather import MockWeather, OpenMeteoWeather
from app.llm.client import LLMClient, get_llm_client

logger = logging.getLogger(__name__)


class FallbackProvider:
    """Calls the live provider; on ProviderError logs a warning and uses the mock."""

    def __init__(self, live: Any, mock: Any, label: str) -> None:
        self._live = live
        self._mock = mock
        self._label = label
        self.fallbacks = 0

    @property
    def name(self) -> str:
        return self._live.name

    def __getattr__(self, attr: str) -> Any:
        live_attr = getattr(self._live, attr)
        if not callable(live_attr):
            return live_attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            try:
                return await live_attr(*args, **kwargs)
            except ProviderError as exc:
                self.fallbacks += 1
                logger.warning("%s failed (%s) — falling back to mock data.", self._label, exc)
                return await getattr(self._mock, attr)(*args, **kwargs)

        return call


@dataclass
class Providers:
    geocoder: Any
    weather: Any
    fx: Any
    routing: Any
    flights: Any
    places: Any
    booking: Any
    llm: LLMClient
    usage: UsageMeter
    modes: dict[str, str] = field(default_factory=dict)
    drift: Drift = field(default_factory=Drift)       # evaluation only; inactive by default


def build_providers(settings: Any = None, *, llm: LLMClient | None = None,
                    client: httpx.AsyncClient | None = None) -> Providers:
    from app.config import get_settings

    settings = settings or get_settings()
    usage = UsageMeter()
    drift = Drift()
    mock_geocoder = MockGeocoder(usage)
    mocks = dict(
        geocoder=mock_geocoder, weather=MockWeather(usage), fx=RecordedFX(usage), routing=MockRouting(usage),
        flights=MockFlights(usage, geocoder=mock_geocoder, drift=drift), places=MockPlaces(usage, drift=drift),
    )
    llm = llm or get_llm_client(settings)
    booking = MockBookingProvider(usage, drift=drift)

    if settings.is_mock:
        return Providers(**mocks, booking=booking, llm=llm, usage=usage,
                         modes={k: "mock" for k in mocks} | {"booking": "mock", "llm": llm.provider}, drift=drift)

    http = HttpJson(timeout_s=settings.voyagesync_http_timeout_s, client=client)
    live: dict[str, Any] = dict(
        geocoder=OpenMeteoGeocoder(http, usage), weather=OpenMeteoWeather(http, usage),
        fx=FrankfurterFX(http, usage), routing=OSRMRouting(http, usage),
    )
    if settings.duffel_access_token:
        live["flights"] = DuffelFlights(http, usage, access_token=settings.duffel_access_token)
    else:
        logger.warning("VOYAGESYNC_MODE=live but DUFFEL_ACCESS_TOKEN is empty — flights use mock data.")
    if settings.geoapify_api_key:
        live["places"] = GeoapifyPlaces(http, usage, api_key=settings.geoapify_api_key)
    else:
        logger.warning("VOYAGESYNC_MODE=live but GEOAPIFY_API_KEY is empty — places use mock data.")

    chosen = {k: FallbackProvider(live[k], mocks[k], k) if k in live else mocks[k] for k in mocks}
    modes = {k: ("live" if k in live else "mock") for k in mocks} | {"booking": "mock", "llm": llm.provider}
    return Providers(**chosen, booking=booking, llm=llm, usage=usage, modes=modes, drift=drift)
