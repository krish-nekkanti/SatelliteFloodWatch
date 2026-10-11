import math
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException

GDACS_EVENTS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
OPEN_METEO_FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"

INVALID_NASA_VALUES = {-99.0, -999.0, -9999.0}
ALERT_RADIUS_KM = 300.0
EVENT_WINDOW_DAYS = 60
ACTIVE_WINDOW_DAYS = 14


def _valid_nasa_value(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number in INVALID_NASA_VALUES else number


def _latest_valid_value(values: dict) -> float | None:
    for key in sorted(values, reverse=True):  # keys are YYYYMMDD
        parsed = _valid_nasa_value(values[key])
        if parsed is not None:
            return parsed
    return None


def _latest_valid_item(values: dict):
    for key in sorted(values, reverse=True):
        parsed = _valid_nasa_value(values[key])
        if parsed is not None:
            return key, parsed
    return None, None


def _distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)  # haversine handles the +/-180 wrap
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _first_point(coords):
    """Return (lon, lat) from a Point or the first vertex of nested geometry."""
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if isinstance(coords, list) and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    return None


async def get_regional_weather_and_flood_context(lat: float, lon: float) -> dict:
    context = {
        "precipitation_24h_mm": None,
        "wind_speed_kmh": None,
        "river_discharge_m3s": None,
        "river_discharge_ratio": None,
        "peak_rain_7d_mm": None,
        "rain_total_10d_mm": None,
        "flood_alert_status": "None",
        "severe_alert": False,
        "active_alerts": [],
        "recent_events": [],
        "forecast_rain": [],
        "forecast_flow": [],
        "official_alerts": [],
        "nws_covered": None,
        "data_notes": [],
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        await _add_gdacs_alerts(client, lat, lon, context)
        await _add_nws_alerts(client, lat, lon, context)
        await _add_nasa_weather(client, lat, lon, context)
        await _add_forecast_rain(client, lat, lon, context)
        await _add_river_discharge(client, lat, lon, context)

    # Rainfall is the only hard requirement; discharge is legitimately
    # missing wherever there is no modeled river.
    if context["precipitation_24h_mm"] is None and context["peak_rain_7d_mm"] is None:
        raise HTTPException(
            status_code=503,
            detail="Analysis unavailable: precipitation data could not be retrieved.",
        )

    if context["river_discharge_m3s"] is None:
        context["river_discharge_m3s"] = 0.0
        context["data_notes"].append("no modeled river discharge at this point")

    return context


def _event_date(value):
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


async def _add_gdacs_alerts(client, lat, lon, context) -> None:
    """Real flood events reported by GDACS in the last EVENT_WINDOW_DAYS days
    within ALERT_RADIUS_KM. Covers rain floods AND flash floods / glacial-lake
    outbursts that rainfall and river models miss."""
    try:
        today = datetime.now(timezone.utc).date()
        response = await client.get(
            GDACS_EVENTS_URL,
            params={
                "eventlist": "FL",
                "fromdate": (today - timedelta(days=EVENT_WINDOW_DAYS)).isoformat(),
                "todate": today.isoformat(),
                "alertlevel": "green;orange;red",
            },
        )
        response.raise_for_status()

        for feature in response.json().get("features", []):
            try:
                point = _first_point((feature.get("geometry") or {}).get("coordinates"))
                if point is None:
                    continue
                event_lon, event_lat = point
                distance = _distance_km(lat, lon, event_lat, event_lon)
                if distance > ALERT_RADIUS_KM:
                    continue

                props = feature.get("properties", {})
                level = str(props.get("alertlevel", "Green")).upper()
                name = props.get("name") or "Flood event"
                start = _event_date(props.get("fromdate"))
                end = _event_date(props.get("todate")) or start
                is_current = str(props.get("iscurrent", "")).lower() == "true"
                days_since_end = (today - end).days if end else None
                active = is_current or (days_since_end is not None and days_since_end <= ACTIVE_WINDOW_DAYS)

                context["recent_events"].append({
                    "name": name,
                    "alertLevel": level,
                    "from": start.isoformat() if start else None,
                    "to": end.isoformat() if end else None,
                    "distanceKm": round(distance),
                    "active": active,
                })
                context["active_alerts"].append(
                    f"GDACS [{level}] {name} ({round(distance)} km away"
                    + (f", ended {end.isoformat()}" if end else "") + ")"
                )
                if level in {"ORANGE", "RED"} and active:
                    context["severe_alert"] = True
                    context["flood_alert_status"] = f"Active regional flood alert: {level}"
            except (TypeError, ValueError):
                continue
        context["recent_events"].sort(key=lambda e: e["distanceKm"])
    except Exception as error:
        print(f"GDACS flood request failed: {error}")
        context["data_notes"].append("GDACS flood-event feed unavailable")


async def _add_nws_alerts(client, lat, lon, context) -> None:
    """Official US National Weather Service flood alerts for this exact point (US only)."""
    try:
        response = await client.get(
            "https://api.weather.gov/alerts/active",
            params={"point": f"{lat:.4f},{lon:.4f}"},
            headers={"User-Agent": "floodwatch (student project)", "Accept": "application/geo+json"},
        )
        if response.status_code in (400, 404):
            context["nws_covered"] = False  # point is outside NWS coverage
            return
        response.raise_for_status()
        context["nws_covered"] = True
        for feature in response.json().get("features", []):
            props = feature.get("properties", {})
            event = str(props.get("event", ""))
            if "FLOOD" in event.upper():
                context["official_alerts"].append({
                    "event": event,
                    "severity": props.get("severity"),
                    "headline": props.get("headline"),
                    "ends": props.get("ends") or props.get("expires"),
                })
    except Exception as error:
        print(f"NWS alert request failed: {error}")
        context["data_notes"].append("NWS alert feed unavailable")


async def _add_nasa_weather(client, lat, lon, context) -> None:
    try:
        end_date = datetime.now(timezone.utc).date() - timedelta(days=4)
        start_date = end_date - timedelta(days=7)

        response = await client.get(
            NASA_POWER_URL,
            params={
                "parameters": "PRECTOTCORR,WS10M_MAX",
                "community": "AG",
                "longitude": lon,
                "latitude": lat,
                "start": start_date.strftime("%Y%m%d"),
                "end": end_date.strftime("%Y%m%d"),
                "format": "JSON",
            },
        )
        response.raise_for_status()
        parameters = response.json().get("properties", {}).get("parameter", {})

        rain_date, precipitation = _latest_valid_item(parameters.get("PRECTOTCORR", {}))
        if rain_date:
            context["nasa_observation_date"] = f"{rain_date[:4]}-{rain_date[4:6]}-{rain_date[6:8]}"
        wind = _latest_valid_value(parameters.get("WS10M_MAX", {}))

        if precipitation is not None:
            context["precipitation_24h_mm"] = round(max(0.0, precipitation), 2)
        if wind is not None:
            context["wind_speed_kmh"] = round(max(0.0, wind) * 3.6, 2)
    except Exception as error:
        print(f"NASA POWER request failed: {error}")


async def _add_forecast_rain(client, lat, lon, context) -> None:
    try:
        response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "precipitation_sum",
                "past_days": 3,
                "forecast_days": 7,
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        daily = response.json().get("daily", {})
        raw = daily.get("precipitation_sum", [])
        times = daily.get("time", [])
        values = [float(v) for v in raw if v is not None]
        if values:
            context["forecast_rain"] = [(t, float(v or 0.0)) for t, v in zip(times, raw)][-7:]
            context["peak_rain_7d_mm"] = round(max(values), 2)
            context["rain_total_10d_mm"] = round(sum(values), 2)
    except Exception as error:
        print(f"Open-Meteo forecast request failed: {error}")


async def _add_river_discharge(client, lat, lon, context) -> None:
    try:
        response = await client.get(
            OPEN_METEO_FLOOD_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "river_discharge,river_discharge_median",
                "past_days": 3,
                "forecast_days": 7,
            },
        )
        response.raise_for_status()
        daily = response.json().get("daily", {})
        flow = daily.get("river_discharge", [])
        median = daily.get("river_discharge_median", [])

        peak, ratio = None, None
        for i, q in enumerate(flow):
            if q is None:
                continue
            q = float(q)
            if peak is None or q > peak:
                peak = q
            m = median[i] if i < len(median) else None
            if m and float(m) > 1.0:
                r = q / float(m)
                if ratio is None or r > ratio:
                    ratio = r

        times = daily.get("time", [])
        series = []
        for i, q in enumerate(flow):
            m = median[i] if i < len(median) else None
            qv = float(q) if q is not None else 0.0
            r = qv / float(m) if (m and float(m) > 1.0) else 0.0
            series.append((times[i] if i < len(times) else None, qv, r))
        context["forecast_flow"] = series[-7:]

        if peak is not None:
            context["river_discharge_m3s"] = round(peak, 2)
        if ratio is not None:
            context["river_discharge_ratio"] = round(ratio, 2)
    except Exception as error:
        print(f"Open-Meteo GloFAS request failed: {error}")