import asyncio
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Query

router = APIRouter()

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# Headline must contain a real flood term (filters "flood of tourists"-style noise only partly).
FLOOD_TITLE = re.compile(
    r"\b(floods?|flooding|flooded|flash[- ]floods?|inundat\w*|deluge|dam (?:break|burst|failure|collapse)|"
    r"glacial lake|GLOF|overflow\w*)\b",
    re.I,
)
# Common metaphorical uses of "flood" that are not about water.
NOISE_TITLE = re.compile(
    r"\b(touris\w*|visitors|fans|customers|orders|market|stocks?|investors?|social media|inbox|"
    r"applications|tributes|messages|offers|calls|funding|bitcoin|crypto|ads?|content)\b",
    re.I,
)
# Reputable outlets, matched by domain (subdomains included). Label is shown to the user.
TRUSTED_SOURCES = {
    # Wire services
    "reuters.com": "Reuters (wire)", "apnews.com": "Associated Press (wire)", "afp.com": "AFP (wire)",
    # International broadcasters
    "bbc.com": "BBC", "bbc.co.uk": "BBC", "cnn.com": "CNN", "aljazeera.com": "Al Jazeera",
    "dw.com": "Deutsche Welle", "france24.com": "France 24", "nhk.or.jp": "NHK World",
    "abc.net.au": "ABC Australia", "cbc.ca": "CBC", "sky.com": "Sky News", "news.sky.com": "Sky News",
    "abcnews.go.com": "ABC News", "cbsnews.com": "CBS News", "nbcnews.com": "NBC News",
    "npr.org": "NPR", "pbs.org": "PBS", "voanews.com": "Voice of America", "channelnewsasia.com": "CNA",
    # Major newspapers
    "nytimes.com": "New York Times", "washingtonpost.com": "Washington Post", "theguardian.com": "The Guardian",
    "wsj.com": "Wall Street Journal", "bloomberg.com": "Bloomberg", "ft.com": "Financial Times",
    "economist.com": "The Economist", "latimes.com": "Los Angeles Times", "usatoday.com": "USA Today",
    # South Asia / regional
    "thehindu.com": "The Hindu", "hindustantimes.com": "Hindustan Times", "timesofindia.indiatimes.com": "Times of India",
    "indianexpress.com": "Indian Express", "ndtv.com": "NDTV", "dawn.com": "Dawn (Pakistan)",
    "tribune.com.pk": "Express Tribune", "kathmandupost.com": "Kathmandu Post",
    "thehimalayantimes.com": "Himalayan Times", "thedailystar.net": "The Daily Star (Bangladesh)",
    "straitstimes.com": "Straits Times", "scmp.com": "South China Morning Post", "japantimes.co.jp": "Japan Times",
    "bangkokpost.com": "Bangkok Post", "rappler.com": "Rappler", "allafrica.com": "AllAfrica",
    "africanews.com": "Africanews", "mg.co.za": "Mail & Guardian", "batimes.com.ar": "Buenos Aires Times",
    # Humanitarian / specialist
    "reliefweb.int": "ReliefWeb (UN OCHA)", "floodlist.com": "FloodList", "un.org": "United Nations",
    "who.int": "WHO", "ifrc.org": "IFRC", "wmo.int": "WMO", "noaa.gov": "NOAA", "weather.gov": "NWS",
    "nasa.gov": "NASA", "earthobservatory.nasa.gov": "NASA Earth Observatory",
}


def _outlet(domain: str):
    d = (domain or "").lower().removeprefix("www.")
    for known, label in TRUSTED_SOURCES.items():
        if d == known or d.endswith("." + known):
            return label
    return None


CACHE_SECONDS = 15 * 60
MIN_GAP_SECONDS = 5.5  # GDELT asks for at most 1 request per 5 seconds

_cache: dict = {}
_lock = asyncio.Lock()
_last_call = 0.0


def parse_articles(payload: dict, place: str = "") -> list[dict]:
    """Keep only headlines that contain a flood term; de-duplicate; newest first."""
    seen, out = set(), []
    for item in payload.get("articles", []):
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", ""))
        if not title or not url.startswith(("http://", "https://")):
            continue
        if not FLOOD_TITLE.search(title) or NOISE_TITLE.search(title):
            continue
        key = re.sub(r"\W+", " ", title.lower())
        if key in seen:
            continue
        seen.add(key)
        seen_date = str(item.get("seendate", ""))
        try:
            when = datetime.strptime(seen_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            when = None
        outlet = _outlet(item.get("domain"))
        out.append({
            "title": title,
            "url": url,
            "domain": item.get("domain"),
            "outlet": outlet,
            "trusted": outlet is not None,
            "language": item.get("language"),
            "seen": when,
            "mentionsPlace": bool(place) and place.lower() in title.lower(),
        })
    out.sort(key=lambda a: a["seen"] or "", reverse=True)
    trusted = [a for a in out if a["trusted"]][:8]
    others = [a for a in out if not a["trusted"]][:4]
    return trusted + others


async def _gdelt(client: httpx.AsyncClient, term: str, timespan: str, place: str):
    """Returns (articles, error). error is None on success, even when zero articles match."""
    global _last_call
    query = f'(flood OR flooding OR "flash flood" OR inundation) "{term}"'
    async with _lock:
        wait = MIN_GAP_SECONDS - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            response = await client.get(GDELT_URL, params={
                "query": query, "mode": "ArtList", "format": "json",
                "maxrecords": 150, "timespan": timespan, "sort": "DateDesc",
            })
        except httpx.RequestError:
            return [], "News service unreachable"
        finally:
            _last_call = time.monotonic()
    if response.status_code != 200:
        return [], f"News service returned {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        # GDELT returns an empty or plain-text body when nothing matches (or the query is rejected)
        text = response.text.strip()
        return [], (None if not text else "News service rejected the query")
    return parse_articles(payload, place), None


@router.get("/flood-news")
async def flood_news(
    place: str = Query("", max_length=80),
    country: str = Query("", max_length=60),
):
    key = (place.strip().lower(), country.strip().lower())
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    groups = {}
    async with httpx.AsyncClient(timeout=25.0) as client:
        if len(place.strip()) >= 3:
            arts, err = await _gdelt(client, place.strip(), "3d", place.strip())
            groups["local"] = {"scope": place.strip(), "window": "last 3 days", "articles": arts, "error": err}
        if len(country.strip()) >= 3:
            arts, err = await _gdelt(client, country.strip(), "7d", "")
            groups["country"] = {"scope": country.strip(), "window": "last 7 days", "articles": arts, "error": err}

    result = {
        "groups": groups,
        "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Headlines only, matched by keyword. No headlines found is NOT evidence that a place is safe.",
    }
    if all(g["error"] is None for g in groups.values()):
        _cache[key] = (time.time(), result)
    return result