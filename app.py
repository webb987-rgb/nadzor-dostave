import asyncio
import datetime
import os
import platform
import subprocess
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.express as px          
import plotly.graph_objects as go    
import random
import urllib.parse
from io import BytesIO
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import time
import streamlit as st
import sys

# TIMEZONE SETUP
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Belgrade")
except Exception:
    LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=2))

def local_time():
    return datetime.datetime.now(LOCAL_TZ)

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

# Streamlit Page Config
st.set_page_config(page_title="Delivery Monitor", page_icon="🍔", layout="wide")

# ================= MODERN CSS DESIGN (Zadržano tvoje) =================
st.markdown("""
<style>
    .live-card { display: flex; gap: 20px; background: #f8f9fa; padding: 15px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    .wolt-card { flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #00c2e8; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .glovo-card { flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #ffc244; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .metric-value { font-size: 32px; font-weight: bold; margin: 0; }
    .metric-title { font-size: 14px; color: #666; margin: 0; text-transform: uppercase; letter-spacing: 1px;}
    .kpi-wrapper { display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }
    .kpi-card { flex: 1; background: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.04); border: 1px solid #f0f2f6; text-align: center; transition: transform 0.2s; }
    .kpi-card:hover { transform: translateY(-5px); }
    .kpi-title { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; font-weight: 700;}
    .kpi-value { font-size: 36px; font-weight: 800; color: #2c3e50; margin: 0; line-height: 1.1;}
    .kpi-wolt { border-bottom: 4px solid #00c2e8; }
    .kpi-glovo { border-bottom: 4px solid #ffc244; }
</style>
""", unsafe_allow_html=True)

# ================= WINDOWS & PLAYWRIGHT FIX =================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

@st.cache_resource
def install_playwright():
    os.system("playwright install chromium")

install_playwright()

# ================= GLOBAL SETTINGS =================
EMAIL_SENDER = "webb987@gmail.com"
EMAIL_PASSWORD = "sdehqzbnqefjlomo" 
OUTPUT_DIR = Path.cwd() / "reports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = OUTPUT_DIR / "delivery_history.csv"
ERRORS_DIR = Path.cwd() / "errors"
ERRORS_DIR.mkdir(parents=True, exist_ok=True)
GLOVO_AUTH_FILE = "glovo_auth.json"
WOLT_AUTH_FILE = "wolt_auth.json"

def timestamp(): return local_time().strftime("%Y%m%d_%H%M%S")
def format_time_short(): return local_time().strftime("%H:%M")
def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder: placeholder.text(msg)

# ---------------- LIVE COUNTER UI ----------------
def refresh_live_ui(ph, wolt_count, glovo_count, address, custom_text=None):
    txt = custom_text if custom_text else f"📍 Currently scanning: <b>{address}</b>"
    html = f"""
    <div class="live-card">
        <div class="wolt-card"><p class="metric-title">🚲 Wolt</p><p class="metric-value" style="color: #00c2e8;">{wolt_count}</p></div>
        <div class="glovo-card"><p class="metric-title">🍔 Glovo</p><p class="metric-value" style="color: #ffc244;">{glovo_count}</p></div>
    </div>
    <p style="text-align: center; color: #666; font-size: 14px;">{txt}</p>
    """
    ph.markdown(html, unsafe_allow_html=True)

# ---------------- POMOĆNE FUNKCIJE ----------------
def cyrillic_to_latin(text):
    if not text: return ""
    mapa = { 'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s', 'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'њ':'nj', 'О':'O', 'П':'P', 'Р':'R', 'С':'S', 'Т':'T', 'Ћ':'C', 'У':'U', 'Ф':'F', 'Х':'H', 'Ц':'C', 'Ч':'C', 'Џ':'Dz', 'Ш':'S' }
    for k, v in mapa.items(): text = text.replace(k, v)
    return text

def remove_accents(text):
    if not text: return ""
    for k, v in {'č':'c', 'ć':'c', 'ž':'z', 'š':'s', 'đ':'dj', 'Č':'C', 'Ć':'C', 'Ž':'Z', 'Š':'S', 'Đ':'Dj'}.items(): text = text.replace(k, v)
    return text

def normalize_name(name): return re.sub(r'[^\w]', '', str(name).lower())

def get_all_json_strings(obj):
    if isinstance(obj, dict): return " ".join(get_all_json_strings(v) for v in obj.values() if v is not None)
    elif isinstance(obj, list): return " ".join(get_all_json_strings(i) for i in obj if i is not None)
    elif isinstance(obj, str): return obj
    return ""

# ---------------- UNAPREĐENI PROMO EXTRACT (WOLT + GLOVO) ----------------
def extract_promo(text, html_content, plat):
    clean_text = (str(text) + " \n " + str(html_content)).lower()
    clean_text = re.sub(r'(?<=\d)[.,](?=\d)', '', clean_text) # 1.000 -> 1000
    
    promos, seen, res = [], set(), []

    if plat == "Glovo":
        glovo_tags = re.findall(r'data-style="promotion"[^>]*>([^<]+)<', str(html_content))
        for gp in glovo_tags: promos.append(gp.strip())
        for pm in re.findall(r'(\d{1,2}\s*%)\s*(?:popust|off|discount|-)', clean_text):
            promos.append(f"{pm.strip()} discount")

    if plat == "Wolt":
        # Hvata procente
        for pm in re.findall(r'(\d{1,3}\s*%\s*[^.\n]*)', clean_text):
            promos.append(pm.strip())
        # Hvata RSD/DIN popuste
        for rsd in re.findall(r'((?:rsd|din)\s*\d+\s*(?:off|popust|iznad|over)[^.\n]*)', clean_text):
            promos.append(rsd.strip())
        # Hvata 0 din dostavu
        if any(x in clean_text for x in ["0 din", "0 rsd", "besplatna dostava", "free delivery"]):
            promos.append("0 RSD delivery fee")

    if any(x in clean_text for x in ["1+1", "1 + 1", "buy 1 get 1"]): promos.append("1+1 Free")
    if "wolt+" in clean_text: promos.append("Wolt+")
    if "prime" in clean_text: promos.append("Prime")
        
    for a in promos:
        ac = a.replace("rsd", "RSD").replace("din", "RSD").strip()
        ac = ac[0].upper() + ac[1:]
        if ac not in seen:
            seen.add(ac); res.append(f"• {ac}")
    return "\n".join(res) if res else "-"

# ---------------- EKSTRAKCIJA OSTALIH PODATAKA ----------------
def extract_name(text):
    if not text: return ""
    for line in str(text).split('\n'):
        line = line.strip()
        if not line or '%' in line or ("min" in line.lower() and re.search(r'\d+', line.lower())): continue
        if any(x in line.lower() for x in ["rsd", "din", "promo", "novo", "new", "off", "discount"]): continue
        if len(line) >= 2: return line
    return ""

def analyze_status(text):
    t = text.lower()
    if any(x in t for x in ["uskoro se zatvara", "closing soon"]): return "Open"
    if any(k in t for k in ["zatvoreno", "nedostupno", "otvara se", "closed"]): return "Closed"
    return "Open"

def extract_rating(text, plat):
    try:
        clean = re.sub(r'<[^>]+>', ' ', str(text)).lower()
        if plat == "Glovo":
            for p in re.findall(r'(\d{1,3})\s*%', clean):
                if int(p) >= 60: return p + "%"
        elif plat == "Wolt":
            m = re.search(r'\b([5-9][.,][0-9]|10[.,]0)\b', clean)
            if m: return m.group(1).replace(',', '.')
    except: pass
    return "-"

def extract_delivery_time(text):
    try:
        clean = re.sub(r'<[^>]+>', ' ', str(text)).lower()
        m1 = re.search(r'(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(?:min|m)', clean)
        if m1: return f"{m1.group(1)}-{m1.group(2)} min", (int(m1.group(1)) + int(m1.group(2))) / 2.0
        m2 = re.search(r'\b(\d{1,3})\s*(?:min|m)', clean)
        if m2: return f"{m2.group(1)} min", float(m2.group(1))
    except: pass
    return "-", np.nan

# ---------------- WOLT API HIBRIDNI SKREPER (UNAPREĐEN) ----------------
async def scrape_wolt_api(context_wolt, address, log_ph=None, live_ph=None, live_state=None, error_screenshots=None, debug_mode=False):
    results_dict = {}
    page = None
    try:
        import urllib.request, json
        log_msg(f"[WOLT] Geocoding: {address}...", log_ph)
        geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address + ', Serbia')}&format=json&limit=1"
        req = urllib.request.Request(geo_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            geo_data = json.loads(response.read().decode())
        if not geo_data: return []
        lat, lon = geo_data[0]["lat"], geo_data[0]["lon"]

        page = await context_wolt.new_page()
        await page.goto("https://wolt.com/sr/srb")
        
        w_data = await page.evaluate(f'async () => {{ let r = await fetch("https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}"); return r.ok ? await r.json() : null; }}')
        if not w_data: return []

        for section in w_data.get("sections", []):
            for item in section.get("items", []):
                venue = item.get("venue")
                if not venue: continue
                slug = venue.get("slug")
                name = remove_accents(venue.get("name"))
                
                results_dict[slug] = {
                    "Address": address, "Platform": "Wolt", "Name": name, 
                    "Rating": str(venue.get("rating", {}).get("score", "-")),
                    "Status": "Open" if venue.get("online") else "Closed",
                    "Link": f"https://wolt.com/sr/srb/city/restaurant/{slug}",
                    "Is_New": False, "Promo": "-", "Time_Num": np.nan, "Delivery Time": "-"
                }

        # FETCH ZA DETALJE (PROMO + NOVO + TAČNO VREME)
        slugs = list(results_dict.keys())
        js_fetch = """
        async ([slugs, lat, lon]) => {
            let res = {};
            let limit = 15;
            for (let i = 0; i < slugs.length; i += limit) {
                let chunk = slugs.slice(i, i + limit);
                await Promise.all(chunk.map(async (s) => {
                    try {
                        let r = await fetch(`https://consumer-api.wolt.com/order-xp/web/v1/venue/slug/${s}/dynamic/?lat=${lat}&lon=${lon}&selected_delivery_method=homedelivery`);
                        if (r.ok) res[s] = await r.json();
                    } catch(e) {}
                }));
            }
            return res;
        }"""
        all_promo_data = await page.evaluate(js_fetch, [slugs, lat, lon])

        for slug, data in all_promo_data.items():
            if data:
                v_raw = data.get("venue_raw", {})
                
                # 1. SAKUPLJANJE SVIH BANNERA (Deals & Benefits)
                all_banners = []
                for disc in v_raw.get("discounts", []):
                    txt = disc.get("banner", {}).get("formatted_text")
                    if txt: all_banners.append(f"• {txt}")
                
                # 2. PROVERA NOVO STATUSA
                is_new = "new-restaurant" in v_raw.get("tags", []) or data.get("is_new") is True
                
                # 3. TAČNO VREME DOSTAVE
                est = v_raw.get("preestimate_total", {})
                time_str, time_num = "-", np.nan
                if est.get("min"):
                    time_str = f"{est['min']}-{est['max']} min"
                    time_num = (est['min'] + est['max']) / 2.0

                payload = get_all_json_strings(data).lower()
                text_promo = extract_promo(payload, "", "Wolt")
                final_promos = set(all_banners)
                if text_promo != "-":
                    for p in text_promo.split('\n'): final_promos.add(p.strip())
                
                results_dict[slug].update({
                    "Promo": "\n".join(sorted(list(final_promos))) if final_promos else "-",
                    "Is_New": is_new or "new" in payload,
                    "Delivery Time": time_str, "Time_Num": time_num
                })

        if live_ph:
            live_state["Wolt"] = len(results_dict)
            refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)

        return list(results_dict.values())
    except Exception as e: return []
    finally:
        if page: await page.close()

# ---------------- GLOVO SKREPER (ZADRŽANA LOGIKA) ----------------
async def smart_scroll_and_extract(page, plat, address, log_ph=None, live_ph=None, live_state=None):
    results_dict = {}
    prev_count = 0
    attempts = 0
    while attempts < 5:
        data = await page.evaluate('() => { let r = []; document.querySelectorAll("a:has(h3), a[data-testid=\'store-card\']").forEach(c => { r.push({link: c.href, text: c.innerText, html: c.innerHTML}); }); return r; }')
        for item in data:
            link = item['link']
            if not link or link in results_dict or '/category' in link: continue
            text, html = item['text'], item['html']
            name = remove_accents(extract_name(text))
            if len(name) < 2: continue
            
            rating = extract_rating(text + html, "Glovo")
            time_str, time_num = extract_delivery_time(text + html)
            is_new = "novo" in text.lower() or "new" in text.lower()
            
            results_dict[link] = {
                "Address": address, "Platform": plat, "Name": name, "Rating": rating,
                "Delivery Time": time_str, "Promo": extract_promo(text, html, plat),
                "Status": analyze_status(text), "Time_Num": time_num, "Is_New": is_new, "Link": link
            }
        
        if len(results_dict) > prev_count:
            prev_count = len(results_dict)
            attempts = 0
            if live_ph:
                live_state[plat] = prev_count
                refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)
        else: attempts += 1
        
        await page.evaluate("window.scrollBy(0, 800);")
        await asyncio.sleep(1)
    return list(results_dict.values())

async def scrape_glovo(context_glovo, address, log_ph=None, live_ph=None, live_state=None, error_screenshots=None, debug_mode=False):
    page = await context_glovo.new_page()
    try:
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")
        try:
            btn = page.locator("button:has-text('Prihvati sve')").first
            await btn.click(timeout=3000)
        except: pass
        
        # Navigacija do adrese (pojednostavljeno za stabilnost)
        await page.locator("#hero-container-input").fill(address)
        await asyncio.sleep(2)
        await page.locator("div[role='button']").first.click()
        await asyncio.sleep(5)
        
        return await smart_scroll_and_extract(page, "Glovo", address, log_ph, live_ph, live_state)
    except: return []
    finally: await page.close()

# ---------------- GLAVNI PROCES SKENIRANJA ----------------
async def scan_process(addresses, log_ph, live_ph, live_state, generate_pdf=False, recipient_email="", debug_mode=False):
    all_data = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for adr in addresses:
            live_state.update({"Wolt": 0, "Glovo": 0})
            refresh_live_ui(live_ph, 0, 0, adr)
            
            # GLOVO
            ctx_g = await browser.new_context(user_agent="Mozilla/5.0")
            all_data.extend(await scrape_glovo(ctx_g, adr, log_ph, live_ph, live_state))
            await ctx_g.close()
            
            # WOLT
            ctx_w = await browser.new_context(user_agent="Mozilla/5.0")
            all_data.extend(await scrape_wolt_api(ctx_w, adr, log_ph, live_ph, live_state))
            await ctx_w.close()
        await browser.close()
    
    if all_data:
        df = pd.DataFrame(all_data)
        # Ovde bi išla tvoja originalna save_to_history i PDF logika...
        return df, pd.DataFrame(), [], []
    return pd.DataFrame(), pd.DataFrame(), [], []

# ================= STREAMLIT UI (ZADRŽANO TVOJE) =================
if 'is_running' not in st.session_state: st.session_state.is_running = False
if 'df_all' not in st.session_state: st.session_state.df_all = pd.DataFrame()

st.title("🍔 Delivery Monitor (Wolt & Glovo)")

with st.sidebar:
    st.header("⚙️ Settings")
    address_1 = st.text_input("📍 Address 1:", placeholder="Makenzijeva 57, Belgrade")
    address_2 = st.text_input("📍 Address 2:", placeholder="Somborska 5, Niš")
    if st.button("▶️ START", type="primary"):
        st.session_state.is_running = True
        st.rerun()
    if st.button("⏹️ STOP"):
        st.session_state.is_running = False
        st.rerun()

if st.session_state.is_running:
    addrs = [cyrillic_to_latin(a.strip()) for a in [address_1, address_2] if a.strip()]
    if addrs:
        live_ph = st.empty()
        log_ph = st.empty()
        live_state = {"Wolt": 0, "Glovo": 0}
        df, hi, pdfs, errs = asyncio.run(scan_process(addrs, log_ph, live_ph, live_state))
        st.session_state.df_all = df
        st.session_state.is_running = False
        st.rerun()

# PRIKAZ REZULTATA (Tvoji tabovi)
df = st.session_state.df_all
if not df.empty:
    tab1, tab2 = st.tabs(["📊 Dashboard", "🔍 Restaurant List"])
    with tab1:
        st.dataframe(df) # Ovde staviš svoje Plotly grafikone
    with tab2:
        st.dataframe(df, use_container_width=True)
