#!/usr/bin/env python3
"""
Gercek Izmir OpenStreetMap (Overpass API) ve 30m AWS Terrarium DEM veri indirici.
Tum veriler yerel osm_cache/ klasorune onbelleklenir.
"""
import json
import math
import os
import sys
import time
from pathlib import Path
import requests

OUT_DIR = Path(__file__).resolve().parent / "osm_cache"
OUT_DIR.mkdir(parents=True, exist_ok=True)
DEM_DIR = OUT_DIR / "dem_tiles"
DEM_DIR.mkdir(parents=True, exist_ok=True)

# Izmir metropol tam sinirlari (Adnan Menderes -> Menemen, Guzelbahce/Tuzla -> Belkahve)
LAT_MIN, LAT_MAX = 38.235, 38.640
LON_MIN, LON_MAX = 26.875, 27.325

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "IzmirRealMapBuilder/1.0 (local-flight-sim)"})


def fetch_overpass(query: str, cache_name: str) -> dict:
    cache_path = OUT_DIR / cache_name
    if cache_path.exists() and cache_path.stat().st_size > 200:
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "elements" in data:
                print(f"[CACHE] {cache_name}: {len(data['elements'])} oge", flush=True)
                return data
        except Exception:
            pass

    for attempt in range(6):
        url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            print(f"[OVERPASS] {cache_name} indiriliyor ({url.split('/')[2]}, deneme {attempt+1})...", flush=True)
            resp = SESSION.post(url, data={"data": query}, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                if "elements" in data:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                    print(f"[OK] {cache_name}: {len(data['elements'])} oge kaydedildi.", flush=True)
                    return data
            print(f"[WARN] HTTP {resp.status_code} ({url.split('/')[2]}), bekleniyor...", flush=True)
        except Exception as e:
            print(f"[WARN] {url.split('/')[2]} hata: {e}", flush=True)
        time.sleep(4 + attempt * 3)

    raise RuntimeError(f"Overpass indirilemedi: {cache_name}")


def deg2tile(lat_deg: float, lon_deg: float, zoom: int):
    lat_rad = math.radians(lat_deg)
    n = 1 << zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def download_terrarium_dem(zoom: int = 12):
    x0, y1 = deg2tile(LAT_MIN - 0.02, LON_MIN - 0.02, zoom)
    x1, y0 = deg2tile(LAT_MAX + 0.02, LON_MAX + 0.02, zoom)
    total = (x1 - x0 + 1) * (y1 - y0 + 1)
    print(f"[DEM] Terrarium z={zoom} karolari ({x0}..{x1}, {y0}..{y1}, toplam {total} karo)...", flush=True)
    meta = {"zoom": zoom, "x0": x0, "x1": x1, "y0": y0, "y1": y1}
    with open(DEM_DIR / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)

    done = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            p = DEM_DIR / f"z{zoom}_{tx}_{ty}.png"
            if p.exists() and p.stat().st_size > 100:
                done += 1
                continue
            url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{tx}/{ty}.png"
            for attempt in range(4):
                try:
                    r = SESSION.get(url, timeout=30)
                    if r.status_code == 200 and len(r.content) > 100:
                        p.write_bytes(r.content)
                        break
                except Exception:
                    time.sleep(1.5)
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  DEM ilerleme: {done}/{total}", flush=True)


def main():
    # 1. DEM yukseklik karolari (gercek Izmir dag, tepe, Egekent-2 yamac, ova ve korfez)
    download_terrarium_dem(zoom=12)

    # 2. Ana yapi: kiyi, IZBAN/TCDD/Tramvay/Metro, otoyol/ana caddeler, havalimanlari
    core_bbox = f"{LAT_MIN},{LON_MIN},{LAT_MAX},{LON_MAX}"
    core_query = f"""
    [out:json][timeout:120];
    (
      way["natural"="coastline"]({core_bbox});
      way["railway"~"^(rail|tram|light_rail|subway)$"]({core_bbox});
      node["railway"~"^(station|halt|tram_stop)$"]({core_bbox});
      way["aeroway"~"^(runway|taxiway|apron|terminal)$"]({core_bbox});
      way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link)$"]({core_bbox});
      way["waterway"~"^(river|stream|canal)$"]({core_bbox});
      node["place"~"^(city|town|suburb|quarter|neighbourhood)$"]({core_bbox});
    );
    out geom;
    """
    fetch_overpass(core_query, "izmir_core.json")
    time.sleep(1.5)

    # 3. Arazi ortusu, parklar, bahceler, yesil alanlar, ormanlar, tuzlalar (2x2 karo)
    lat_mid = 0.5 * (LAT_MIN + LAT_MAX)
    lon_mid = 0.5 * (LON_MIN + LON_MAX)
    quads = [
        ("sw", LAT_MIN, LON_MIN, lat_mid, lon_mid),
        ("se", LAT_MIN, lon_mid, lat_mid, LON_MAX),
        ("nw", lat_mid, LON_MIN, LAT_MAX, lon_mid),
        ("ne", lat_mid, lon_mid, LAT_MAX, LON_MAX),
    ]
    for qname, a0, o0, a1, o1 in quads:
        qb = f"{a0:.5f},{o0:.5f},{a1:.5f},{o1:.5f}"
        lu_query = f"""
        [out:json][timeout:120];
        (
          way["leisure"~"^(park|garden|pitch|recreation_ground|playground|stadium|nature_reserve)$"]({qb});
          relation["leisure"~"^(park|garden|recreation_ground|nature_reserve)$"]({qb});
          way["landuse"~"^(forest|grass|meadow|orchard|village_green|industrial|commercial|retail|residential|salt_pond|recreation_ground|cemetery|military|farmland)$"]({qb});
          relation["landuse"~"^(forest|grass|industrial|salt_pond|military)$"]({qb});
          way["natural"~"^(wood|scrub|wetland|water|beach|sand|salt_pond)$"]({qb});
          relation["natural"~"^(wood|wetland|water)$"]({qb});
        );
        out geom;
        """
        fetch_overpass(lu_query, f"izmir_landuse_{qname}.json")
        time.sleep(1.5)

    # 4. Tum ara sokaklar (4x4 daha kucuk grid ile timeout ve 504 olmadan hizlica indir)
    N_GRID = 4
    for gy in range(N_GRID):
        for gx in range(N_GRID):
            sub_name = f"izmir_streets_g{gy}_{gx}.json"
            sub_path = OUT_DIR / sub_name
            if sub_path.exists() and sub_path.stat().st_size > 200:
                print(f"[CACHE] {sub_name} hazir.", flush=True)
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
            try:
                fetch_overpass(st_query, sub_name)
            except Exception as e:
                print(f"[WARN] {sub_name} alinamadi: {e}", flush=True)
            time.sleep(1.0)

    # 5. Tum gercek Izmir binalari & ozel yapilar (4x4 detay karolari)
    B_LAT0, B_LAT1 = 38.260, 38.635
    B_LON0, B_LON1 = 26.940, 27.285
    grid_n = 4
    for gy in range(grid_n):
        for gx in range(grid_n):
            b_name = f"izmir_bldgs_{gy}_{gx}.json"
            b_path = OUT_DIR / b_name
            if b_path.exists() and b_path.stat().st_size > 200:
                print(f"[CACHE] {b_name} hazir.", flush=True)
                continue
            a0 = B_LAT0 + (B_LAT1 - B_LAT0) * gy / grid_n
            a1 = B_LAT0 + (B_LAT1 - B_LAT0) * (gy + 1) / grid_n
            o0 = B_LON0 + (B_LON1 - B_LON0) * gx / grid_n
            o1 = B_LON0 + (B_LON1 - B_LON0) * (gx + 1) / grid_n
            bb = f"{a0:.5f},{o0:.5f},{a1:.5f},{o1:.5f}"
            b_query = f"""
            [out:json][timeout:90];
            (
              way["building"]({bb});
            );
            out geom;
            """
            try:
                fetch_overpass(b_query, b_name)
            except Exception as e:
                print(f"[WARN] {b_name} alinamadi: {e}", flush=True)
            time.sleep(1.0)

    print("[TAMAM] Tum gercek Izmir OSM & DEM verileri basariyla indirildi.", flush=True)


if __name__ == "__main__":
    main()
