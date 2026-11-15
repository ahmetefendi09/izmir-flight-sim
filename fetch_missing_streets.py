#!/usr/bin/env python3
import json
import time
from pathlib import Path
import requests

OUT_DIR = Path(__file__).resolve().parent / "osm_cache"
LAT_MIN, LAT_MAX = 38.235, 38.640
LON_MIN, LON_MAX = 26.875, 27.325

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "IzmirStreetFetcher/1.0"})

missing = [(0, 2), (1, 3), (3, 1)]
N_GRID = 4

for gy, gx in missing:
    sub_name = f"izmir_streets_g{gy}_{gx}.json"
    sub_path = OUT_DIR / sub_name
    if sub_path.exists() and sub_path.stat().st_size > 200:
        print(f"[CACHE] {sub_name} already exists.")
        continue

    a0 = LAT_MIN + (LAT_MAX - LAT_MIN) * gy / N_GRID
    a1 = LAT_MIN + (LAT_MAX - LAT_MIN) * (gy + 1) / N_GRID
    o0 = LON_MIN + (LON_MAX - LON_MIN) * gx / N_GRID
    o1 = LON_MIN + (LON_MAX - LON_MIN) * (gx + 1) / N_GRID
    qb = f"{a0:.5f},{o0:.5f},{a1:.5f},{o1:.5f}"

    st_query = f"""
    [out:json][timeout:90];
    (
      way["highway"~"^(residential|living_street|unclassified|pedestrian|service)$"]({qb});
    );
    out geom;
    """

    success = False
    for attempt in range(8):
        url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            print(f"[FETCH] {sub_name} from {url.split('/')[2]} (attempt {attempt+1})...", flush=True)
            resp = SESSION.post(url, data={"data": st_query}, timeout=90)
            if resp.status_code == 200:
                data = resp.json()
                if "elements" in data:
                    with open(sub_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                    print(f"[OK] {sub_name}: {len(data['elements'])} elements saved.", flush=True)
                    success = True
                    break
            print(f"[WARN] HTTP {resp.status_code}, retrying...", flush=True)
        except Exception as e:
            print(f"[WARN] Error: {e}", flush=True)
        time.sleep(3 + attempt * 2)

    if not success:
        print(f"[FAIL] Could not fetch {sub_name}!", flush=True)

print("[COMPLETE] Done fetching missing tiles.")
