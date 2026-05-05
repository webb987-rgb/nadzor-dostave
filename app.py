def scrape_wolt_api(adresa, log_ph=None):
    lat, lon = dobavi_koordinate(adresa)
    if not lat:
        log_msg(f"❌ Neuspešno geokodiranje za: {adresa}", log_ph)
        return []
    
    url = f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200: return []
        
        data = r.json()
        found = []
        for section in data.get("sections", []):
            for item in section.get("items", []):
                v = item.get("venue")
                if not v: continue
                
                # Ekstrakcija osnovnih podataka
                ime = v.get("name", "Nepoznato")
                slug = v.get("slug", "")
                link = f"https://wolt.com/sr/srb/beograd/restaurant/{slug}"
                
                # Status & Vreme
                status = "Otvoreno" if v.get("online", False) else "Zatvoreno"
                est = v.get("estimate_range", "")
                v_num = float(est.split('-')[0]) if est and '-' in str(est) else (float(est) if str(est).isdigit() else np.nan)
                
                # ==========================================
                # 🎯 EKSTRAKCIJA AKCIJA I POPUSTA (NOVO)
                # ==========================================
                akcije_set = set() # Koristimo set da izbegnemo duplikate
                
                # 1. Provera za Wolt+ pretplatu
                if v.get("show_wolt_plus", False):
                    akcije_set.add("Wolt+")
                    
                # 2. Standardni bedževi (npr. "Free delivery", "-20%", "1+1")
                badges = v.get("badges", [])
                for b in badges:
                    txt = b.get("text")
                    if txt: akcije_set.add(txt)
                    
                # 3. Dodatne ponude (offer_assistant - kao sa tvoje slike)
                offer_assistant = v.get("offer_assistant", {})
                trackers = offer_assistant.get("offer_trackers", [])
                for tracker in trackers:
                    title = tracker.get("title", "")
                    if title:
                        # Ako je tekst dugačak (npr. "10% discount on selected items..."), čupamo samo procenat
                        m = re.search(r'(\d+\s*%)', title)
                        if m:
                            akcije_set.add(f"{m.group(1)} popusta")
                        else:
                            akcije_set.add(title)
                            
                # Formatiranje za tabelu (svaka akcija u novi red sa tačkom)
                promo_list = sorted([f"• {a}" for a in akcije_set])
                akcija_final = "\n".join(promo_list) if promo_list else "-"
                # ==========================================
                
                # Provera da li je restoran nov
                is_new = "novo" in str(promo_list).lower() or "new" in str(promo_list).lower()

                found.append({
                    "Adresa": adresa, 
                    "Platforma": "Wolt", 
                    "Naziv": ukloni_kvacice(ime),
                    "Ocena": str(v.get("rating", {}).get("score", "-")),
                    "Vreme dostave": f"{est} min" if est else "-", 
                    "Vreme_Broj": v_num,
                    "Akcija": akcija_final,
                    "Status": status, 
                    "Is_New": is_new,
                    "Link": link
                })
        
        return pd.DataFrame(found).drop_duplicates(subset=['Link']).to_dict('records')
    except Exception as e:
        log_msg(f"Wolt API Greška: {e}", log_ph)
        return []
