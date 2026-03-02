#!/usr/bin/env python3
"""
Fetch all major truck stop / travel center locations across the US from
OpenStreetMap via Overpass API and save to data/truckstops.json.

Run once (or periodically) to rebuild the database:
    python3 scripts/build_truckstop_db.py
"""

from __future__ import annotations

import json
import time
import sys
import os
from typing import Optional

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "truckstops.json")

# Each entry: (brand_regex for Overpass, canonical_brand)
BRANDS = [
    # Big 3
    ("Pilot|Flying J|Pilot Flying J", "Pilot Flying J"),
    ("Love's|Loves", "Love's Travel Stops"),
    # TA / Petro
    ("TravelCenters of America|Travel Centers of America|^TA$|TA Travel|TA Express", "TA Travel Centers"),
    ("Petro Stopping|Petro Shopping", "Petro Stopping Centers"),
    # Regional / Large
    ("Buc-ee|Bucees|Buc-ees", "Buc-ee's"),
    ("Sapp Bros", "Sapp Bros"),
    ("Road Ranger", "Road Ranger"),
    ("Ambest|Am Best", "Ambest"),
    ("Iowa 80", "Iowa 80"),
    ("Little America", "Little America"),
    ("Kenly 95", "Kenly 95"),
    ("QuikTrip|QT", "QuikTrip"),
    ("Sheetz", "Sheetz"),
    ("Casey's|Caseys", "Casey's"),
    ("Wawa", "Wawa"),
    ("Kwik Trip|Kwik Star", "Kwik Trip"),
    ("RaceTrac|Raceway", "RaceTrac"),
    ("Maverik", "Maverik"),
    ("Speedway", "Speedway"),
    ("Circle K", "Circle K"),
    ("Cenex", "Cenex"),
    ("Wally's|Wallys", "Wally's"),
    ("Truck Stops of America", "Truck Stops of America"),
    ("Petro-Canada", "Petro-Canada"),
    ("7-Eleven|7-eleven|Seven.Eleven", "7-Eleven"),
    ("Shell", "Shell"),
    ("BP", "BP"),
    ("Chevron", "Chevron"),
    ("ExxonMobil|Exxon|Mobil", "ExxonMobil"),
    ("Marathon", "Marathon"),
    ("Phillips 66", "Phillips 66"),
    ("Conoco", "Conoco"),
    ("Sinclair", "Sinclair"),
    ("Valero", "Valero"),
    ("Murphy USA|Murphy Express", "Murphy USA"),
    ("Costco", "Costco"),
    ("Sam's Club", "Sam's Club"),
    ("Kroger", "Kroger"),
    ("H-E-B|HEB", "H-E-B"),
    ("WilcoHess|Wilco", "WilcoHess"),
    ("Sunoco", "Sunoco"),
    ("Citgo|CITGO", "Citgo"),
    ("GetGo|Giant Eagle", "GetGo"),
]

# Truck stop ONLY brands (these we keep all results)
TRUCK_STOP_BRANDS = {
    "Pilot Flying J", "Love's Travel Stops", "TA Travel Centers",
    "Petro Stopping Centers", "Buc-ee's", "Sapp Bros", "Road Ranger",
    "Ambest", "Iowa 80", "Little America", "Kenly 95",
    "Truck Stops of America", "Wally's",
}

# For non-truck-stop brands, only keep if tagged as HGV or truck_stop
MAJOR_BRANDS_KEEP_HGV_ONLY = {
    "QuikTrip", "Sheetz", "Casey's", "Wawa", "Kwik Trip", "RaceTrac",
    "Maverik", "Speedway", "Circle K", "Cenex", "Petro-Canada",
    "7-Eleven", "Shell", "BP", "Chevron", "ExxonMobil", "Marathon",
    "Phillips 66", "Conoco", "Sinclair", "Valero", "Murphy USA",
    "Costco", "Sam's Club", "Kroger", "H-E-B", "WilcoHess",
    "Sunoco", "Citgo", "GetGo",
}

QUERY_TEMPLATE = """
[out:json][timeout:180];
area["ISO3166-1"="US"]["admin_level"="2"]->.us;
(
  nwr["amenity"="fuel"]["brand"~"{brand_regex}",i](area.us);
  nwr["amenity"="fuel"]["operator"~"{brand_regex}",i](area.us);
  nwr["amenity"="fuel"]["name"~"{brand_regex}",i](area.us);
);
out center body;
"""

# Separate query for HGV-tagged fuel stations (any brand)
HGV_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="US"]["admin_level"="2"]->.us;
(
  nwr["amenity"="fuel"]["hgv"="yes"](area.us);
  nwr["amenity"="fuel"]["fuel:HGV_diesel"="yes"](area.us);
  nwr["highway"="services"]["amenity"="fuel"](area.us);
);
out center body;
"""

# Query for name-based truck stop detection
TRUCKSTOP_NAME_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="US"]["admin_level"="2"]->.us;
(
  nwr["amenity"="fuel"]["name"~"[Tt]ruck [Ss]top|[Tt]ravel [Cc]enter|[Tt]ravel [Pp]laza|[Tt]ruck [Pp]laza",i](area.us);
);
out center body;
"""


def fetch_overpass(query: str, label: str) -> list[dict]:
    """Run an Overpass query and return elements."""
    print(f"  Fetching: {label}...", end=" ", flush=True)
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=200,
            headers={"User-Agent": "TankTok/1.0 truckstop-db-builder"},
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        print(f"{len(elements)} results")
        return elements
    except Exception as e:
        print(f"FAILED: {e}")
        return []


def parse_element(el: dict, canonical_brand: str = "") -> Optional[dict]:
    """Parse an Overpass element into a truck stop record."""
    tags = el.get("tags", {})

    lat = el.get("lat")
    lon = el.get("lon")
    if lat is None:
        center = el.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")
    if lat is None:
        return None

    name = tags.get("name") or tags.get("brand") or tags.get("operator") or canonical_brand or "Unknown"
    brand = canonical_brand or tags.get("brand", "") or tags.get("operator", "")

    addr_parts = [tags.get("addr:housenumber", ""), tags.get("addr:street", "")]
    addr = " ".join(p for p in addr_parts if p).strip()
    city = tags.get("addr:city", "")
    state = tags.get("addr:state", "")
    zipcode = tags.get("addr:postcode", "")

    if city or state:
        if addr:
            addr += f", {city} {state}".strip(", ")
        else:
            addr = f"{city} {state}".strip()

    return {
        "id": el.get("id", 0),
        "name": name,
        "brand": brand,
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "address": addr or "",
        "city": city,
        "state": state,
        "zip": zipcode,
        "hgv": tags.get("hgv", "") == "yes" or tags.get("fuel:HGV_diesel", "") == "yes",
        "truck_stop": True,
    }


def main():
    all_stops: dict[int, dict] = {}  # dedupe by OSM id

    # 1. Fetch truck stop brands (keep all)
    print("\n=== Phase 1: Truck stop brands ===")
    for brand_regex, canonical in BRANDS:
        if canonical not in TRUCK_STOP_BRANDS:
            continue
        query = QUERY_TEMPLATE.format(brand_regex=brand_regex)
        time.sleep(2)  # be polite
        elements = fetch_overpass(query, canonical)
        for el in elements:
            rec = parse_element(el, canonical)
            if rec:
                all_stops[rec["id"]] = rec

    print(f"\nTruck stop brands total: {len(all_stops)}")

    # 2. Fetch HGV-tagged stations (any brand — these are truck-friendly)
    print("\n=== Phase 2: HGV-tagged stations ===")
    time.sleep(3)
    elements = fetch_overpass(HGV_QUERY, "HGV-tagged fuel stations")
    hgv_added = 0
    for el in elements:
        osm_id = el.get("id", 0)
        if osm_id not in all_stops:
            rec = parse_element(el, "")
            if rec:
                all_stops[osm_id] = rec
                hgv_added += 1
    print(f"HGV stations added: {hgv_added}")

    # 3. Fetch name-based truck stops
    print("\n=== Phase 3: Name-based truck stops ===")
    time.sleep(3)
    elements = fetch_overpass(TRUCKSTOP_NAME_QUERY, "Name-based truck stops")
    name_added = 0
    for el in elements:
        osm_id = el.get("id", 0)
        if osm_id not in all_stops:
            rec = parse_element(el, "")
            if rec:
                all_stops[osm_id] = rec
                name_added += 1
    print(f"Name-based truck stops added: {name_added}")

    # 4. Fetch major gas brands but ONLY keep HGV-tagged ones
    print("\n=== Phase 4: Major brands (HGV-only filter) ===")
    for brand_regex, canonical in BRANDS:
        if canonical not in MAJOR_BRANDS_KEEP_HGV_ONLY:
            continue
        query = QUERY_TEMPLATE.format(brand_regex=brand_regex)
        time.sleep(2)
        elements = fetch_overpass(query, f"{canonical} (HGV filter)")
        brand_added = 0
        for el in elements:
            osm_id = el.get("id", 0)
            if osm_id in all_stops:
                continue
            tags = el.get("tags", {})
            # Only keep if HGV tagged or has "truck" in name
            is_hgv = tags.get("hgv") == "yes" or tags.get("fuel:HGV_diesel") == "yes"
            name_lower = tags.get("name", "").lower()
            has_truck_kw = any(kw in name_lower for kw in ("truck", "travel center", "travel plaza"))
            if is_hgv or has_truck_kw:
                rec = parse_element(el, canonical)
                if rec:
                    all_stops[osm_id] = rec
                    brand_added += 1
        if brand_added:
            print(f"  → kept {brand_added} HGV/truck locations for {canonical}")

    # Final
    stops_list = sorted(all_stops.values(), key=lambda s: (s["state"], s["city"], s["name"]))

    print(f"\n{'='*50}")
    print(f"TOTAL TRUCK STOPS: {len(stops_list)}")

    # Stats by brand
    brand_counts: dict[str, int] = {}
    for s in stops_list:
        b = s["brand"] or "Unknown"
        brand_counts[b] = brand_counts.get(b, 0) + 1
    print("\nBy brand:")
    for b, c in sorted(brand_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {b}: {c}")

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(stops_list, f, indent=None, separators=(",", ":"))

    size_mb = os.path.getsize(OUTPUT) / 1024 / 1024
    print(f"\nSaved to {OUTPUT} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
