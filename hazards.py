from datetime import date, timedelta

import httpx
from fastapi import APIRouter

from services.flood_data import GDACS_EVENTS_URL, _first_point

router = APIRouter()


@router.get("/hurricanes")
async def active_hurricanes():
    """Recent tropical cyclones from GDACS (public API, no key needed)."""
    storms = []
    error_message = None
    try:
        today = date.today()
        params = {
            "eventlist": "TC",
            "fromdate": (today - timedelta(days=10)).isoformat(),
            "todate": today.isoformat(),
            "alertlevel": "green;orange;red",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(GDACS_EVENTS_URL, params=params)
            response.raise_for_status()
            for feature in response.json().get("features", []):
                props = feature.get("properties", {})
                if str(props.get("iscurrent", "true")).lower() == "false":
                    continue
                point = _first_point((feature.get("geometry") or {}).get("coordinates"))
                if point is None:
                    continue
                storms.append({
                    "name": props.get("name") or "Tropical cyclone",
                    "alertLevel": str(props.get("alertlevel", "Green")).upper(),
                    "lat": point[1],
                    "lon": point[0],
                })
    except Exception as error:
        print(f"GDACS TC request failed: {error}")
        error_message = "GDACS feed unavailable"
    return {"storms": storms, "error": error_message}