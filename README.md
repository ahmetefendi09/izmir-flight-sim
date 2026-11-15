# İzmir 3D Uçuş Simülatörü ve Gerçek GIS Haritası

İzmir bölgesini (`35 km × 45 km` 3D simülatör alanı, `41.8 km × 47.8 km` 2D harita alanı) **Overture Maps + OpenStreetMap + 30m Terrarium DEM** verileriyle 1:1 ölçekte modelleyen C++/OpenGL uçuş simülatörü ve Python GIS harita üreteci.

## İçerik
- `izmir_flight_sim.cpp`: C++/OpenGL 3D uçuş simülatörü (`IZMIROSM` v2 ikili dünya formatı desteği)
- `izmir_osm_world.bin`: Paketlenmiş `22.70 MB` v2 dünya verisi (`1024×1024` DEM, `2048×2048` GIS arazi ızgarası, `420,734` bina, `107,324` yol, `658` ray segmenti, `4,239` haritalı ağaç)
- `pack_world_v2.py`: Ham Overture/OSM/DEM önbelleğini `izmir_osm_world.bin` dosyasına paketleyen betik
- `build_izmir_map_v2.py`: 6K ana harita (`izmir_master_real_map_v2_6k.png`) ve 7 yüksek çözünürlüklü ilçe detay haritası üreten betik

## Derleme ve Çalıştırma
```bash
g++ -O3 -std=c++17 -Wall -Wextra izmir_flight_sim.cpp -o izmir_flight_sim -lEGL -lX11 -lGL -lGLU -lm -lpthread
./izmir_flight_sim
```

### Doğrulama Modları
```bash
./izmir_flight_sim --test-gen      # Dünya üretimi, çakışmasızlık ve 6 uçuş testi
./izmir_flight_sim --inspect-city  # 24 noktalı otonom şehir turu ekran görüntüleri
```
