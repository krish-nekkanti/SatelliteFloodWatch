import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.analysis import router as sites_router
from api.hazards import router as hazards_router
from api.news import router as news_router

app = FastAPI(
    title="floodwatch Intelligence API",
    version="1.2.0",
    description=(
        "Preliminary global flood-hazard exploration API. "
        "Not for emergency response, insurance, or parcel-level safety decisions."
    ),
)

# Dev: "*". Production: replace with your exact frontend origin(s).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(sites_router, prefix="/api")
app.include_router(hazards_router, prefix="/api")
app.include_router(news_router, prefix="/api")


@app.get("/")
def read_root():
    return {
        "status": "online",
        "system": "floodwatch Engine",
        "disclaimer": "Preliminary modeled information only; not an emergency alert.",
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"success": False, "detail": "Internal server error"},
    )