from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.elevation import fetch_elevation
from services.flood_data import get_regional_weather_and_flood_context
from services.risk_engine import calculate_model_based_risk


router = APIRouter()


class SiteRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    scenario: str = Field("moderate", pattern="^(moderate|severe|extreme)$")


@router.post("/analyze-site")
async def analyze_site(payload: SiteRequest):
    try:
        elevation = await fetch_elevation(payload.lat, payload.lon)
        weather_context = await get_regional_weather_and_flood_context(
            payload.lat,
            payload.lon,
        )
        analysis = calculate_model_based_risk(
            elevation=elevation,
            weather_context=weather_context,
            scenario=payload.scenario,
        )

        return {
            "success": True,
            "coordinates": {
                "lat": payload.lat,
                "lon": payload.lon,
            },
            "scenario": payload.scenario,
            **analysis,
        }

    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Upstream data error: {error}")