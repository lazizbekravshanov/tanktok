"""One-time script: fill missing addresses in truckstops.json using Nominatim.

Usage: python3 scripts/fill_addresses.py
Takes ~50 min for ~3000 stops (1 req/sec Nominatim rate limit).
Saves progress every 50 stops so you can Ctrl+C and resume.
"""

import asyncio
import json
import os
import sys
import time

import aiohttp

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "truckstops.json")

STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def abbrev(state_name: str) -> str:
    return STATE_ABBREV.get(state_name.strip().lower(), state_name)


async def reverse_geocode(session: aiohttp.ClientSession, lat: float, lon: float) -> str:
    params = {
        "lat": str(lat), "lon": str(lon),
        "format": "jsonv2", "addressdetails": "1", "zoom": "18",
    }
    headers = {"User-Agent": "TankTok/1.0 (fill-addresses)"}
    try:
        async with session.get(
            "https://nominatim.openstreetmap.org/reverse",
            params=params, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return ""
            data = await resp.json()
    except Exception as e:
        print(f"  Error: {e}")
        return ""

    addr = data.get("address", {})
    parts = []
    house = addr.get("house_number", "")
    road = addr.get("road", "")
    if house and road:
        parts.append(f"{house} {road}")
    elif road:
        parts.append(road)

    city = addr.get("city") or addr.get("town") or addr.get("village") or ""
    state = addr.get("state", "")
    state_short = abbrev(state)
    if city and state_short:
        parts.append(f"{city}, {state_short}")
    elif city:
        parts.append(city)

    postcode = addr.get("postcode", "")
    if postcode:
        parts.append(postcode)

    return ", ".join(parts)


async def main():
    with open(DB_PATH) as f:
        stops = json.load(f)

    missing = [(i, s) for i, s in enumerate(stops) if not s.get("address")]
    total = len(missing)
    print(f"Total stops: {len(stops)}, missing addresses: {total}")

    if total == 0:
        print("All addresses filled!")
        return

    filled = 0
    async with aiohttp.ClientSession() as session:
        for batch_start in range(0, total, 50):
            batch = missing[batch_start:batch_start + 50]
            for idx, (i, stop) in enumerate(batch):
                addr = await reverse_geocode(session, stop["lat"], stop["lon"])
                if addr:
                    stops[i]["address"] = addr
                    filled += 1
                progress = batch_start + idx + 1
                print(f"  [{progress}/{total}] {stop.get('name', '?')}: {addr or '(failed)'}")
                # Nominatim rate limit: 1 req/sec
                await asyncio.sleep(1.05)

            # Save progress every 50
            with open(DB_PATH, "w") as f:
                json.dump(stops, f, indent=2)
            print(f"  -- Saved ({filled} filled so far) --")

    print(f"\nDone! Filled {filled}/{total} addresses.")


if __name__ == "__main__":
    asyncio.run(main())
