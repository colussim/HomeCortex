"""
services/get_weather.py — Weather service for Kira

Uses Open-Meteo (https://open-meteo.com):
  - Free, no API key, no registration
  - Built-in geocoding (city name → coordinates)
  - Real-time data + 3-day forecast
  - 1 to 11 km resolution depending on region

All response strings come from config/lang/<lang>.yaml via LANG.
No hardcoded language-specific text in this file.

Supported questions:
  "what's the weather like today?"
  "what's the weather in Geneva?"
  "will it rain tomorrow?"
"""

import requests
import time

try:
    from services.config_loader import LANG as _LANG, KIRA as _KIRA
except ImportError:
    _LANG = None
    _KIRA = None

# ── Weather cache ─────────────────────────────────────────────────────────────
_weather_cache: dict = {}
_CACHE_TTL = 15 * 60  # 15 minutes

# ── API URLs ──────────────────────────────────────────────────────────────────
GEOCODE_URL  = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT      = 8


# ── Language helpers ──────────────────────────────────────────────────────────

def _t(key: str, **kwargs) -> str:
    """Returns a translated string from LANG config."""
    if _LANG and hasattr(_LANG, "_d"):
        tpl = _LANG._d.get(key, "")
        if tpl:
            try:
                return tpl.format(**kwargs)
            except (KeyError, ValueError):
                return tpl
    return key


def _wmo(code: int) -> str:
    """Returns the weather condition label for a WMO code."""
    if _LANG and hasattr(_LANG, "_d"):
        codes = _LANG._d.get("wmo_codes", {})
        return codes.get(code, codes.get(str(code), str(code)))
    return str(code)


def _default_city() -> str:
    """Returns the default city from KIRA config."""
    if _KIRA:
        return _KIRA.weather_default_city
    return "Geneva"


def _geocode_language() -> str:
    """Returns the geocoding language from LANG config."""
    if _LANG and hasattr(_LANG, "_d"):
        return _LANG._d.get("weather_geocode_language", "en")
    return "en"


# ── Public interface ──────────────────────────────────────────────────────────

def run(city: str = "", days: int = 1) -> str:
    """
    Returns a natural language weather description, ready to be read aloud.
    Result cached for 15 minutes to avoid repeated HTTP calls.

    Args:
        city : city name (defaults to kira.yaml → weather.default_city)
        days : 1=today, 2=tomorrow, 3=day after tomorrow
    """
    if not city:
        city = _default_city()

    cache_key = f"{city.lower().strip()}:{days}"
    now = time.time()

    if cache_key in _weather_cache:
        entry = _weather_cache[cache_key]
        if now - entry["ts"] < _CACHE_TTL:
            print(f"  Weather cache ({now - entry['ts']:.0f}s) : {cache_key}")
            return entry["result"]

    try:
        lat, lon, city_name = _geocode(city)
        data   = _fetch_weather(lat, lon)
        result = _format_response(data, city_name, days)
        _weather_cache[cache_key] = {"ts": now, "result": result}
        print(f"  Weather API : {city_name} cached 15min")
        return result
    except GeocodingError:
        return _t("weather_city_not_found", city=city)
    except WeatherError as e:
        return _t("weather_error", error=str(e))
    except Exception as e:
        return _t("weather_generic_error", error=str(e))


# ── Geocoding ─────────────────────────────────────────────────────────────────

def _geocode(city: str) -> tuple[float, float, str]:
    """Converts a city name to coordinates (lat, lon, normalized_name)."""
    try:
        r = requests.get(
            GEOCODE_URL,
            params={"name": city, "count": 1,
                    "language": _geocode_language(), "format": "json"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            raise GeocodingError(f"City '{city}' not found")

        result    = results[0]
        lat       = result["latitude"]
        lon       = result["longitude"]
        city_name = result.get("name", city)

        # Add country if not FR/CH/BE/LU to avoid ambiguity
        country = result.get("country_code", "")
        if country not in ("FR", "CH", "BE", "LU"):
            city_name += f" ({result.get('country', country)})"

        return lat, lon, city_name

    except GeocodingError:
        raise
    except Exception as e:
        raise GeocodingError(f"Geocoding error: {e}")


# ── Weather API ───────────────────────────────────────────────────────────────

def _fetch_weather(lat: float, lon: float) -> dict:
    """Calls Open-Meteo and returns raw data."""
    params = {
        "latitude":  lat,
        "longitude": lon,
        "timezone":  "auto",
        "current": [
            "temperature_2m", "apparent_temperature",
            "relative_humidity_2m", "weather_code",
            "wind_speed_10m", "precipitation", "is_day",
        ],
        "daily": [
            "temperature_2m_max", "temperature_2m_min",
            "weather_code", "precipitation_sum",
            "precipitation_probability_max", "wind_speed_10m_max",
        ],
        "forecast_days": 3,
    }
    try:
        r = requests.get(FORECAST_URL, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if "current" not in data:
            raise WeatherError("Incomplete API response")
        return data
    except requests.exceptions.Timeout:
        raise WeatherError("Weather API timeout")
    except requests.exceptions.ConnectionError:
        raise WeatherError("Cannot reach weather service")
    except Exception as e:
        raise WeatherError(str(e))


# ── Response formatting ───────────────────────────────────────────────────────

def _format_response(data: dict, city_name: str, days: int = 1) -> str:
    if days <= 1:
        return _format_today(data, city_name)
    else:
        return _format_forecast(data, city_name, days)


def _format_today(data: dict, city_name: str) -> str:
    cur   = data["current"]
    daily = data.get("daily", {})

    temp     = round(cur.get("temperature_2m", 0))
    apparent = round(cur.get("apparent_temperature", temp))
    wind     = round(cur.get("wind_speed_10m", 0))
    precip   = cur.get("precipitation", 0) or 0
    wmo_code = cur.get("weather_code", 0)
    condition = _wmo(wmo_code)

    t_max = t_min = None
    if daily.get("temperature_2m_max"):
        t_max = round(daily["temperature_2m_max"][0])
    if daily.get("temperature_2m_min"):
        t_min = round(daily["temperature_2m_min"][0])

    rain_prob = None
    if daily.get("precipitation_probability_max"):
        rain_prob = daily["precipitation_probability_max"][0]

    parts = []

    # Main temperature
    if t_min is not None and t_max is not None:
        parts.append(_t("weather_today_minmax",
                        city=city_name, temp=temp, tmin=t_min, tmax=t_max))
    else:
        parts.append(_t("weather_today_temp", city=city_name, temp=temp))

    # Feels like if differs by more than 3°
    if abs(apparent - temp) >= 3:
        if apparent < temp:
            parts.append(_t("weather_feels_cold", apparent=apparent))
        else:
            parts.append(_t("weather_feels_warm", apparent=apparent))

    # Weather condition
    parts.append(condition)

    # Wind if notable
    if wind >= 50:
        parts.append(_t("weather_wind_strong", wind=wind))
    elif wind >= 30:
        parts.append(_t("weather_wind_moderate", wind=wind))
    elif wind >= 20:
        parts.append(_t("weather_wind_light", wind=wind))

    # Current precipitation
    if precip > 0:
        parts.append(_t("weather_precip_now", precip=f"{precip:.1f}"))

    # Rain probability
    if rain_prob is not None:
        if rain_prob >= 70:
            parts.append(_t("weather_rain_high", prob=rain_prob))
        elif rain_prob >= 40:
            parts.append(_t("weather_rain_moderate", prob=rain_prob))
        else:
            parts.append(_t("weather_rain_low"))
    elif precip == 0:
        parts.append(_t("weather_no_precip"))

    response = ", ".join(parts) + "."
    return response[0].upper() + response[1:]


def _format_forecast(data: dict, city_name: str, days: int) -> str:
    daily = data.get("daily", {})
    idx   = days - 1  # 0=today, 1=tomorrow, 2=day after

    if not daily.get("temperature_2m_max") or idx >= len(daily["temperature_2m_max"]):
        return _t("weather_no_forecast")

    t_max     = round(daily["temperature_2m_max"][idx])
    t_min     = round(daily["temperature_2m_min"][idx])
    wmo_code  = daily["weather_code"][idx] if daily.get("weather_code") else 0
    condition = _wmo(wmo_code)
    rain_sum  = daily["precipitation_sum"][idx] if daily.get("precipitation_sum") else 0
    rain_prob = daily["precipitation_probability_max"][idx] if daily.get("precipitation_probability_max") else None
    wind_max  = round(daily["wind_speed_10m_max"][idx]) if daily.get("wind_speed_10m_max") else 0

    day_label = (_t("weather_day_tomorrow") if days == 2
                 else _t("weather_day_after"))

    parts = [_t("weather_forecast_intro", day=day_label, city=city_name)]
    parts.append(_t("weather_forecast_temp", tmin=t_min, tmax=t_max))
    parts.append(condition)

    if wind_max >= 30:
        parts.append(_t("weather_forecast_wind", wind=wind_max))

    if rain_sum and rain_sum > 0.5:
        parts.append(_t("weather_forecast_rain", rain=f"{rain_sum:.0f}"))
    elif rain_prob is not None:
        if rain_prob >= 60:
            parts.append(_t("weather_forecast_rain_prob", prob=rain_prob))
        else:
            parts.append(_t("weather_forecast_rain_low"))

    response = ", ".join(parts) + "."
    return response[0].upper() + response[1:]


# ── Exceptions ────────────────────────────────────────────────────────────────

class GeocodingError(Exception):
    pass

class WeatherError(Exception):
    pass


# ── Command-line test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    city = sys.argv[1] if len(sys.argv) > 1 else _default_city()
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    print(f"Weather for '{city}' (day +{days-1}):")
    print(run(city=city, days=days))
    print("\nTomorrow:")
    print(run(city=city, days=2))
