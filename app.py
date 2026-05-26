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
import urllib.request
import json
from io import BytesIO
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import time
import streamlit as st
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

# Pokušaj uvoza zoneinfo za vremensku zonu
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Belgrade")
except Exception:
    LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=2))

def local_time():
    return datetime.datetime.now(LOCAL_TZ)

# Streamlit Page Config (Mora biti na samom vrhu)
st.set_page_config(page_title="Delivery Monitor", page_icon="🍔", layout="wide")

# ================= MODERN CSS DESIGN =================
st.markdown("""
<style>
    .live-card { display: flex; gap: 20px; background: #f8f9fa; padding: 15px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    .wolt-card { flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #00c2e8; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .glovo-card { flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #ffc244; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .metric-value { font-size: 32px; font-weight: bold; margin: 0; }
    .metric-title { font-size: 14px; color: #666; margin: 0; text-transform: uppercase; letter-spacing: 1px;}
    .kpi-wrapper { display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }
    .kpi-card { flex: 1; background: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.04); border: 1px solid #f0f2f6; text-align: center; }
    .kpi-title { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; font-weight: 700;}
    .kpi-value { font-size: 36px; font-weight: 800; color: #2c3e50; margin: 0; line-height: 1.1;}
</style>
""", unsafe_allow_html=True)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# POPRAVKA: Optimizovana instalacija Playwright-a (pokreće se samo jednom)
@st.cache_resource
def install_playwright():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        st.error(f"Greška pri instalaciji Playwright-a: {e}")

install_playwright()

# ================= GLOBAL SETTINGS =================
EMAIL_SENDER = st.secrets.get("EMAIL_SENDER", "webb987@gmail.com")
EMAIL_PASSWORD = st.secrets.get("EMAIL_PASSWORD", "sdehqzbnqefjlomo")

OUTPUT_DIR = Path.cwd() / "reports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = OUTPUT_DIR / "delivery_history.csv"

def timestamp(): return local_time().strftime("%Y%m%d_%H%M%S")
def format_time_short(): return local_time().strftime("%H:%M")
def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder: placeholder.text(msg)

# ---------------- POMOĆNE FUNKCIJE ----------------
def cyrillic_to_latin(text):
    if not text: return ""
    mapa = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','ђ':'dj','е':'e','ж':'z','з':'z','и':'i',
        'ј':'j','к':'k','л':'l','љ':'lj','м':'m','н':'n','њ':'nj','о':'o','п':'p','р':'r',
        'с':'s','т':'t','ћ':'c','у':'u','ф':'f','х':'h','ц':'c','ч':'c','џ':'dz','ш':'s',
        'А':'A','Б':'B','В':'V','Г':'G','Д':'D','Ђ':'Dj','Е':'E','Ж':'Z','З':'Z','И':'I',
        'Ј':'J','К':'K','Л':'L','Љ':'Lj','М':'M','Н':'N','Њ':'Nj','О':'O','П':'P','Р':'R',
        'С':'S','Т':'T','Ћ':'C','У':'U','Ф':'F','Х':'H','Ц':'C','Ч':'C','Џ':'Dz','Ш':'S'
    }
    for k, v in mapa.items(): text = text.replace(k, v)
    return text

def remove_accents(text):
    if not text: return ""
    for k, v in {'č':'c','ć':'c','ž':'z','š':'s','đ':'dj','Č':'C','Ć':'C','Ž':'Z','Š':'S','Đ':'Dj'}.items():
        text = text.replace(k, v)
    return text

def extract_name(text):
    if not text: return ""
    for line in str(text).split('\n'):
        line = line.strip()
        if not line or '%' in line or ("min" in line.lower() and re.search(r'\d+', line.lower())): continue
        if any(x in line.lower() for x in ["rsd","din","promo","novo","new","odlično","besplatna dostava","artikli","narudžb","popust","off","discount"]): continue
        if len(line) >= 2: return line
    return ""

def analyze_status(text):
    t = text.lower()
    if any(x in t for x in ["uskoro se zatvara","closing soon","zatvara se za","closes in"]): return "Open"
    if any(k in t for k in ["samo preuzimanje","samo za preuzimanje","pickup only","dostava nije dostupna","dostava trenutno nije","samo licno preuzimanje","zatvoreno","zakažite","zakaži","zakazi","nedostupno","otvara se","otvara","closed","schedule"]): return "Closed"
    return "Open"

def extract_rating(text, plat):
    try:
        clean_text = re.sub(r'<[^>]+>', ' ', str(text)).lower()
        if plat == "Glovo":
            for p in re.findall(r'(\d{1,3})\s*%', clean_text):
                if int(p) >= 60: return p + "%"
        elif plat == "Wolt":
            m = re.search(r'\b([5-9][.,][0-9]|10[.,]0)\b', clean_text)
            if m: return m.group(1).replace(',', '.')
    except: pass
    return "-"

def extract_delivery_time(text):
    try:
        clean = re.sub(r'<[^>]+>', ' ', str(text)).lower()
        m1 = re.search(r'(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(?:min|m|\')', clean)
        if m1 and int(m1.group(1)) < 120 and int(m1.group(2)) < 120:
            return f"{m1.group(1)}-{m1.group(2)} min", (int(m1.group(1)) + int(m1.group(2))) / 2.0
        m2 = re.search(r'\b(\d{1,3})\s*(?:min|m|\')', clean)
        if m2 and int(m2.group(1)) < 120: return f"{m2.group(1)} min", float(m2.group(1))
    except: pass
    return "-", np.nan

def extract_promo(text, html_content, plat):
    clean_text = (str(text) + " \n " + str(html_content)).lower()
    clean_text = re.sub(r'<[^>]+>', ' ', clean_text)
    promos, seen, res = [], set(), []
    if any(x in clean_text for x in ["besplatna dostava","free delivery","besplatna"]): promos.append("Free delivery")
    for a in promos:
        ac = a[0].upper() + a[1:]
        if ac not in seen:
            seen.add(ac)
            res.append(f"• {ac}")
    return "\n".join(res) if res else "-"

def normalize_name(name): return re.sub(r'[^\w]', '', str(name).lower())

def refresh_live_ui(ph, wolt_count, glovo_count, address, custom_text=None):
    txt = custom_text if custom_text else f"📍 Trenutno skenira: <b>{address}</b>"
    html = f"""
    <div class="live-card">
        <div class="wolt-card">
            <p class="metric-title">🚲 Wolt</p>
            <p class="metric-value" style="color: #00c2e8;">{wolt_count}</p>
        </div>
        <div class="glovo-card">
            <p class="metric-title">🍔 Glovo</p>
            <p class="metric-value" style="color: #ffc244;">{glovo_count}</p>
        </div>
    </div>
    <p style="text-align: center; color: #666; font-size: 14px;">{txt}</p>
    """
    ph.markdown(html, unsafe_allow_html=True)

# ---------------- SMART SCROLLING (GLOVO UI) ----------------
async def smart_scroll_and_extract(page, plat, address, log_ph=None, live_ph=None, live_state=None):
    results_dict = {}
    prev_count = 0
    GLOVO_SELECTORS = ["a[data-testid='store-card']", ".store-card a", "a[href*='/store/']"]

    log_msg("[GLOVO] Pokrećem skrolovanje i ekstrakciju...", log_ph)
    for _ in range(30): # Smanjeno na 30 radi bržeg izvršavanja na Streamlit-u
        data = await page.evaluate(''' (selectors) => {
            let rez = [];
            for (let sel of selectors) {
                document.querySelectorAll(sel).forEach(c => {
                    if (c.href) rez.push({link: c.href, text: c.innerText, html: c.innerHTML});
                });
            }
            return rez;
        }''', GLOVO_SELECTORS)

        for item in data:
            link = item['link']
            if link in results_dict: continue
            name = remove_accents(extract_name(item['text']))
            if len(name) < 2: continue
            all_text = item['text'] + " " + item['html']
            results_dict[link] = {
                "Address": address, "Platform": plat, "Name": name, "Rating": extract_rating(all_text, plat),
                "Delivery Time": extract_delivery_time(all_text)[0], "Promo": extract_promo(item['text'], item['html'], plat),
                "Status": analyze_status(all_text), "Time_Num": extract_delivery_time(all_text)[1], "Link": link
            }

        current = len(results_dict)
        if current > prev_count:
            if live_ph and live_state:
                live_state[plat] = current
                refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)
            prev_count = current
        await page.evaluate("window.scrollBy(0, 600);")
        await asyncio.sleep(0.5)
    return list(results_dict.values())

# ---------------- WOLT HIBRIDNI API SCRAPER ----------------
async def scrape_wolt_api(context_wolt, address, log_ph=None, live_ph=None, live_state=None):
    results_dict = {}
    try:
        log_msg(f"[WOLT] Geocoding adrese: {address}...", log_ph)
        
        geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address + ', Serbia')}&format=json&limit=1&email=kontakt@example.com"
        req = urllib.request.Request(geo_url, headers={'User-Agent': 'DeliveryMonitor/1.0'})
        
        # POPRAVKA: Bezbedno otvaranje URL-a bez blokiranja
        with urllib.request.urlopen(req, timeout=10) as response:
            geo_data = json.loads(response.read().decode())
        
        if not geo_data:
            log_msg(f"[WOLT ERROR] Koordinate nisu pronađene za: {address}", log_ph)
            return []

        lat, lon = geo_data[0]["lat"], geo_data[0]["lon"]
        page = await context_wolt.new_page()
        
        ep = f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}"
        await page.goto("https://wolt.com", wait_until="domcontentloaded")
        
        batch = await page.evaluate(f"""async () => {{
            try {{
                let res = await fetch("{ep}");
                if (!res.ok) return [];
                let d = await res.json();
                let items = [];
                for (let sec of (d.sections || [])) {{
                    for (let it of (sec.items || [])) {{ if (it.venue) items.push(it); }}
                }}
                return items;
            }} catch(e) {{ return []; }}
        }}""")

        for item in batch:
            venue = item.get("venue", {})
            slug = venue.get("slug")
            if not slug: continue
            
            name = remove_accents(venue.get("name", ""))
            results_dict[slug] = {
                "Address": address, "Platform": "Wolt", "Name": name, 
                "Rating": str(venue.get("rating", {}).get("score", "-")),
                "Delivery Time": "-", "Promo": "-", "Status": "Open" if venue.get("online", True) else "Closed",
                "Time_Num": np.nan, "Link": f"https://wolt.com/sr/srb/restaurant/{slug}"
            }
            
        if live_ph and live_state:
            live_state["Wolt"] = len(results_dict)
            refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)

    except Exception as e:
        log_msg(f"[WOLT MAIN ERROR] {e}", log_ph)
        
    return list(results_dict.values())

# ================= ASYNC RUNNER WRAPPER =================
# POPRAVKA: Prosleđivanje placeholder-a u glavnu async funkciju
async def run_scraper(address, log_ph, live_ph, live_state):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        
        # Wolt scrap
        wolt_data = await scrape_wolt_api(context, address, log_ph, live_ph, live_state)
        
        # Glovo scrap
        glovo_page = await context.new_page()
        log_msg("[GLOVO] Otvaram Glovo stranicu...", log_ph)
        await glovo_page.goto("https://glovoapp.com/rs/sr/beograd/", wait_until="domcontentloaded")
        glovo_data = await smart_scroll_and_extract(glovo_page, "Glovo", address, log_ph, live_ph, live_state)
        
        await browser.close()
        return wolt_data + glovo_data

# ================= STREAMLIT UI & MAIN FLOW =================
st.title("🍔 Delivery Monitor Sustav")

address_input = st.text_input("Unesi adresu za proveru:", "Knez Mihailova, Beograd")
btn_start = st.button("Pokreni Skeniranje")

live_placeholder = st.empty()
log_placeholder = st.empty()

if btn_start:
    live_state = {"Wolt": 0, "Glovo": 0}
    refresh_live_ui(live_placeholder, 0, 0, address_input)
    
    # POPRAVKA: Pravilan asinhroni poziv unutar Streamlit-a bez rušenja petlje
    try:
        final_data = asyncio.run(run_scraper(address_input, log_placeholder, live_placeholder, live_state))
        
        if final_data:
            df = pd.DataFrame(final_data)
            st.success(f"Skeniranje završeno! Pronađeno ukupno {len(df)} objekata.")
            st.dataframe(df)
        else:
            st.warning("Nema pronađenih podataka. Proveri internet vezu ili adresu.")
    except Exception as main_err:
        st.error(f"Došlo je do greške prilikom izvršavanja: {main_err}")
