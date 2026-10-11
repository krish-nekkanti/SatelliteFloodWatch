import traceback

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.elevation import fetch_elevation
from services.flood_data import get_regional_weather_and_flood_context
from services.risk_engine import calculate_model_based_risk

router = APIRouter()


class SiteRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    scenario: str = Field(default="moderate", pattern="^(moderate|severe|extreme)$")
    # 0..1 fraction of the mapped 100-year flood-hazard layer around the point (from the browser)
    hazard: float | None = Field(default=None, ge=0, le=1)


@router.post("/analyze-site")
async def analyze_site(payload: SiteRequest):
    try:
        elevation = await fetch_elevation(payload.lat, payload.lon)
        weather = await get_regional_weather_and_flood_context(payload.lat, payload.lon)
        analysis = calculate_model_based_risk(elevation, weather, payload.scenario, payload.hazard)
        return {
            "success": True,
            "coordinates": {"lat": payload.lat, "lon": payload.lon},
            "scenario": payload.scenario,
            **analysis,
        }
    except HTTPException:
        raise
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Site analysis failed; see backend logs.") from error