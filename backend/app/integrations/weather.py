"""
Daily weather for a location and date range.

Live: Open-Meteo (no key). Dates within the next 16 days use the forecast API.
Dates further out (or in the past) use the archive API for the same calendar
days in the most recent past year as a climatological proxy; those rows are
marked ``source="open-meteo-archive-proxy"``.

Mock: recorded Open-Meteo archive responses for whole windows
(``openmeteo_archive_<slug>_<MM-DD>_<MM-DD>``), else deterministic synthetic days.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.integrations.fixtures import FIXTURE_DIR, load_fixture
from app.integrations.geocoding import Location
from app.integrations.http import HttpJson, ProviderError, UsageMeter

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_HORIZON_DAYS = 15
STORM_CONDITIONS = frozenset({"thunderstorm", "heavy_rain", "storm"})

# WMO weather interpretation codes → our condition vocabulary.
_WMO: list[tuple[range | tuple, str]] = [
    ((0,), "sunny"), ((1, 2), "partly_cloudy"), ((3,), "cloudy"), ((45, 48), "fog"),
    (range(51, 58), "light_rain"), ((61, 80), "light_rain"), ((63, 66, 67, 81), "rain"),
    ((65, 82), "heavy_rain"), (range(71, 78), "snow"), ((85, 86), "snow"), ((95, 96, 99), "thunderstorm"),
]


def wmo_condition(code: int | None) -> str:
    if code is None:
        return "unknown"
    for codes, condition in _WMO:
        if code in codes:
            return condition
    return "unknown"


class DailyWeather(BaseModel):
    date: date
    condition: str
    is_storm: bool
    weather_code: int | None = None
    temperature_high_c: float = Field(ge=-80, le=65)
    temperature_low_c: float = Field(ge=-90, le=60)
    precipitation_probability: float | None = Field(default=None, ge=0, le=1)
    precipitation_mm: float | None = Field(default=None, ge=0)
    wind_speed_kmh: float | None = Field(default=None, ge=0, le=400)
    source: str = ""

    @model_validator(mode="after")
    def high_not_below_low(self) -> "DailyWeather":
        if self.temperature_high_c < self.temperature_low_c:
            raise ValueError("temperature_high_c is below temperature_low_c")
        return self


def _shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:            # 29 Feb
        return d.replace(year=d.year + years, day=28)


def plan_request(location: Location, start: date, end: date, today: date) -> tuple[str, dict[str, Any], int]:
    """Return (url, params, years_shift). years_shift > 0 means archive proxy dates."""
    base = {"latitude": location.latitude, "longitude": location.longitude, "timezone": "auto"}
    if today <= start and end <= today + timedelta(days=FORECAST_HORIZON_DAYS):
        return FORECAST_URL, base | {
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,wind_speed_10m_max",
            "start_date": start.isoformat(), "end_date": end.isoformat(),
        }, 0
    shift = 0
    while _shift_year(end, -shift) > today - timedelta(days=7):
        shift += 1
    return ARCHIVE_URL, base | {
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        "start_date": _shift_year(start, -shift).isoformat(), "end_date": _shift_year(end, -shift).isoformat(),
    }, shift


def parse_daily(raw: Any, *, years_shift: int, source: str) -> tuple[list[DailyWeather], list[str]]:
    """Parse Open-Meteo ``daily`` arrays; invalid rows are rejected with a reason."""
    daily = raw.get("daily") if isinstance(raw, dict) else None
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        raise ProviderError("Open-Meteo response has no daily data.")

    def col(name: str, i: int) -> Any:
        values = daily.get(name)
        return values[i] if isinstance(values, list) and i < len(values) else None

    days, rejected = [], []
    for i, day in enumerate(daily["time"]):
        code = col("weather_code", i)
        condition = wmo_condition(code)
        prob = col("precipitation_probability_max", i)
        try:
            days.append(DailyWeather(
                date=_shift_year(date.fromisoformat(day), years_shift),
                condition=condition,
                is_storm=condition in STORM_CONDITIONS,
                weather_code=code,
                temperature_high_c=col("temperature_2m_max", i),
                temperature_low_c=col("temperature_2m_min", i),
                precipitation_probability=prob / 100 if isinstance(prob, (int, float)) else None,
                precipitation_mm=col("precipitation_sum", i),
                wind_speed_kmh=col("wind_speed_10m_max", i),
                source=source,
            ))
        except (ValidationError, ValueError, TypeError) as exc:
            rejected.append(f"{day}: {exc.__class__.__name__}")
    return days, rejected


class OpenMeteoWeather:
    name = "open_meteo_weather"

    def __init__(self, http: HttpJson, usage: UsageMeter, *, today: date | None = None) -> None:
        self._http = http
        self._usage = usage
        self._today = today

    async def daily(self, location: Location, start: date, end: date) -> list[DailyWeather]:
        url, params, shift = plan_request(location, start, end, self._today or date.today())
        self._usage.record(self.name)
        raw = await self._http.get(url, params=params)
        source = "open-meteo-forecast" if shift == 0 else "open-meteo-archive-proxy"
        days, _ = parse_daily(raw, years_shift=shift, source=source)
        return days


_SYNTHETIC = ["sunny", "partly_cloudy", "sunny", "cloudy", "light_rain"]


class MockWeather:
    name = "mock_weather"

    def __init__(self, usage: UsageMeter) -> None:
        self._usage = usage

    @staticmethod
    def _recorded(location: Location, start: date, end: date) -> list[DailyWeather] | None:
        """
        Recorded archive windows are named by month-day
        (``openmeteo_archive_<slug>_<MM-DD>_<MM-DD>``); a window covering the
        request is re-dated to the requested year and sliced.
        """
        if start.year != end.year:
            return None
        want = (start.strftime("%m-%d"), end.strftime("%m-%d"))
        for path in sorted(FIXTURE_DIR.glob(f"openmeteo_archive_{location.key}_*_*.json")):
            first, last = path.stem.rsplit("_", 2)[-2:]
            if not (first <= want[0] and want[1] <= last):
                continue
            raw = load_fixture(path.stem)
            shift = start.year - int(raw.get("_recorded_year", start.year))
            days, _ = parse_daily(raw, years_shift=shift, source="recorded:open-meteo-archive")
            days = [d for d in days if start <= d.date <= end]
            if len(days) == (end - start).days + 1:
                return days
        return None

    async def daily(self, location: Location, start: date, end: date) -> list[DailyWeather]:
        self._usage.record(self.name)
        recorded = self._recorded(location, start, end)
        if recorded is not None:
            return recorded
        days, d = [], start
        while d <= end:
            h = int(hashlib.sha256(f"{location.key}|{d.isoformat()}".encode()).hexdigest()[:8], 16)
            condition = _SYNTHETIC[h % len(_SYNTHETIC)]
            high = 20 + h % 12
            days.append(DailyWeather(date=d, condition=condition, is_storm=False, temperature_high_c=high,
                                     temperature_low_c=high - 7,
                                     precipitation_probability=0.6 if condition == "light_rain" else 0.1,
                                     wind_speed_kmh=8 + h % 20, source="synthetic"))
            d += timedelta(days=1)
        return days
