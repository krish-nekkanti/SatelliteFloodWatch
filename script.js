const FLOOD_MODEL_URL =
  "https://tiles.arcgis.com/tiles/RTK5Unh1Z71JKIiR/arcgis/rest/services/WRF_2024_GLO_I3_2/MapServer";

const BACKEND_API_URL =
  window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    ? "http://localhost:8000/api/analyze-site"
    : "/api/analyze-site";
const API_BASE = BACKEND_API_URL.replace("/analyze-site", "");

const map = L.map("map", {
  center: [20, 0],
  zoom: 2,
  minZoom: 2,
  worldCopyJump: true,
  zoomControl: false,
});

L.control.zoom({ position: "topright" }).addTo(map);

const satelliteLayer = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" },
);

const floodLayer = L.tileLayer(FLOOD_MODEL_URL + "/tile/{z}/{y}/{x}", {
  opacity: 0.78,
  maxZoom: 19,
  attribution:
    '<a href="' + FLOOD_MODEL_URL + '" target="_blank" rel="noopener">Global flood hazard model</a>',
});

satelliteLayer.addTo(map);
floodLayer.addTo(map);

let selectedMarker = null;
let reverseLookupId = 0;
let siteRequestId = 0;
let clickTimer;

// UI elements
const locationName = document.getElementById("selected-name");
const locationCoordinates = document.getElementById("selected-coordinates");
const coordinatesDisplay = document.getElementById("coordinates");
const mapLocation = document.getElementById("map-location");
const searchStatus = document.getElementById("search-status");

const metricElevation = document.getElementById("metric-elevation");
const metricLfe = document.getElementById("metric-lfe");
const metricRisk = document.getElementById("metric-risk");
const metricRecommendation = document.getElementById("metric-recommendation");

function escapeHTML(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  })[c]);
}

function formatCoordinate(value, positive, negative) {
  return `${Math.abs(value).toFixed(4)}° ${value >= 0 ? positive : negative}`;
}

function updateCoordinates(lat, lon) {
  if (coordinatesDisplay) {
    coordinatesDisplay.textContent = `LAT ${lat.toFixed(4)}° · LON ${lon.toFixed(4)}°`;
  }
}

function buildLocationNameFromAddress(address = {}) {
  const parts = [
    address.city || address.town || address.village || address.hamlet || address.municipality,
    address.county,
    address.state || address.region || address.state_district,
    address.country,
  ].filter(Boolean);
  if (!parts.length) return "Selected area";
  return parts.slice(0, 3).join(", ") || address.country || "Selected area";
}

async function reverseGeocodeLocation(lat, lon, fallbackName = "Selected area") {
  const lookupId = ++reverseLookupId;
  try {
    const params = new URLSearchParams({
      format: "jsonv2", lat: String(lat), lon: String(lon), zoom: "8", addressdetails: "1",
    });
    const response = await fetch(`https://nominatim.openstreetmap.org/reverse?${params}`);
    if (!response.ok) throw new Error("Reverse geocode unavailable");

    const data = await response.json();
    const placeName = buildLocationNameFromAddress(data.address || {});
    const displayName = placeName !== "Selected area" ? placeName : data.display_name || fallbackName;
    if (lookupId !== reverseLookupId) return;

    if (selectedMarker) {
      selectedMarker.bindPopup(`<strong>${escapeHTML(displayName)}</strong><br>${lat.toFixed(5)}, ${lon.toFixed(5)}`);
    }
    if (locationName) locationName.textContent = displayName;
    if (mapLocation) mapLocation.textContent = displayName;

    const a = data.address || {};
    const place = a.city || a.town || a.village || a.municipality || a.county || a.state_district || a.state || "";
    loadFloodNews(place, a.country || "");
  } catch (error) {
    console.warn("Reverse geocode failed:", error);
    if (lookupId !== reverseLookupId) return;
    if (locationName) locationName.textContent = fallbackName;
    if (mapLocation) mapLocation.textContent = fallbackName;
  }
}

let newsRequestId = 0;

function setNewsMessage(text) {
  const box = document.getElementById("news-box");
  if (!box) return;
  const p = document.createElement("p");
  p.className = "signal-meta";
  p.textContent = text;
  box.replaceChildren(p);
}

async function loadFloodNews(place, country) {
  const box = document.getElementById("news-box");
  if (!box) return;
  const requestId = ++newsRequestId;
  setNewsMessage("Checking recent flood reporting (can take about 10 seconds)...");

  try {
    const params = new URLSearchParams({ place, country });
    const response = await fetch(`${API_BASE}/flood-news?${params}`);
    if (!response.ok) throw new Error("news endpoint error");
    const data = await response.json();
    if (requestId !== newsRequestId) return;

    box.replaceChildren();
    Object.values(data.groups || {}).forEach((group) => {
      const title = document.createElement("div");
      title.className = "news-group";
      title.textContent = `${group.scope} · ${group.window}`;
      box.appendChild(title);

      if (group.error) {
        const err = document.createElement("div");
        err.className = "signal-meta";
        err.textContent = `News check failed (${group.error}). This says nothing about flood risk.`;
        box.appendChild(err);
        return;
      }
      if (!group.articles.length) {
        const none = document.createElement("div");
        none.className = "signal-meta";
        none.textContent = "No flood headlines found. This is NOT evidence the area is safe: coverage can be sparse, delayed, or in other languages.";
        box.appendChild(none);
        return;
      }
      let shownOtherLabel = false;
      group.articles.forEach((art) => {
        if (!art.trusted && !shownOtherLabel) {
          shownOtherLabel = true;
          const label = document.createElement("div");
          label.className = "signal-meta";
          label.textContent = "Other sources (not verified by this app):";
          box.appendChild(label);
        }
        const row = document.createElement("div");
        row.className = "news-item" + (art.trusted ? " news-trusted" : "");
        const link = document.createElement("a");
        link.href = art.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = art.title;
        const meta = document.createElement("div");
        meta.className = "signal-meta";
        meta.textContent = `${art.outlet || art.domain || "source"} · ${art.seen ? art.seen.slice(0, 10) : "date unknown"}`;
        const open = document.createElement("a");
        open.href = art.url;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        open.className = "news-open";
        open.textContent = "Read full article ↗";
        row.append(link, meta, open);
        box.appendChild(row);
      });
    });

    const note = document.createElement("div");
    note.className = "signal-meta";
    note.textContent = `${data.note} Checked ${data.checkedAt} UTC. News is shown for context and is not part of the risk score.`;
    box.appendChild(note);
  } catch (error) {
    if (requestId !== newsRequestId) return;
    setNewsMessage("Could not load news reports. This says nothing about flood risk.");
  }
}

// Fraction (0..1) of pixels around the point covered by the mapped flood-hazard layer.
// Returns 0 when there is no tile (no mapped hazard) and null if it can't be read (e.g. CORS).
const LEGEND_STOPS = [[240, 217, 121], [216, 166, 92], [188, 114, 80], [148, 78, 70]]; // light yellow -> dark red
function classifyHazardColor(r, g, b) {
  let best = -1;
  let bestD = Infinity;
  LEGEND_STOPS.forEach((stop, i) => {
    const d = Math.hypot(r - stop[0], g - stop[1], b - stop[2]);
    if (d < bestD) { bestD = d; best = i; }
  });
  return bestD <= 90 ? best + 1 : null; // null = colour not recognised
}

async function sampleFloodHazard(lat, lon) {
  const z = 8;
  const n = 2 ** z;
  const latRad = (lat * Math.PI) / 180;
  const x = ((((lon + 180) / 360) * n) % n + n) % n;
  const y = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  const tx = Math.floor(x);
  const ty = Math.floor(y);
  if (ty < 0 || ty >= n) return { coverage: 0, level: null, rgb: null };

  try {
    const res = await fetch(`${FLOOD_MODEL_URL}/tile/${z}/${ty}/${tx}`);
    if (!res.ok) return { coverage: 0, level: null, rgb: null };
    const bmp = await createImageBitmap(await res.blob());

    const canvas = document.createElement("canvas");
    canvas.width = bmp.width;
    canvas.height = bmp.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bmp, 0, 0);

    const px = Math.floor((x - tx) * bmp.width);
    const py = Math.floor((y - ty) * bmp.height);
    const r = 3;
    const x0 = Math.max(0, px - r);
    const y0 = Math.max(0, py - r);
    const w = Math.min(bmp.width - x0, 2 * r + 1);
    const h = Math.min(bmp.height - y0, 2 * r + 1);
    const data = ctx.getImageData(x0, y0, w, h).data;

    let covered = 0, sr = 0, sg = 0, sb = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] > 40) { covered++; sr += data[i]; sg += data[i + 1]; sb += data[i + 2]; }
    }
    if (!covered) return { coverage: 0, level: null, rgb: null };
    const rgb = [Math.round(sr / covered), Math.round(sg / covered), Math.round(sb / covered)];
    return { coverage: covered / (w * h), level: classifyHazardColor(...rgb), rgb };
  } catch (error) {
    console.warn("Hazard sampling failed:", error);
    return null;
  }
}

// Three-stage view: 1) in mapped zone? 2) do live signals match? 3) next-7-day outlook.
function renderPipeline(data) {
  const box = document.getElementById("risk-pipeline");
  if (!box) return;
  box.replaceChildren();
  const pipe = data.pipeline;
  if (!pipe) return;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const step = (num, title) => {
    const card = el("div", "pipe-step");
    const head = el("div", "pipe-head");
    head.append(el("span", "pipe-num", String(num)), el("strong", "", title));
    card.appendChild(head);
    box.appendChild(card);
    return card;
  };

  // Step 1
  const s1 = step(1, "Is this spot in a mapped flood zone?");
  const zoneTag = pipe.zone.inZone === null ? "UNKNOWN" : pipe.zone.inZone ? "YES" : "NO";
  const t1 = el("span", "pipe-tag " + (pipe.zone.inZone ? "tag-yes" : "tag-no"), zoneTag);
  s1.append(t1, el("div", "pipe-text", pipe.zone.text));
  if (pipe.zone.coveragePct !== null) {
    s1.appendChild(el("div", "signal-meta", `${pipe.zone.coveragePct}% of nearby map pixels are in the zone`));
  }

  // Step 2
  const s2 = step(2, "Do live signals match the map?");
  s2.appendChild(el("div", "pipe-text", `${pipe.match.verdict} (${pipe.match.activeCount}/${pipe.match.total} sources active)`));
  const chips = el("div", "pipe-chips");
  pipe.match.sources.forEach((src) => {
    const chip = el("span", "pipe-chip " + (src.active ? "chip-on" : "chip-off"), `${src.active ? "●" : "○"} ${src.name}: ${src.detail}`);
    chips.appendChild(chip);
  });
  s2.appendChild(chips);

  // Step 3
  const s3 = step(3, "Upcoming risk: next 7 days");
  if (!pipe.outlook) {
    s3.appendChild(el("div", "pipe-text", "Forecast unavailable right now."));
    return;
  }
  s3.appendChild(el("div", "pipe-text", pipe.outlook.summary));
  const strip = el("div", "pipe-strip");
  pipe.outlook.days.forEach((day) => {
    const col = el("div", "pipe-day");
    const bar = el("div", "pipe-bar lvl-" + day.level.toLowerCase());
    bar.style.height = `${10 + Math.min(day.points, 6) * 9}px`;
    bar.title = `${day.date}: ${day.level} · rain ${day.rainMm} mm · river ${day.riverRatio}x normal`;
    const label = el("div", "pipe-daylabel");
    const d = new Date(day.date + "T00:00:00");
    label.textContent = isNaN(d) ? "" : d.toLocaleDateString(undefined, { weekday: "short" }).slice(0, 2);
    col.append(bar, label);
    strip.appendChild(col);
  });
  s3.appendChild(strip);
  s3.appendChild(el("div", "signal-meta", pipe.outlook.caveat));
}

// Builds the "Why this result" table with DOM nodes (no innerHTML, so no injection risk).
function renderSignals(data) {
  const box = document.getElementById("signal-breakdown");
  if (!box) return;
  box.replaceChildren();

  const logic = document.createElement("p");
  logic.className = "signal-logic";
  logic.textContent = data.statusLogic || "";
  box.appendChild(logic);

  (data.signals || []).forEach((sig) => {
    const row = document.createElement("div");
    row.className = "signal-row" + (sig.points > 0 ? " signal-active" : "");

    const head = document.createElement("div");
    head.className = "signal-head";
    const name = document.createElement("strong");
    name.textContent = sig.name;
    const pts = document.createElement("span");
    pts.textContent = `${sig.points} pt${sig.points === 1 ? "" : "s"}`;
    head.append(name, pts);

    const value = document.createElement("div");
    value.className = "signal-value";
    value.textContent = sig.value;

    const meta = document.createElement("div");
    meta.className = "signal-meta";
    meta.textContent = `Rule: ${sig.rule} · Source: ${sig.source}`;

    row.append(head, value, meta);
    box.appendChild(row);
  });

  const stamp = document.createElement("div");
  stamp.className = "signal-meta";
  stamp.textContent = `Computed ${data.generatedAt || ""} UTC · total score ${data.riskScore}`;
  box.appendChild(stamp);
}

async function fetchSiteIntelligence(lat, lon) {
  const requestId = ++siteRequestId;
  metricElevation.textContent = "Querying model...";
  metricLfe.textContent = "Calculating...";
  metricRisk.textContent = "Processing telemetry...";
  metricRisk.style.color = "";
  metricRecommendation.textContent = "Running terrain, rainfall, river-discharge, and alert checks...";
  document.getElementById("signal-breakdown")?.replaceChildren();
  document.getElementById("risk-pipeline")?.replaceChildren();

  try {
    const sample = await sampleFloodHazard(lat, lon);
    if (requestId !== siteRequestId) return;
    console.info("Hazard sample (coverage, colour class, avg RGB):", sample);

    const response = await fetch(BACKEND_API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat, lon, scenario: "moderate",
        hazard: sample ? sample.coverage : null,
        hazard_level: sample ? sample.level : null,
      }),
    });
    const data = await response.json();
    if (requestId !== siteRequestId) return;
    if (!response.ok || data.success === false) {
      throw new Error(data.detail || "Backend calculation failed.");
    }

    console.info("Site analysis:", data.riskScore, data.scoreBreakdown, data.inputs);
    renderPipeline(data);
    renderSignals(data);

    metricElevation.textContent = data.groundElevation != null ? `${data.groundElevation} m` : "Unavailable";
    metricLfe.textContent =
      data.recommendedHousingAltitude != null ? `${data.recommendedHousingAltitude} m` : "Unavailable";
    metricRisk.textContent = data.riskStatus || "Not evaluated";
    const status = String(data.riskStatus || "").toUpperCase();
    metricRisk.style.color = status.includes("HIGH")
      ? "#d63031"
      : status.includes("ELEVATED")
        ? "#e67e22"
        : status.includes("MAPPED")
          ? "#b8860b"
          : "#2d6aa5";
    metricRecommendation.textContent = data.recommendation || "Preliminary assessment unavailable.";
  } catch (error) {
    if (requestId !== siteRequestId) return;
    const offline = error instanceof TypeError;
    metricElevation.textContent = "Unavailable";
    metricLfe.textContent = "Unavailable";
    metricRisk.textContent = offline ? "API Error" : "No data";
    metricRisk.style.color = "";
    metricRecommendation.textContent = offline
      ? "Could not reach the model server. Is uvicorn running on port 8000?"
      : error.message;
  }
}

function selectLocation(lat, lon, name = "Selected location") {
  if (selectedMarker) map.removeLayer(selectedMarker);

  selectedMarker = L.marker([lat, lon]).addTo(map);
  selectedMarker.bindPopup(`<strong>${escapeHTML(name)}</strong><br>${lat.toFixed(5)}, ${lon.toFixed(5)}`);

  if (locationName) locationName.textContent = name;
  if (locationCoordinates) {
    locationCoordinates.textContent = `${formatCoordinate(lat, "N", "S")} · ${formatCoordinate(lon, "E", "W")}`;
  }
  if (mapLocation) mapLocation.textContent = name;
  updateCoordinates(lat, lon);

  fetchSiteIntelligence(lat, lon);
  setNewsMessage("Waiting for place name...");
  if (typeof radarMap !== "undefined" && radarMap) radarMap.setView([lat, lon], 5);
}

map.on("click", (event) => {
  const { lat, lng } = event.latlng;
  selectLocation(lat, lng, "Selected area");
  clearTimeout(clickTimer);
  clickTimer = setTimeout(() => reverseGeocodeLocation(lat, lng, "Selected area"), 900);
});

// Layer toggle
const layerToggleButtons = document.querySelectorAll(".layer-toggle-button");
layerToggleButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const showFloodHazard = button.dataset.layerMode === "flood";
    satelliteLayer.addTo(map);
    if (showFloodHazard) floodLayer.addTo(map);
    else map.removeLayer(floodLayer);
    layerToggleButtons.forEach((b) => {
      const active = b === button;
      b.classList.toggle("active", active);
      b.setAttribute("aria-pressed", String(active));
    });
  });
});

const resetViewBtn = document.getElementById("reset-view");
if (resetViewBtn) {
  resetViewBtn.addEventListener("click", () => {
    map.flyTo([20, 0], 2, { duration: 1.2 });
    if (mapLocation) mapLocation.textContent = "Earth · Global view";
  });
}

// Search
const searchForm = document.getElementById("search-form");
if (searchForm) {
  searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const queryInput = document.getElementById("location-search");
    const query = queryInput ? queryInput.value.trim() : "";

    if (!query) {
      if (searchStatus) searchStatus.textContent = "Enter a city, location, or ZIP code.";
      return;
    }
    if (searchStatus) searchStatus.textContent = "Searching for location...";

    try {
      const params = new URLSearchParams({ format: "jsonv2", limit: "1", addressdetails: "1" });
      if (/^\d{5}(?:-\d{4})?$/.test(query)) {
        params.set("postalcode", query);
        params.set("countrycodes", "us");
      } else {
        params.set("q", query);
      }

      const response = await fetch(`https://nominatim.openstreetmap.org/search?${params}`);
      if (!response.ok) throw new Error("Location search unavailable.");

      const results = await response.json();
      if (!results.length) {
        if (searchStatus) searchStatus.textContent = "No matching location found.";
        return;
      }

      const result = results[0];
      const lat = Number(result.lat);
      const lon = Number(result.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) throw new Error("Invalid coordinates.");

      map.flyTo([lat, lon], 11, { duration: 1.5 });
      selectLocation(lat, lon, result.name || query);
      reverseGeocodeLocation(lat, lon, result.name || query);
      if (searchStatus) searchStatus.textContent = "Location found.";
    } catch (error) {
      if (searchStatus) searchStatus.textContent = error.message || "Search failed.";
    }
  });
}

// Rainfall radar + storms mini-map (right panel)
let radarMap = null;
const radarEl = document.getElementById("radar-mini");
if (radarEl) {
  radarMap = L.map(radarEl, { zoomControl: false, maxZoom: 7 }).setView([20, 0], 3);

  // Basemap: same Esri imagery service the main map already uses (no key needed).
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 7, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" },
  ).addTo(radarMap);
  setTimeout(() => radarMap.invalidateSize(), 300);

  // Rainfall: NASA GPM IMERG near-real-time satellite precipitation via NASA GIBS (free, no API key).
  // "default" as the time asks GIBS for its most recent available data.
  const rainLayer = L.tileLayer(
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/IMERG_Precipitation_Rate/default/default/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png",
    {
      opacity: 0.8,
      maxNativeZoom: 6,
      maxZoom: 7,
      attribution: 'Rainfall: <a href="https://earthdata.nasa.gov/gibs" target="_blank" rel="noopener">NASA GIBS</a> / GPM IMERG',
    },
  ).addTo(radarMap);

  let rainErrors = 0;
  rainLayer.on("tileerror", () => {
    rainErrors += 1;
    const list = document.getElementById("rain-status");
    if (rainErrors === 6 && list) {
      list.textContent = "Satellite rainfall layer is not loading right now.";
    }
  });

  fetch(`${API_BASE}/hurricanes`)
    .then((r) => r.json())
    .then(({ storms }) => {
      const list = document.getElementById("storm-list");
      if (list) {
        list.textContent = storms.length
          ? storms.map((s) => `${s.name} (${s.alertLevel})`).join(" · ")
          : "No active tropical cyclones reported.";
      }
      storms.forEach((s) =>
        L.circleMarker([s.lat, s.lon], { radius: 8, color: "#c0392b", fillOpacity: 0.7 })
          .bindPopup(`<strong>${escapeHTML(s.name)}</strong><br>Alert: ${escapeHTML(s.alertLevel)}`)
          .addTo(radarMap),
      );
    })
    .catch(() => {
      const list = document.getElementById("storm-list");
      if (list) list.textContent = "Storm feed unavailable.";
    });
}

document.querySelector(".map-loading")?.remove();
console.info("floodwatch initialized successfully.");