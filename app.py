# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  WOLT PATCH — zameni odgovarajuće blokove u glavnoj skripti ovim kodom      ║
# ║  Izvor logike: promo__1_.py (fetch_city + _fetch_one + make_thread_session)  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ─────────────────────────────────────────────────────────────────────────────
# BLOK 1 — zameni ceo blok "GLOBAL SETTINGS" deo koji definiše wolt_session
#          i WOLT_FETCH_WORKERS (ostavi ostalo, samo promeni ove konstante)
# ─────────────────────────────────────────────────────────────────────────────

WOLT_FETCH_WORKERS = 2          # smanji sa 6 na 2 — isti kao u promo.py (FETCH_WORKERS)

# ─────────────────────────────────────────────────────────────────────────────
# BLOK 2 — zameni celu funkciju make_thread_session()
#          Dodata podrška za opcioni _scan_cookie.txt (kao u promo.py)
# ─────────────────────────────────────────────────────────────────────────────
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

def make_thread_session() -> requests.Session:
    s = requests.Session()
    for k, v in wolt_session.headers.items():
        s.headers[k] = v
    # Opcioni cookie fajl — ako postoji, koristi ga (kao u promo.py)
    try:
        cookie_val = Path("_scan_cookie.txt").read_text().strip()
    except Exception:
        cookie_val = ""
    if cookie_val:
        s.headers["Cookie"] = cookie_val
    return s

# ─────────────────────────────────────────────────────────────────────────────
# BLOK 3 — zameni celu funkciju _fetch_url()
#          Dodat stop_event parametar (kao u promo.py) — sprečava beskonačne
#          retry-je i omogućuje čisto zaustavljanje skeniranja
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_url(ts, url: str, label: str, stop_event=None) -> tuple:
    for attempt in range(4):
        # Provjeri stop signal pre svakog pokušaja
        if stop_event is not None and stop_event.is_set():
            return None, 0
        _wait_throttle()
        try:
            time.sleep(random.uniform(0.3, 1.2))
            with _global_http_sem:
                r = ts.get(url, timeout=10)
            if r.status_code == 200:
                return r.json(), 200
            if r.status_code in (401, 403):
                _log_fetch(f"{label} → {r.status_code} (auth fail) — refreshing session")
                _refresh_wolt_session()
                return None, r.status_code
            if r.status_code == 429:
                wait = 2 + 2 ** attempt
                _set_throttle(wait)
                _log_fetch(f"{label} → 429 retry {attempt} (throttle {wait:.0f}s)")
                continue
            _log_fetch(f"{label} → {r.status_code}")
            return None, r.status_code
        except Exception as e:
            _log_fetch(f"{label} → EXC {e}")
            if attempt < 3:
                time.sleep(0.5)
    return None, -1

# ─────────────────────────────────────────────────────────────────────────────
# BLOK 4 — zameni celu funkciju _fetch_one_wolt()
#          Dodat stop_event parametar + logging "200 ali NEMA akcija"
#          (identično _fetch_one u promo.py, samo preimenovano)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_one_wolt(slug: str, lat: float, lon: float, feed_akcije: list, stop_event=None) -> tuple:
    if stop_event is not None and stop_event.is_set():
        return slug, "-"
    ts = make_thread_session()
    time.sleep(random.uniform(1.0, 2.0))
    dyn_url = (
        f"https://consumer-api.wolt.com/order-xp/web/v1/venue/slug/{slug}/dynamic/"
        f"?lat={lat}&lon={lon}&selected_delivery_method=homedelivery"
    )
    akcije_str = "-"
    dyn_data, _ = _fetch_url(ts, dyn_url, f"DYN {slug}", stop_event)
    if dyn_data:
        try:
            parsed   = _parse_dynamic_with_item_discount(dyn_data)
            combined = list(dict.fromkeys(feed_akcije + parsed))
            akcije_str = "\n".join(combined) if combined else "-"
            if akcije_str == "-":
                _log_fetch(f"DYN {slug} → 200 ali NEMA akcija")
        except Exception as e:
            _log_fetch(f"DYN {slug} → parse EXC {e}")
    elif feed_akcije:
        akcije_str = "\n".join(feed_akcije)
    return slug, akcije_str

# ─────────────────────────────────────────────────────────────────────────────
# BLOK 5 — ZAMENI CELU FUNKCIJU scrape_wolt_sync()
#          Ovo je glavni fix — logika je preuzeta direktno iz fetch_city()
#          u promo.py, prilagođena za geocoding adrese umesto city_key-a.
#
#          Ključne razlike vs stara verzija:
#          1. _refresh_wolt_session() se poziva EKSPLICITNO na početku
#          2. max_pages povećan na 50 (sa 30)
#          3. stop_event prosleđen svuda
#          4. Logging "200 ali NEMA akcija" u _fetch_one_wolt
#          5. make_thread_session čita opcioni cookie fajl
# ─────────────────────────────────────────────────────────────────────────────

def scrape_wolt_sync(address: str, log_ph=None, live_ph=None, live_state=None) -> list:
    """
    Requests-based Wolt scraper — logika preuzeta iz fetch_city (promo.py).
    1. Geocodira adresu → lat/lon
    2. Eksplicitno inicijalizuje Wolt sesiju pre prvog poziva
    3. Paginovano preuzima sve restorane (max 50 stranica)
    4. Konkurentno preuzima dynamic API za promocije
    """
    import urllib.request as _urllib_req
    import json as _json

    # ── Inicijalizacija sesije (KEY FIX) ─────────────────────────────────────
    # Bez ovoga, prvi poziv wolt_get() može da ide bez važećih kolačića.
    _refresh_wolt_session()

    log_msg(f"[WOLT] Geocoding: {address}...", log_ph)

    custom_agent = 'DeliveryMonitorApp/7.0 (wolt_scraper)'
    geo_data = []
    try:
        geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address + ', Serbia')}&format=json&limit=1&addressdetails=1"
        req = _urllib_req.Request(geo_url, headers={'User-Agent': custom_agent})
        with _urllib_req.urlopen(req) as resp:
            geo_data = _json.loads(resp.read().decode())
        if not geo_data:
            time.sleep(1)
            geo_url2 = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address)}&format=json&limit=1&addressdetails=1"
            req2 = _urllib_req.Request(geo_url2, headers={'User-Agent': custom_agent})
            with _urllib_req.urlopen(req2) as resp2:
                geo_data = _json.loads(resp2.read().decode())
    except Exception as e:
        log_msg(f"[WOLT ERROR] Geocoding failed: {e}", log_ph)
        return []

    if not geo_data:
        log_msg(f"[WOLT ERROR] Cannot find coordinates for: {address}", log_ph)
        return []

    lat      = float(geo_data[0]["lat"])
    lon      = float(geo_data[0]["lon"])
    addr_obj = geo_data[0].get("address", {})
    city_raw = addr_obj.get("city") or addr_obj.get("town") or addr_obj.get("village") or "belgrade"
    city_slug = normalize_name(cyrillic_to_latin(city_raw))

    log_msg(f"[WOLT] Koordinate: {lat:.4f}, {lon:.4f} (grad slug: {city_slug}). Učitavam restorane...", log_ph)

    # ── Paginacija feed API-ja ────────────────────────────────────────────────
    restaurants = {}
    stop_event  = threading.Event()   # dummy — nema UI stop dugmeta za Wolt

    skip = 0
    for page_num in range(50):         # 50 stranica kao u promo.py (ne 30)
        count_before = len(restaurants)
        endpoint = f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}&skip={skip}"
        data, status = wolt_get(endpoint)
        items_in_response = 0

        if data:
            for section in data.get("sections", []):
                for item in section.get("items", []):
                    venue = item.get("venue")
                    if not venue:
                        continue
                    name = venue.get("name", "")
                    slug = venue.get("slug", "")
                    if not name or not slug or slug in restaurants:
                        continue
                    items_in_response += 1

                    status_val = "Open" if venue.get("online") else "Closed"
                    rating_obj = venue.get("rating") or {}
                    r_score    = rating_obj.get("score", "-") if isinstance(rating_obj, dict) else "-"
                    est        = venue.get("estimate_range") or venue.get("estimate")
                    time_str   = f"{est} min" if est else "-"

                    time_num = np.nan
                    if est:
                        try:
                            parts = str(est).split('-')
                            time_num = (int(parts[0]) + int(parts[1])) / 2.0 if len(parts) == 2 else float(parts[0])
                        except Exception:
                            pass

                    feed_akcije = []
                    novo_status = False
                    for badge in venue.get("badges", []):
                        txt = badge.get("text", "")
                        if txt:
                            if txt.lower() in ["novo", "new"]:
                                novo_status = True
                            else:
                                feed_akcije.append(f"• {txt}")
                    label = venue.get("label", "")
                    if label:
                        if label.lower() in ["novo", "new"]:
                            novo_status = True
                        else:
                            feed_akcije.append(f"• {label}")

                    link_target = item.get("link", {}).get("target", "")
                    if link_target.startswith("http"):
                        link = link_target
                    elif link_target.startswith("/"):
                        link = f"https://wolt.com{link_target}"
                    else:
                        link = f"https://wolt.com/en/srb/{city_slug}/restaurant/{slug}"

                    restaurants[slug] = {
                        "Address":       address,
                        "Platform":      "Wolt",
                        "Name":          remove_accents(name),
                        "Rating":        str(r_score),
                        "Delivery Time": time_str,
                        "Promo":         "\n".join(feed_akcije) if feed_akcije else "-",
                        "Status":        status_val,
                        "Time_Num":      time_num,
                        "Is_New":        novo_status,
                        "Link":          link,
                        "_slug":         slug,
                        "_feed_akcije":  feed_akcije,
                    }

        new_this_page = len(restaurants) - count_before
        log_msg(f"[WOLT] Str.{page_num+1}: +{new_this_page} restorana (ukupno {len(restaurants)})", log_ph)

        if live_ph and live_state is not None:
            live_state["Wolt"] = len(restaurants)
            refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)

        if items_in_response == 0:
            break
        skip += 40
        time.sleep(random.uniform(0.5, 1.8))   # malo duži sleep kao u promo.py

    if not restaurants:
        log_msg(f"[WOLT ERROR] Nije pronađen nijedan restoran za: {address}", log_ph)
        return []

    log_msg(f"[WOLT] Ukupno {len(restaurants)} restorana. Učitavam promocije...", log_ph)
    if live_ph and live_state is not None:
        refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address,
                        custom_text=f"📍 {len(restaurants)} Wolt restorana učitano. Skeniram promocije...")

    # ── Konkurentno preuzimanje dynamic API (promocije) ───────────────────────
    slugs     = list(restaurants.keys())
    total     = len(slugs)
    completed = 0

    with ThreadPoolExecutor(max_workers=WOLT_FETCH_WORKERS) as executor:
        futures = {
            executor.submit(
                _fetch_one_wolt, slug, lat, lon,
                restaurants[slug]["_feed_akcije"],
                stop_event
            ): slug for slug in slugs
        }
        for future in as_completed(futures):
            try:
                slug, akcije_str = future.result()
                restaurants[slug]["Promo"] = akcije_str
            except Exception:
                pass
            completed += 1
            if completed % 20 == 0 or completed == total:
                log_msg(f"[WOLT] Promocije: {completed}/{total}", log_ph)

    # ── Čišćenje internih ključeva ────────────────────────────────────────────
    result = []
    for r in restaurants.values():
        r.pop("_slug", None)
        r.pop("_feed_akcije", None)
        result.append(r)

    log_msg(f"[WOLT] Završeno. {len(result)} restorana.", log_ph)
    if live_ph and live_state is not None:
        live_state["Wolt"] = len(result)
        refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)

    return result
