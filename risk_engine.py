from datetime import datetime, timezone

SAFETY_FREEBOARD_METERS = 0.6
SCENARIO_MULTIPLIERS = {"moderate": 1.0, "severe": 1.35, "extreme": 1.8}


def _tier(value, steps):
    return sum(1 for s in steps if value >= s)


def _day_level(points: int, in_zone: bool) -> str:
    """Per-day outlook level. Being inside the mapped zone raises sensitivity by one point,
    but only when there is some rain/river signal that day."""
    if points <= 0:
        return "LOW"
    if in_zone:
        points += 1
    return "WATCH" if points == 1 else "ELEVATED" if points <= 3 else "HIGH"


def _build_pipeline(hazard, hazard_level, rain_score, flow_score, alert_total,
                    rain_peak, discharge, ratio, weather_context, multiplier):
    # Stage 1: is the point inside the mapped flood-hazard region?
    in_zone = None if hazard is None else hazard >= 0.15
    if in_zone is None:
        zone_text = "Zone check unavailable"
    elif not in_zone:
        zone_text = "Outside the mapped 100-year flood zone"
    elif hazard_level and hazard_level >= 3:
        zone_text = "Inside the HIGHER-hazard (red) mapped zone"
    elif hazard_level:
        zone_text = "Inside the mapped zone (lower-hazard colours)"
    else:
        zone_text = "Inside the mapped zone (hazard class not recognised)"

    # Stage 2: do independent live sources agree?
    sources = [
        {"name": "Rain", "active": rain_score > 0, "detail": f"peak {rain_peak:.0f} mm/day"},
        {"name": "River", "active": flow_score > 0,
         "detail": f"{discharge:.0f} m3/s" + (f", {ratio:.1f}x normal" if ratio else "")},
        {"name": "Alerts", "active": alert_total > 0,
         "detail": "official or GDACS flood alert" if alert_total > 0 else "none"},
    ]
    active_n = sum(1 for src in sources if src["active"])
    if in_zone is None:
        verdict = "Zone check unavailable; live signals shown only"
    elif in_zone and active_n >= 2:
        verdict = "Zone and live signals MATCH"
    elif in_zone and active_n == 1:
        verdict = "In zone; live signals partly match"
    elif in_zone:
        verdict = "In zone; live signals quiet right now"
    elif active_n >= 2:
        verdict = "Live signals active OUTSIDE the mapped zone"
    elif active_n == 1:
        verdict = "One live signal; outside the mapped zone"
    else:
        verdict = "No match: outside zone and signals quiet"

    # Stage 3: next-7-day outlook from forecast rain + forecast river discharge
    outlook = None
    fr = weather_context.get("forecast_rain") or []
    ff = weather_context.get("forecast_flow") or []
    if fr:
        flow_by_date = {d: (q, r) for d, q, r in ff if d}
        days = []
        for i, (d, mm) in enumerate(fr):
            q, r = flow_by_date.get(d) or ((ff[i][1], ff[i][2]) if i < len(ff) else (0.0, 0.0))
            rp = _tier(mm * multiplier, (20, 40, 80))
            fp = max(_tier(r, (2, 4, 8)) if q >= 20 else 0, _tier(q, (300, 1000, 3000)))
            pts = rp + fp
            days.append({
                "date": d, "rainMm": round(mm, 1), "riverM3s": round(q), "riverRatio": round(r, 1),
                "points": pts, "level": _day_level(pts, bool(in_zone)),
            })
        pts = [d["points"] for d in days]
        first = sum(pts[:3]) / max(1, len(pts[:3]))
        last = sum(pts[-3:]) / max(1, len(pts[-3:]))
        trend = ("RISING" if last - first >= 1 else "EASING" if first - last >= 1
                 else "STEADY" if max(pts) > 0 else "QUIET")
        peak = max(days, key=lambda d: (d["points"], d["rainMm"]))
        summary = (
            "No flood-driving rain or river rise is forecast for the next 7 days."
            if peak["points"] == 0 else
            f"Peak on {peak['date']}: {peak['level']} (rain {peak['rainMm']:.0f} mm, "
            f"river {peak['riverRatio']:.1f}x normal). Trend: {trend}."
        )
        outlook = {
            "days": days, "trend": trend, "peak": peak, "summary": summary,
            "caveat": "Forecasts are less certain beyond about 3 days and can change quickly.",
        }

    return {
        "zone": {"inZone": in_zone, "text": zone_text,
                 "coveragePct": None if hazard is None else round(hazard * 100),
                 "colourClass": hazard_level},
        "match": {"verdict": verdict, "activeCount": active_n, "total": len(sources), "sources": sources},
        "outlook": outlook,
    }


def calculate_model_based_risk(
    elevation: float,
    weather_context: dict,
    scenario: str = "moderate",
    hazard: float | None = None,
    hazard_level: int | None = None,
) -> dict:
    """Heuristic screening model (NOT a hydraulic model). Every signal is returned
    with its value, rule, points and source so the result can be audited."""
    observed = float(weather_context.get("precipitation_24h_mm") or 0.0)
    peak_rain = float(weather_context.get("peak_rain_7d_mm") or 0.0)
    rain_total = float(weather_context.get("rain_total_10d_mm") or 0.0)
    discharge = float(weather_context.get("river_discharge_m3s") or 0.0)
    ratio = float(weather_context.get("river_discharge_ratio") or 0.0)
    alerts = weather_context.get("active_alerts", [])
    events = weather_context.get("recent_events", [])
    severe_alert = bool(weather_context.get("severe_alert"))
    official = weather_context.get("official_alerts", [])
    nws_covered = weather_context.get("nws_covered")
    official_warning = any("WARNING" in a["event"].upper() for a in official)
    official_watch = any("WATCH" in a["event"].upper() for a in official)
    official_score = 3 if official_warning else 2 if official_watch else 1 if official else 0
    notes = list(weather_context.get("data_notes", []))
    multiplier = SCENARIO_MULTIPLIERS.get(scenario, 1.0)

    rain_peak = max(observed, peak_rain)
    rain_score = max(
        _tier(rain_peak * multiplier, (20, 40, 80)),
        _tier(rain_total * multiplier, (50, 100, 200)),
    )
    flow_score = max(
        _tier(ratio, (2, 4, 8)) if discharge >= 20 else 0,
        _tier(discharge, (300, 1000, 3000)),
    )
    low_lying = 2 if elevation < 5 else 1 if elevation < 15 else 0

    # Events: only ORANGE/RED count. Active (<=14 days) severe = 3, older ORANGE/RED = 1, green = 0.
    older_severe = any(e.get("alertLevel") in ("ORANGE", "RED") for e in events)
    alert_score = 3 if severe_alert else 1 if older_severe else 0

    if hazard is None:
        hazard_score = 0
        notes.append("flood-hazard layer could not be sampled")
    elif hazard > 0 and hazard_level:
        # Colour class decides intensity (red = higher); coverage decides whether it is really "in" the zone.
        hazard_score = {1: 1, 2: 1, 3: 2, 4: 3}[hazard_level]
        if hazard < 0.15:
            hazard_score = min(hazard_score, 1)
    else:
        hazard_score = 3 if hazard >= 0.5 else 2 if hazard >= 0.15 else 1 if hazard > 0 else 0
        if hazard > 0:
            notes.append("hazard colour class not recognised; scored by coverage only")

    long_term = hazard_score + low_lying
    current = rain_score + flow_score + alert_score + official_score
    score = long_term + current

    if official_warning:
        status = "HIGH FLOOD HAZARD INDICATED"
        logic = "HIGH because the US National Weather Service has a flood WARNING in effect for this exact point."
    elif severe_alert:
        status = "HIGH FLOOD HAZARD INDICATED"
        logic = "HIGH because a GDACS ORANGE/RED flood event is active (ended within 14 days) within 300 km."
    elif current >= 3:
        status = "HIGH FLOOD HAZARD INDICATED"
        logic = f"HIGH because current-conditions points (rain + river + events) = {current}, which is 3 or more."
    elif current >= 2 and score >= 4:
        status = "HIGH FLOOD HAZARD INDICATED"
        logic = f"HIGH because current-conditions points = {current} (2+) and total score = {score} (4+)."
    elif current >= 2 or (current >= 1 and score >= 2):
        status = "ELEVATED MODELED FLOOD RISK"
        logic = f"ELEVATED because current-conditions points = {current} with total score = {score}."
    elif long_term >= 2:
        status = "LOW FLOOD RISK"
        logic = f"Static signals (mapped zone/terrain) = {long_term}, but current-conditions points = 0."
    else:
        status = "NO SIGNALS DETECTED IN SCREENING"
        logic = "No signal reached a scoring threshold. This does not mean the location is safe."

    rainfall_proxy = (rain_peak / 80.0) * multiplier
    discharge_proxy = min(3.0, discharge / 500.0) * multiplier
    hazard_proxy = {0: 0.0, 1: 0.25, 2: 0.5, 3: 0.75}[hazard_score]
    flood_height = round(min(4.0, max(rainfall_proxy, discharge_proxy, hazard_proxy)), 2)
    rise = round(flood_height + SAFETY_FREEBOARD_METERS, 2)
    lfe = round(elevation + rise, 2)

    nasa_date = weather_context.get("nasa_observation_date")
    if nws_covered is False:
        official_value = "not available here (US National Weather Service covers the US only)"
    elif official:
        official_value = "; ".join(a["event"] for a in official)
    elif nws_covered:
        official_value = "no flood warnings, watches or advisories in effect"
    else:
        official_value = "unavailable"

    signals = [
        {
            "name": "Official flood alerts",
            "value": official_value,
            "rule": "Warning = 3 pts (forces HIGH), Watch = 2, Advisory/other = 1",
            "points": official_score,
            "source": "US National Weather Service (api.weather.gov)",
        },
        {
            "name": "Mapped 100-year flood zone",
            "value": "unavailable" if hazard is None else (
                f"{hazard * 100:.0f}% of nearby pixels covered; colour class "
                + (f"{hazard_level}/4 (4 = darkest red)" if hazard_level else "not recognised")
            ),
            "rule": "colour class 4 = 3 pts, 3 = 2, 1-2 = 1 (needs >=15% coverage; else max 1)",
            "points": hazard_score,
            "source": "ArcGIS global flood-hazard tiles (static model)",
        },
        {
            "name": "Rainfall",
            "value": f"peak {rain_peak:.1f} mm/day, 10-day total {rain_total:.0f} mm"
                     + (f" (NASA obs. {nasa_date})" if nasa_date else ""),
            "rule": "peak >=20/40/80 mm/day or total >=50/100/200 mm = 1/2/3 pts",
            "points": rain_score,
            "source": "NASA POWER (observed, lagged) + Open-Meteo forecast (past 3 + next 7 days)",
        },
        {
            "name": "River discharge",
            "value": f"peak {discharge:.0f} m3/s" + (f", {ratio:.1f}x normal" if ratio else ""),
            "rule": "ratio >=2/4/8 (if >=20 m3/s) or flow >=300/1000/3000 = 1/2/3 pts",
            "points": flow_score,
            "source": "GloFAS via Open-Meteo Flood API (model)",
        },
        {
            "name": "Terrain",
            "value": f"{elevation:.0f} m above sea level",
            "rule": "<5 m = 2 pts, <15 m = 1",
            "points": low_lying,
            "source": "ASTER GDEM 30 m via OpenTopoData (error roughly +/-10 m)",
        },
        {
            "name": "Recent flood events within 300 km",
            "value": (", ".join(f"{e['name']} [{e['alertLevel']}, {e['distanceKm']} km]" for e in events[:3])
                      if events else "none in last 60 days"),
            "rule": "active ORANGE/RED = 3 pts (forces HIGH), older ORANGE/RED = 1, green = 0",
            "points": alert_score,
            "source": "GDACS (UN/EU) flood event list",
        },
    ]

    pipeline = _build_pipeline(
        hazard, hazard_level, rain_score, flow_score, alert_score + official_score,
        rain_peak, discharge, ratio, weather_context, multiplier,
    )

    recommendation = (
        f"Ground elevation {elevation:.1f} m. Unvalidated screening estimate of floor height: "
        f"{rise:.2f} m above grade ({lfe:.2f} m elevation). {logic} "
        "Screening tool only: not for evacuation, insurance or building decisions. "
        "Follow official local flood maps and emergency guidance."
    )
    if notes:
        recommendation += " Notes: " + "; ".join(notes) + "."

    return {
        "groundElevation": round(elevation, 2),
        "projectedFloodHeight": flood_height,
        "recommendedHeightAboveGround": rise,
        "recommendedHousingAltitude": lfe,
        "riskScore": score,
        "scoreBreakdown": {
            "rain": rain_score, "river": flow_score, "lowLying": low_lying,
            "alert": alert_score, "hazardMap": hazard_score,
            "longTerm": long_term, "current": current,
        },
        "signals": signals,
        "pipeline": pipeline,
        "statusLogic": logic,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "rainPeakMmDay": rain_peak, "rainTotal10dMm": rain_total,
            "riverDischargeM3s": discharge, "riverDischargeRatio": ratio,
            "hazardFraction": hazard, "hazardLevel": hazard_level,
        },
        "recentEvents": events,
        "officialAlerts": official,
        "floodAlertStatus": weather_context.get("flood_alert_status", "None"),
        "activeAlerts": alerts or ["No GDACS flood events within 300 km in the last 60 days"],
        "riskStatus": status,
        "recommendation": recommendation,
        "modelLimitations": (
            "Heuristic screening model; thresholds are not calibrated against historical floods. "
            "It does not replace official flood maps, hydrologic modeling, surveying, or emergency guidance."
        ),
    }