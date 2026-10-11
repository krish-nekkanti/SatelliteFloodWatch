Floodwatch

A global flood-hazard explorer. Click anywhere on the map to see whether the spot is in a mapped flood zone, whether live data agrees, and what the next 7 days look like.

> **Screening tool only.** Not an emergency alert, engineering assessment, or basis for evacuation, insurance, or building decisions. Follow official local flood maps and guidance.

How it works

1. **Mapped zone?** Reads the global 100-year flood-hazard layer at the click (red = higher hazard).
2. **Live signals match?** Checks rainfall, river discharge, and flood alerts.
3. **Next 7 days:** Day-by-day outlook from forecast rain and river flow.

Every result lists each signal, its rule, points, and source. A news panel shows flood headlines from vetted outlets (context only, not scored). A mini-map shows NASA satellite rainfall and active cyclones.

Data sources

Esri imagery, ArcGIS flood-hazard tiles, ASTER elevation (OpenTopoData), NASA POWER, Open-Meteo (forecast + GloFAS river discharge), US National Weather Service alerts, GDACS, GDELT news, NASA GIBS/GPM IMERG, OpenStreetMap Nominatim, Leaflet.

Limitations

- Scoring thresholds are estimates, not validated against past floods.
- The floor-elevation figure is an unvalidated screening estimate, not a safe building height.
- Elevation error is about ±10 m; river data is about 5 km resolution.
- Flash floods, glacial-lake outbursts, and storm surge may be missed.
- No news or a quiet status does not mean a place is safe.
- Official alerts cover the US only.

Run locally

# Backend
cd Backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend (second terminal)
cd Frontend
python3 -m http.server 5500   # open http://localhost:5500/explorer.html
```

Team

Ryan Lin (Frontend) · Krish Nekkanti (Backend) · Salindu Amaradiwakara (Data) · Taran Yarlagadda (Frontend)