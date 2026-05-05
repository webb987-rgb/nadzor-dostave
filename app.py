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
import requests
import urllib.parse
from io import BytesIO
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import time
import streamlit as st
import sys

# PODEŠAVANJE LOKALNOG VREMENA
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Belgrade")
except Exception:
    LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=2))

def lokalno_vreme():
    return datetime.datetime.now(LOCAL_TZ)

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Konfiguracija Streamlit stranice
st.set_page_config(page_title="Nadzor Dostave PRO", page_icon="🍔", layout="wide")

# ================= MODERNI CSS DIZAJN (VRAĆENO SVE) =================
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
    .kpi-title { font-size: 12px; color: #888; text-transform: uppercase; font-weight: 700;}
    .kpi-value { font-size: 36px; font-weight: 800; color: #2c3e50; margin: 0; }
    .kpi-wolt { border-bottom: 4px solid #00c2e8; }
    .kpi-glovo { border-bottom: 4px solid #ffc244; }
</style>
""", unsafe_allow_html=True)

# ================= GLOBALNA PODEŠAVANJA =================
EMAIL_POSILJAOCA = "webb987@gmail.com"
LOZINKA_POSILJAOCA = "sdehqzbnqefjlomo" 

OUTPUT_DIR = Path.cwd() / "izvestaji"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = OUTPUT_DIR / "istorija_dostave.csv"
ERRORS_DIR = Path.cwd() / "greske"
ERRORS_DIR.mkdir(parents=True, exist_ok=True)

GLOVO_AUTH_FILE = "glovo_auth.json"

def timestamp(): return lokalno_vreme().strftime("%Y%m%d_%H%M%S")
def format_time_short(): return lokalno_vreme().strftime("%H:%M")
def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder: placeholder.text(msg)

# ---------------- POMOĆNE FUNKCIJE ----------------
def cirilica_u_latinicu(tekst):
    if not tekst: return ""
    mapa = { 'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s', 'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'Њ':'Nj', 'О':'O', 'П':'P', 'Р':'R', 'С':'S', 'Т':'T', 'Ћ':'C', 'У':'U', 'Ф':'H', 'Х':'H', 'Ц':'C', 'Ч':'C', 'Џ':'Dz', 'Ш':'S' }
    for k, v in mapa.items(): tekst = tekst.replace(k, v)
    return tekst

def ukloni_kvacice(tekst):
    if not tekst: return ""
    for k, v in {'č':'c', 'ć':'c', 'ž':'z', 'š':'s', 'đ':'dj', 'Č':'C', 'Ć':'C', 'Ž':'Z', 'Š':'S', 'Đ':'Dj'}.items(): tekst = tekst.replace(k, v)
    return tekst

def normalizuj_ime(ime): return re.sub(r'[^\w]', '', str(ime).lower())

def osvezi_live_ui(ph, wolt_count, glovo_count, adresa):
    html = f"""
    <div class="live-card">
        <div class="wolt-card"><p class="metric-title">🚲 Wolt (API)</p><p class="metric-value" style="color: #00c2e8;">{wolt_count}</p></div>
        <div class="glovo-card"><p class="metric-title">🍔 Glovo (Live)</p><p class="metric-value" style="color: #ffc244;">{glovo_count}</p></div>
    </div>
    <p style="text-align: center; color: #666; font-size: 14px;">📍 Trenutno skeniram: <b>{adresa}</b></p>
    """
    ph.markdown(html, unsafe_allow_html=True)

# ================= WOLT API LOGIKA (NOVO) =================

def dobavi_koordinate(adresa):
    url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(adresa)}&format=json&limit=1"
    headers = {"User-Agent": "WoltMonitor_V3"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        if data: return data[0]["lat"], data[0]["lon"]
    except: pass
    return None, None

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
                
                # Ekstrakcija podataka
                ime = v.get("name", "Nepoznato")
                slug = v.get("slug", "")
                link = f"https://wolt.com/sr/srb/beograd/restaurant/{slug}"
                
                # Status & Vreme
                status = "Otvoreno" if v.get("online", False) else "Zatvoreno"
                est = v.get("estimate_range", "")
                v_num = float(est.split('-')[0]) if est and '-' in str(est) else (float(est) if str(est).isdigit() else np.nan)
                
                # Akcije (Badges)
                badges = v.get("badges", [])
                promo_list = [f"• {b.get('text')}" for b in badges if b.get('text')]
                
                found.append({
                    "Adresa": adresa, "Platforma": "Wolt", "Naziv": ukloni_kvacice(ime),
                    "Ocena": str(v.get("rating", {}).get("score", "-")),
                    "Vreme dostave": f"{est} min" if est else "-", "Vreme_Broj": v_num,
                    "Akcija": "\n".join(promo_list) if promo_list else "-",
                    "Status": status, "Is_New": "novo" in str(promo_list).lower(),
                    "Link": link
                })
        
        return pd.DataFrame(found).drop_duplicates(subset=['Link']).to_dict('records')
    except Exception as e:
        log_msg(f"Wolt API Greška: {e}", log_ph)
        return []

# ================= GLOVO LOGIKA (VRAĆENO KAKO JE BILO) =================

def analiziraj_status_glovo(text):
    t = text.lower()
    if any(x in t for x in ["uskoro se zatvara", "closing soon", "zatvara se za"]): return "Otvoreno"
    if any(k in t for k in ["samo preuzimanje", "zatvoreno", "zakažite", "zakaži", "nedostupno", "otvara se", "closed"]): return "Zatvoreno"
    return "Otvoreno"

async def pametno_skrolovanje_glovo(page, address, log_ph, live_ph, live_state):
    results = {}
    prethodni = 0
    dno_count = 0
    
    while dno_count < 5:
        podaci = await page.evaluate('''() => {
            let rez = [];
            document.querySelectorAll("a:has(h3), a[data-testid='store-card']").forEach(c => {
                let link = c.href;
                if (link && !link.includes('/category')) {
                    rez.push({link, text: c.innerText, html: c.innerHTML});
                }
            });
            return rez;
        }''')
        
        for item in podaci:
            if item['link'] not in results:
                t = item['text']
                results[item['link']] = {
                    "Adresa": address, "Platforma": "Glovo", 
                    "Naziv": ukloni_kvacice(t.split('\n')[0]),
                    "Ocena": "-", "Vreme dostave": "-", "Akcija": "-",
                    "Status": analiziraj_status_glovo(t), "Link": item['link']
                }
        
        if len(results) > prethodni:
            prethodni = len(results)
            live_state["Glovo"] = prethodni
            osvezi_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)
            dno_count = 0
        else: dno_count += 1
        
        await page.evaluate("window.scrollBy(0, 800);")
        await asyncio.sleep(0.8)
    return list(results.values())

async def scrape_glovo(context, address, log_ph, live_ph, live_state):
    page = await context.new_page()
    try:
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded", timeout=20000)
        # Skloni kolačiće
        try:
            btn = page.locator("button", has_text=re.compile(r"Accept All|Prihvati sve", re.I)).first
            await btn.click(timeout=3000)
        except: pass
        
        # Unos adrese
        inp = page.locator("#hero-container-input")
        await inp.fill(address)
        await asyncio.sleep(1.5)
        await page.keyboard.press("Enter")
        await asyncio.sleep(4)
        
        # Kategorija restorani
        try:
            await page.locator("text='Restorani'").first.click(timeout=5000)
            await asyncio.sleep(3)
        except: pass
        
        return await pametno_skrolovanje_glovo(page, address, log_ph, live_ph, live_state)
    except Exception as e:
        log_msg(f"Glovo Greška: {e}", log_ph)
        return []
    finally: await page.close()

# ================= ISTORIJA I GRAFIKONI (VRAĆENO) =================

def sacuvaj_u_istoriju(df):
    vreme = format_time_short()
    datum = lokalno_vreme().strftime("%Y-%m-%d")
    istorija = []
    for adr in df["Adresa"].unique():
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Adresa"] == adr) & (df["Platforma"] == plat)]
            if sub.empty: continue
            istorija.append({
                "Datum": datum, "Vreme": vreme, "Adresa": adr, "Platforma": plat,
                "Otvoreno": len(sub[sub["Status"]=="Otvoreno"]),
                "Zatvoreno": len(sub[sub["Status"]=="Zatvoreno"]),
                "Avg_Vreme": round(sub["Vreme_Broj"].mean(), 1) if "Vreme_Broj" in sub else 0,
                "Broj_Akcija": len(sub[sub["Akcija"] != "-"])
            })
    df_novo = pd.DataFrame(istorija)
    if os.path.exists(HISTORY_FILE):
        df_old = pd.read_csv(HISTORY_FILE)
        df_novo = pd.concat([df_old, df_novo], ignore_index=True)
    df_novo.to_csv(HISTORY_FILE, index=False)
    return df_novo

# Funkcije za UI Grafikone (Plotly) - ovde idu tvoje originalne funkcije
def kreiraj_grafikon_status_ui(df_sub, naslov):
    data = []
    for p in ["Wolt", "Glovo"]:
        s = df_sub[df_sub["Platforma"]==p]
        data.append({"Platforma": p, "Status": "Otvoreno", "Broj": len(s[s["Status"]=="Otvoreno"])})
        data.append({"Platforma": p, "Status": "Zatvoreno", "Broj": len(s[s["Status"]=="Zatvoreno"])})
    fig = px.bar(data, x="Status", y="Broj", color="Platforma", barmode="group",
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, title=naslov)
    return fig

# ================= GLAVNI PROCES =================

async def proces_skeniranja(adrese, log_ph, live_ph, live_state):
    sve_podaci = []
    
    # 1. WOLT API (Munjevito)
    for adr in adrese:
        log_msg(f"🚲 Skeniram Wolt API: {adr}", log_ph)
        w_rez = scrape_wolt_api(adr, log_ph)
        sve_podaci.extend(w_rez)
        live_state["Wolt"] = len(w_rez)
        osvezi_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], adr)
    
    # 2. GLOVO BROWSER (Standardno)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ga = {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        context = await browser.new_context(**ga)
        
        for adr in adrese:
            log_msg(f"🍔 Skeniram Glovo Browser: {adr}", log_ph)
            g_rez = await scrape_glovo(context, adr, log_ph, live_ph, live_state)
            sve_podaci.extend(g_rez)
            
        await browser.close()
    
    df = pd.DataFrame(sve_podaci)
    hist = sacuvaj_u_istoriju(df)
    return df, hist

# ================= STREAMLIT UI (VRAĆENO SVE) =================

if 'pokrenuto' not in st.session_state: st.session_state.pokrenuto = False
if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()

st.title("🚀 Nadzor Dostave PRO (Wolt API Edition)")

with st.sidebar:
    st.header("⚙️ Podešavanja")
    adresa_1 = st.text_input("📍 Adresa 1:", placeholder="Makenzijeva 57, Beograd")
    adresa_2 = st.text_input("📍 Adresa 2:", placeholder="Opciono")
    
    if st.button("▶️ POKRENI SKENIRANJE", type="primary", use_container_width=True):
        st.session_state.pokrenuto = True
        st.rerun()

# Logika pokretanja
if st.session_state.pokrenuto:
    lista_adresa = [a.strip() for a in [adresa_1, adresa_2] if a.strip()]
    if lista_adresa:
        live_ph = st.empty()
        log_ph = st.empty()
        live_state = {"Wolt": 0, "Glovo": 0}
        
        with st.spinner("Prikupljam podatke..."):
            df, hist = asyncio.run(proces_skeniranja(lista_adresa, log_ph, live_ph, live_state))
            st.session_state.df_sve = df
            st.session_state.df_history = hist
        
        log_ph.empty()
        st.session_state.pokrenuto = False
        st.rerun()

# Prikaz rezultata (Tabovi)
if not st.session_state.df_sve.empty:
    df = st.session_state.df_sve
    tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🔍 Detaljna Lista", "⚖️ Uporedni Prikaz"])
    
    with tab1:
        st.plotly_chart(kreiraj_grafikon_status_ui(df, "Ukupni Status Restorana"), use_container_width=True)
        # Ovde dodaj ostale tvoje KPI kartice
        
    with tab2:
        st.dataframe(df, use_container_width=True)
        
    with tab3:
        st.info("Ovde ide tvoja logika za upoređivanje istih restorana...")

else:
    st.info("Unesite adresu i kliknite na dugme za početak.")
