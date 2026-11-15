#!/usr/bin/env python3
"""Fetch complete Overture Maps GeoParquet layers + targeted OSM Overpass supplements
for the full İzmir Metropolitan area (26.860..27.340 E, 38.220..38.650 N).
"""
import os
import sys
import time
import subprocess
import requests
import orjson

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_BIN = os.path.join(BASE_DIR, ".venv", "bin")
OVERTURE_CLI = os.path.join(VENV_BIN, "overturemaps")
OVERTURE_DIR = os.path.join(BASE_DIR, "osm_cache", "overture")
OSM_EXTRA_DIR = os.path.join(BASE_DIR, "osm_cache", "osm_extra")

os.makedirs(OVERTURE_DIR, exist_ok=True)
os.makedirs(OSM_EXTRA_DIR, exist_ok=True)

# Full Metro İzmir bounding box (Güzelbahçe -> Kemalpaşa, Menderes -> Menemen North / Foça boundary)
LON_MIN, LAT_MIN = 26.860, 38.220
LON_MAX, LAT_MAX = 27.340, 38.650
BBOX_OVERTURE = f"{LON_MIN},{LAT_MIN},{LON_MAX},{LAT_MAX}"
BBOX_OSM = f"{LAT_MIN},{LON_MIN},{LAT_MAX},{LON_MAX}"

OVERTURE_TYPES = [
    "water",
    "land",
    "land_cover",
    "land_use",
    "infrastructure",
    "place",
    "division",
    "division_area",
    "segment",
    "building",
    "building_part",
]

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

OVERPASS_QUERIES = {
    "osm_rails_stations.json": f"""[out:json][timeout:60];
(
  node["railway"~"station|halt|tram_stop|subway_entrance"]({BBOX_OSM});
  way["railway"~"station|halt|tram_stop|platform"]({BBOX_OSM});
  node["public_transport"="station"]["station"!="bus"]({BBOX_OSM});
  way["railway"~"rail|subway|tram|light_rail|narrow_gauge"]({BBOX_OSM});
);
out geom;
""",
    "osm_building_relations.json": f"""[out:json][timeout:60];
(
  relation["building"]({BBOX_OSM});
  relation["building:part"]({BBOX_OSM});
);
out geom;
""",
    "osm_special_places.json": f"""[out:json][timeout:60];
(
  node["place"]({BBOX_OSM});
  node["amenity"~"school|university|college|hospital|clinic|place_of_worship|bus_station|ferry_terminal|marketplace|townhall|courthouse|police|fire_station|library|stadium|theatre"]({BBOX_OSM});
  node["shop"~"mall|department_store"]({BBOX_OSM});
  node["aeroway"~"aerodrome|terminal"]({BBOX_OSM});
  way["amenity"~"school|university|college|hospital|clinic|place_of_worship|bus_station|ferry_terminal|marketplace|stadium"]({BBOX_OSM});
  way["shop"~"mall|department_store"]({BBOX_OSM});
  way["aeroway"]({BBOX_OSM});
  way["man_made"~"pier|breakwater"]({BBOX_OSM});
);
out geom;
""",
    "osm_coast_water_extra.json": f"""[out:json][timeout:60];
(
  way["natural"="coastline"]({BBOX_OSM});
  way["natural"="water"]({BBOX_OSM});
  way["landuse"~"salt_pond|reservoir|harbour"]({BBOX_OSM});
  way["waterway"~"river|riverbank|canal"]({BBOX_OSM});
);
out geom;
""",
}


def download_overture():
    print("=== Downloading Overture Maps GeoParquet layers ===", flush=True)
    for t in OVERTURE_TYPES:
        out_path = os.path.join(OVERTURE_DIR, f"{t}.parquet")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
            sz_mb = os.path.getsize(out_path) / (1024 * 1024)
            print(f"  [SKIP] {t}.parquet already exists ({sz_mb:.2f} MB)", flush=True)
            continue

        tmp_path = out_path + ".tmp"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        ok = False
        for attempt in range(1, 3):
            t0 = time.time()
            cmd = [
                OVERTURE_CLI,
                "download",
                f"--bbox={BBOX_OVERTURE}",
                "-f", "geoparquet",
                "-t", t,
                "-o", tmp_path,
                "--connect_timeout", "30",
                "--request_timeout", "180",
            ]
            print(f"  [FETCH] {t}.parquet (attempt {attempt})...", flush=True)
            res = subprocess.run(cmd, capture_output=False)
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                os.replace(tmp_path, out_path)
                sz_mb = os.path.getsize(out_path) / (1024 * 1024)
                print(f"  [OK] {t}.parquet -> {sz_mb:.2f} MB ({time.time()-t0:.1f}s)", flush=True)
                ok = True
                break
            else:
                print(f"  [WARN] {t} attempt {attempt} failed (rc={res.returncode})", flush=True)
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                time.sleep(2)
        if not ok:
            print(f"  [ERR] Failed to download {t}.parquet!", flush=True)
            sys.exit(1)


def download_overpass():
    print("\n=== Downloading targeted Overpass supplements ===", flush=True)
    for fname, query in OVERPASS_QUERIES.items():
        out_path = os.path.join(OSM_EXTRA_DIR, fname)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 500:
            sz_kb = os.path.getsize(out_path) / 1024
            print(f"  [SKIP] {fname} already exists ({sz_kb:.1f} KB)", flush=True)
            continue

        ok = False
        for ep in OVERPASS_ENDPOINTS:
            t0 = time.time()
            print(f"  [OSM] Fetching {fname} via {ep.split('/')[2]}...", flush=True)
            try:
                r = requests.post(
                    ep,
                    data={"data": query},
                    timeout=140,
                    headers={"User-Agent": "IzmirCartoSim/2.0"},
                )
                if r.status_code == 200 and r.content.strip().startswith(b"{"):
                    data = orjson.loads(r.content)
                    elems = data.get("elements", [])
                    tmp_path = out_path + ".tmp"
                    with open(tmp_path, "wb") as f:
                        f.write(r.content)
                    os.replace(tmp_path, out_path)
                    print(
                        f"  [OK] {fname}: {len(elems)} elements ({len(r.content)/1024:.1f} KB, {time.time()-t0:.1f}s)",
                        flush=True,
                    )
                    ok = True
                    break
                else:
                    print(f"  [WARN] HTTP {r.status_code} from {ep}", flush=True)
            except Exception as e:
                print(f"  [WARN] Exception on {ep}: {e}", flush=True)
            time.sleep(2)
        if not ok:
            print(f"  [ERR] Failed to fetch {fname} from all Overpass mirrors!", flush=True)
            sys.exit(1)


if __name__ == "__main__":
    download_overture()
    download_overpass()
    print("\n=== ALL COMPLETE GIS DATASETS FETCHED SUCCESSFULLY ===", flush=True)
