#!/usr/bin/env python3
"""
Izmir Gercek Dunya Veri Birlestirici ve Binary Paketleyici (pack_osm_to_bin.py).
Tum OSM yollarini, ara sokaklari, park/bahceleri, 120.000+ gercek binayi,
IZBAN hattini ve 30m Terrarium DEM yukseklik matrisini tek bir optimize
izmir_osm_world.bin dosyasina paketler.
"""

import glob
import json
import math
import os
import struct
import sys
from pathlib import Path
from PIL import Image

OUT_DIR = Path(__file__).resolve().parent
CACHE_DIR = OUT_DIR / "osm_cache"
DEM_DIR = CACHE_DIR / "dem_tiles"
BIN_PATH = OUT_DIR / "izmir_osm_world.bin"

REF_LAT = 38.4189
REF_LON = 27.1287
WORLD_X0, WORLD_X1 = -17500.0, 17500.0
WORLD_Z0, WORLD_Z1 = -26000.0, 19000.0
DEM_N = 1024

def lonToX(lon): return (lon - REF_LON) * 87215.0
def latToZ(lat): return -(lat - REF_LAT) * 111132.0
def xToLon(x):   return REF_LON + x / 87215.0
def zToLat(z):   return REF_LAT - z / 111132.0

def deg2tile_f(lat_deg, lon_deg, zoom=12):
    lat_rad = math.radians(lat_deg)
    n = 1 << zoom
    xtile = (lon_deg + 180.0) / 360.0 * n
    ytile = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile

def safe_fixed_bytes(s, length=48):
    b = s.encode("utf-8", errors="ignore")
    if len(b) >= length:
        b = b[:length - 1]
        while b:
            try:
                b.decode("utf-8")
                break
            except UnicodeDecodeError:
                b = b[:-1]
    return b.ljust(length, b"\x00")

# 1. DEM YUKSEKLIK MATRISI OLUSTUR
print("[1/5] 1024x1024 DEM yukseklik matrisi olusturuluyor...", flush=True)
tile_cache = {}
def get_tile(tx, ty):
    k = (tx, ty)
    if k in tile_cache: return tile_cache[k]
    p = DEM_DIR / f"z12_{tx}_{ty}.png"
    if p.exists():
        im = Image.open(p).convert("RGB")
        tile_cache[k] = im
        return im
    return None

def sample_dem(lat, lon):
    xf, yf = deg2tile_f(lat, lon, 12)
    tx, ty = int(xf), int(yf)
    im = get_tile(tx, ty)
    if im is None: return 2.0
    px = max(0, min(255, int((xf - tx) * 256)))
    py = max(0, min(255, int((yf - ty) * 256)))
    r, g, b = im.getpixel((px, py))
    return (r * 256.0 + g + b / 256.0) - 32768.0

dem_floats = []
for iz in range(DEM_N):
    wz = WORLD_Z0 + (iz / (DEM_N - 1)) * (WORLD_Z1 - WORLD_Z0)
    lat = zToLat(wz)
    for ix in range(DEM_N):
        wx = WORLD_X0 + (ix / (DEM_N - 1)) * (WORLD_X1 - WORLD_X0)
        lon = xToLon(wx)
        e = sample_dem(lat, lon)
        dem_floats.append(e)

print(f"  DEM matrisi hazir: {len(dem_floats)} nokta.", flush=True)

# 2. CORE VERISI: KIYI, IZBAN, OTOYOLLAR, ANA CADDELER
print("[2/5] izmir_core.json yukleniyor...", flush=True)
with open(CACHE_DIR / "izmir_core.json", "r", encoding="utf-8") as f:
    core_data = json.load(f)

coast_polylines = []
major_roads = []
rail_segments = []
station_nodes = []

for el in core_data.get("elements", []):
    tags = el.get("tags", {})
    geom = el.get("geometry", [])
    etype = el.get("type")

    if etype == "node":
        if tags.get("railway") in ("station", "halt"):
            sname = tags.get("name", "")
            lat, lon = el.get("lat"), el.get("lon")
            if lat is not None and lon is not None:
                station_nodes.append((sname, lat, lon))
        continue

    if not geom or len(geom) < 2:
        continue

    # Kiyi
    if tags.get("natural") == "coastline":
        pts = [(lonToX(p['lon']), latToZ(p['lat'])) for p in geom]
        coast_polylines.append(pts)

    # Rayli sistem
    if tags.get("railway") in ("rail", "subway"):
        pts = [(lonToX(p['lon']), latToZ(p['lat'])) for p in geom]
        rail_segments.append(pts)

    # Otoyol ve Ana Caddeler
    hw = tags.get("highway", "")
    if hw:
        pts = [(lonToX(p['lon']), latToZ(p['lat'])) for p in geom]
        rname = tags.get("name", "")
        rtype = 0  # Motorway
        half_w = 15.0
        if hw in ("primary", "primary_link"):
            rtype = 1
            half_w = 11.0
        elif hw in ("secondary", "secondary_link", "tertiary", "tertiary_link"):
            rtype = 2
            half_w = 8.0
        major_roads.append((rname, rtype, half_w, pts))

print(f"  Kiyi: {len(coast_polylines)}, Otoyol/Cadde: {len(major_roads)}, Ray: {len(rail_segments)}, Istasyon: {len(station_nodes)}", flush=True)

# 3. ARA SOKAKLAR (Streets)
print("[3/5] Ara sokaklar yukleniyor...", flush=True)
side_streets = []
street_files = sorted(glob.glob(str(CACHE_DIR / "izmir_streets_*.json")))
for sf in street_files:
    try:
        with open(sf, "r", encoding="utf-8") as f:
            sd = json.load(f)
            for el in sd.get("elements", []):
                geom = el.get("geometry", [])
                if len(geom) >= 2:
                    # Noktalari metreye cevir
                    pts = [(lonToX(p['lon']), latToZ(p['lat'])) for p in geom]
                    # Dunya sinirlari icinde mi kontrol et
                    if any(WORLD_X0 <= p[0] <= WORLD_X1 and WORLD_Z0 <= p[1] <= WORLD_Z1 for p in pts):
                        side_streets.append(pts)
    except Exception as e:
        print(f"Hata {sf}: {e}")

print(f"  Toplam {len(side_streets)} gercek ara sokak ve yol cizgisi yuklendi.", flush=True)

# 4. YESIL ALANLAR, PARKLAR, BAHCELER
print("[4/5] Park ve bahceler yukleniyor...", flush=True)
green_polygons = []
lu_files = sorted(glob.glob(str(CACHE_DIR / "izmir_landuse_*.json")))
for lf in lu_files:
    try:
        with open(lf, "r", encoding="utf-8") as f:
            ld = json.load(f)
            for el in ld.get("elements", []):
                geom = el.get("geometry", [])
                if len(geom) >= 3:
                    tags = el.get("tags", {})
                    leisure = tags.get("leisure", "")
                    landuse = tags.get("landuse", "")
                    gtype = 0 # 0=Park/Bahce, 1=Orman/Koru, 2=Tarim
                    if "forest" in landuse or "wood" in tags.get("natural", ""):
                        gtype = 1
                    elif "farmland" in landuse or "orchard" in landuse:
                        gtype = 2
                    pts = [(lonToX(p['lon']), latToZ(p['lat'])) for p in geom]
                    green_polygons.append((gtype, pts))
    except Exception as e:
        print(f"Hata {lf}: {e}")

print(f"  Toplam {len(green_polygons)} gercek park/bahce alani yuklendi.", flush=True)

# 5. GERCEK BINALAR (Ev duzeni, siteler, ozel binalar)
print("[5/5] Gercek binalar taranip OBB olarak hesaplaniyor...", flush=True)
buildings = []
bldg_files = sorted(glob.glob(str(CACHE_DIR / "izmir_bldgs_*.json")))

special_kw = {
    "okul": 3, "lise": 3, "ilkokul": 3, "ortaokul": 3, "üniversite": 3, "universite": 3, "fakülte": 3,
    "cami": 4, "mescit": 4, "church": 4,
    "hastane": 5, "sağlık": 5, "saglik": 5, "poliklinik": 5, "tıp": 5,
    "stadyum": 6, "spor": 6, "arena": 6,
    "havalimanı": 7, "terminal": 7, "gar": 7, "istasyon": 7,
    "avm": 8, "alışveriş": 8, "pazar": 8, "belediye": 8, "hükümet": 8, "adliye": 8
}

for bf in bldg_files:
    try:
        with open(bf, "r", encoding="utf-8") as f:
            bd = json.load(f)
            for el in bd.get("elements", []):
                geom = el.get("geometry", [])
                if len(geom) < 3:
                    continue
                tags = el.get("tags", {})
                bname = tags.get("name", "").lower()
                btype_tag = tags.get("building", "")

                # Bina agirlik merkezi (Centroid)
                sx = sum(lonToX(p['lon']) for p in geom) / len(geom)
                sz = sum(latToZ(p['lat']) for p in geom) / len(geom)

                if sx < WORLD_X0 or sx > WORLD_X1 or sz < WORLD_Z0 or sz > WORLD_Z1:
                    continue

                # Boyutlar ve yonelim
                # En uzak iki kose arasi diagonal ve yon
                max_d2 = 0.0
                best_dx, best_dz = 1.0, 0.0
                for p in geom:
                    dx = lonToX(p['lon']) - sx
                    dz = latToZ(p['lat']) - sz
                    d2 = dx*dx + dz*dz
                    if d2 > max_d2:
                        max_d2 = d2
                        best_dx, best_dz = dx, dz

                diag = math.sqrt(max_d2) * 2.0
                yaw = math.atan2(best_dz, best_dx)

                # Tahmini genislik / derinlik
                w = max(10.0, min(120.0, diag * 0.70))
                d = max(8.0, min(100.0, diag * 0.50))

                # Bina tipi ve yukseklik belirleme
                b_kind = 0  # 0: Standart konut apartmani
                for kw, kid in special_kw.items():
                    if kw in bname:
                        b_kind = kid
                        break

                if b_kind == 0:
                    if btype_tag in ("commercial", "office"):
                        b_kind = 1
                    elif btype_tag in ("industrial", "warehouse"):
                        b_kind = 2

                # Kat / Yukseklik
                levels = 0
                if "building:levels" in tags:
                    try:
                        levels = int(tags["building:levels"])
                    except Exception:
                        pass

                if levels > 0:
                    h = max(6.0, levels * 3.3)
                elif b_kind == 1:
                    # Ofis / Gökdelen bolgesi (Bayrakli / Halkapinar civari)
                    dist_to_bayrakli = math.hypot(sx - 3500.0, sz - (-3500.0))
                    if dist_to_bayrakli < 2000.0:
                        h = 45.0 + (int(sx * 13 + sz * 7) % 80) # 45m..125m kuleler
                    else:
                        h = 24.0
                elif b_kind == 2:
                    h = 10.0 # Sanayi tek kat hangarlar
                elif b_kind in (3, 5, 8):
                    h = 18.0 # Okul, Hastane, AVM
                elif b_kind == 4:
                    h = 22.0 # Cami kubbe ve minare
                elif b_kind == 6:
                    h = 26.0 # Stadyum
                else:
                    # Konut: Izmir merkezde genelde 5-8 kat (16-25m), Menemen/Egekent-2'de 4-8 kat siteler
                    h = 14.0 + (int(sx * 17 + sz * 31) % 12)

                # Renk cesidi
                r = 240 + (int(sx) % 15)
                g = 230 + (int(sz) % 20)
                b = 220 + (int(sx + sz) % 30)

                buildings.append((sx, sz, w, d, yaw, h, b_kind, r, g, b))

    except Exception as e:
        print(f"Hata {bf}: {e}")

print(f"  Toplam {len(buildings)} gercek bina 3D haritaya islendi.", flush=True)

# 6. BINARY FORMATTA KAYDET (izmir_osm_world.bin)
print("\n[YAZILIYOR] izmir_osm_world.bin olusturuluyor...", flush=True)
with open(BIN_PATH, "wb") as f:
    # Header: Magic "IZMIROSM" (8 bytes), Version (4 bytes)
    f.write(b"IZMIROSM")
    f.write(struct.pack("<I", 1)) # Version 1

    # World params: refLat, refLon, worldX0, worldX1, worldZ0, worldZ1
    f.write(struct.pack("<ffffff", REF_LAT, REF_LON, WORLD_X0, WORLD_X1, WORLD_Z0, WORLD_Z1))

    # DEM: 1024x1024 floats (4 MB)
    f.write(struct.pack("<II", DEM_N, DEM_N))
    f.write(struct.pack(f"<{len(dem_floats)}f", *dem_floats))

    # Kiyi Cizgileri
    f.write(struct.pack("<I", len(coast_polylines)))
    for poly in coast_polylines:
        f.write(struct.pack("<I", len(poly)))
        for x, z in poly:
            f.write(struct.pack("<ff", x, z))

    # IZBAN Ray Segmentleri
    f.write(struct.pack("<I", len(rail_segments)))
    for poly in rail_segments:
        f.write(struct.pack("<I", len(poly)))
        for x, z in poly:
            f.write(struct.pack("<ff", x, z))

    # Istasyonlar
    f.write(struct.pack("<I", len(station_nodes)))
    for sname, lat, lon in station_nodes:
        f.write(safe_fixed_bytes(sname, 48))
        f.write(struct.pack("<ffff", lonToX(lon), latToZ(lat), lat, lon))

    # Ana Yollar (Motorway, Primary, Secondary)
    f.write(struct.pack("<I", len(major_roads)))
    for rname, rtype, half_w, pts in major_roads:
        f.write(safe_fixed_bytes(rname, 48))
        f.write(struct.pack("<IfI", rtype, half_w, len(pts)))
        for x, z in pts:
            f.write(struct.pack("<ff", x, z))

    # Ara Sokaklar
    f.write(struct.pack("<I", len(side_streets)))
    for pts in side_streets:
        f.write(struct.pack("<I", len(pts)))
        for x, z in pts:
            f.write(struct.pack("<ff", x, z))

    # Park ve Bahceler
    f.write(struct.pack("<I", len(green_polygons)))
    for gtype, pts in green_polygons:
        f.write(struct.pack("<II", gtype, len(pts)))
        for x, z in pts:
            f.write(struct.pack("<ff", x, z))

    # Gercek Binalar
    f.write(struct.pack("<I", len(buildings)))
    for sx, sz, w, d, yaw, h, b_kind, r, g, b in buildings:
        f.write(struct.pack("<ffffffBBBB", sx, sz, w, d, yaw, h, b_kind, r, g, b))

bin_size_mb = BIN_PATH.stat().st_size / (1024 * 1024)
print(f"\n[TAMAMLANDI] {BIN_PATH} olusturuldu! Dosya Boyutu: {bin_size_mb:.2f} MB", flush=True)
