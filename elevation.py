import asyncio
import math
import time
from collections import OrderedDict

import httpx
from fastapi import HTTPException

DATASETS = ("aster30m", "mapzen")  # mapzen = global fallback when ASTER has a void
BASE_URL = "https://api.opentopodata.org/v1/"
MIN_INTERVAL = 1.1
CACHE_LIMIT = 2000

_cache: OrderedDict = OrderedDict()
_lock = asyncio.Lock()
_next_slot = 0.0
_blocked_until = 0.0


async def _reserve_slot() -> float:
    """Reserve a rate-limit slot under the lock; sleeping happens outside it."""
    global _next_slot
    async with _lock:
        now = time.monotonic()
        if now < _blocked_until:
            raise HTTPException(
                status_code=503,
                detail="Elevation provider is temporarily rate-limited.",
                headers={"Retry-After": str(max(1, math.ceil(_blocked_until - now)))},
            )
        start = max(now, _next_slot)
        _next_slot = start + MIN_INTERVAL
        return start - now


async def _query(dataset: str, lat: float, lon: float):
    """Return elevation (float) or None when the dataset has no value here."""
    global _blocked_until
    delay = await _reserve_slot()
    if delay > 0:
        await asyncio.sleep(delay)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(BASE_URL + dataset, params={"locations": f"{lat},{lon}"})
    except httpx.RequestError as error:
        raise HTTPException(status_code=502, detail="Could not reach the elevation provider.") from error

    if response.status_code == 429:
        _blocked_until = time.monotonic() + 60
        raise HTTPException(
            status_code=503,
            detail="Elevation provider is rate-limiting requests.",
            headers={"Retry-After": "60"},
        )

    try:
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "OK":
            raise ValueError("Provider did not return OK")
        value = data["results"][0]["elevation"]
    except (httpx.HTTPStatusError, ValueError, TypeError, KeyError, IndexError) as error:
        raise HTTPException(status_code=502, detail="Elevation provider returned an error.") from error

    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


async def fetch_elevation(lat: float, lon: float) -> float:
    key = (round(float(lat), 4), round(float(lon), 4))
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    elevation = await _query(DATASETS[0], *key)
    if elevation is None:
        fallback = await _query(DATASETS[1], *key)
        # Fallback includes ocean bathymetry; strongly negative means open water.
        if fallback is not None and fallback >= -10:
            elevation = fallback

    if elevation is None:
        raise HTTPException(
            status_code=404,
            detail="No land elevation data at this point (open water or no coverage). Try a point on land.",
        )

    _cache[key] = elevation
    _cache.move_to_end(key)
    if len(_cache) > CACHE_LIMIT:
        _cache.popitem(last=False)
    return elevation