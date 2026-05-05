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

# Konfiguracija Streamlit stranice
st.set_page_config(page_title="Nadzor Dostave V3", page_icon="🍔", layout="wide")

# ================= MODERNI CSS DIZAJN =================
st.markdown("""
<style>
    .live-card { display: flex; gap: 20px; background: #f8f9fa; padding: 15px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    .wolt-card { flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #00c2e8; }
    .glovo-card { flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #ffc244; }
    .metric-value { font-size: 32px; font-weight: bold; margin: 0; }
    .metric-title { font-size: 14px; color: #666; margin: 0; text-transform: uppercase; }
    .kpi-wrapper { display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }
    .kpi-card { flex: 1; background: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.04); border: 1px solid #f0f2f6; text-align: center; }
    .kpi-wolt { border-bottom: 4px solid #00c2e8; }
    .kpi-glovo { border-bottom: 4px solid #ffc244; }
</style>
""", unsafe_allow_html=True)

# ================= POMOĆNE FUNKCIJE =================
OUTPUT_DIR = Path.cwd() / "izvestaji"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = OUTPUT_DIR / "istorija_dostave.csv"
EMAIL_POSILJAOCA = "webb987@gmail.com"
LOZINKA_POSILJAOCA = "sdehqzbnqefjlomo" 

def log_msg(msg, placeholder=None):
    if placeholder: placeholder.text(msg)
    print(msg)

def timestamp(): return lokalno_vreme().strftime("%Y%m%d_%H%M%S")
def format_time_short(): return lokalno_vreme().strftime("%H:%M")

def ukloni_kvacice(tekst):
    if not tekst: return ""
    for k, v in {'č':'c', 'ć':'c', 'ž':'z', 'š':'s', 'đ':'dj', 'Č':'C', 'Ć':'C', 'Ž':'Z', 'Š':'S', 'Đ':'Dj'}.items(): tekst = tekst.replace(k, v)
    return tekst

def osvezi_live_ui(ph, wolt_count, glovo_count, adresa):
    html = f"""
    <div class="live-card">
        <div class="wolt-card"><p class="metric-title">🚲 Wolt (API)</p><p class="metric-value" style="color: #00c2e8;">{wolt_count}</p></div>
        <div class="glovo-card"><p class="metric-title">🍔 Glovo (Browser)</p><p class="metric-value" style="color: #ffc244;">{glovo_count}</p></div>
    </div>
    <p style="text-align: center; color: #666; font-size: 14px;">📍 Skeniram: <b>{adresa}</b></p>
    """
    ph.markdown(html, unsafe_allow_html=True)

# ================= WOLT API LOGIKA =================

def dobavi_koordinate(adresa):
    url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(adresa)}&format=json&limit=1"
    headers = {"User-Agent": "Wolt_Monitor_Srbija/2.0"}
    try:
        odgovor = requests.get(url, headers=headers, timeout=10)
        podaci = odgovor.json()
        if podaci:
            return podaci[0]["lat"], podaci[0]["lon"]
    except Exception as e:
        print(f"Greška geokodiranja: {e}")
    return None, None

def scrape_wolt_api(adresa, log_ph=None):
    log_msg(f"[WOLT API] Tražim koordinate za: {adresa}", log_ph)
    lat, lon = dobavi_koordinate(adresa)
    if not lat or not lon:
        log_msg(f"[WOLT API] Neuspešno geokodiranje!", log_ph)
        return []

    url = f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    try:
        odgovor = requests.get(url, headers=headers, timeout=15)
        if odgovor.status_code != 200: return []
        
        podaci = odgovor.json()
        rezultati = []
        
        for sekcija in podaci.get("sections", []):
            for stavka in sekcija.get("items", []):
                venue = stavka.get("venue")
                if not venue: continue
                
                ime = venue.get("name", "Nepoznato")
                slug = venue.get("slug", "")
                link = f"https://wolt.com/sr/srb/beograd/restaurant/{slug}"
                
                # Status
                je_online = venue.get("online", False)
                status = "Otvoreno" if je_online else "Zatvoreno"
                
                # Vreme i Ocena
                est = venue.get("estimate_range", "Nema podataka")
                vreme_str = f"{est} min" if est else "-"
                vreme_num = float(est.split('-')[0]) if est and '-' in str(est) else (float(est) if str(est).isdigit() else np.nan)
                
                ocena_val = venue.get("rating", {}).get("score", "-")
                
                # Akcije (Badges)
                bedzevi = venue.get("badges", [])
                akcije_lista = [f"• {b.get('text')}" for b in bedzevi if b.get("text")]
                akcija_final = "\n".join(akcije_lista) if akcije_lista else "-"

                rezultati.append({
                    "Adresa": adresa,
                    "Platforma": "Wolt",
                    "Naziv": ukloni_kvacice(ime),
                    "Ocena": str(ocena_val),
                    "Vreme dostave": vreme_str,
                    "Akcija": akcija_final,
                    "Status": status,
                    "Vreme_Broj": vreme_num,
                    "Is_New": "novo" in str(akcije_lista).lower(),
                    "Link": link
                })
        
        # Uklanjanje duplikata po Linku
        df_temp = pd.DataFrame(rezultati).drop_duplicates(subset=['Link'])
        return df_temp.to_dict('records')

    except Exception as e:
        log_msg(f"[WOLT API GREŠKA] {e}", log_ph)
        return []

# ================= GLOVO BROWSER LOGIKA (Zadržano) =================
# ... (Zadržavamo tvoju funkciju scrape_glovo i pametno_skrolovanje jer Glovo API zahteva potpisane tokene)

# ================= INTEGRISANI PROCES =================

async def proces_skeniranja_v3(adrese, log_ph, live_ph, live_state):
    sve_ukupno = []
    
    # 1. WOLT ZAVRŠAVAMO ODMAH PREKO API-ja
    for adr in adrese:
        log_msg(f"🚀 Pokrećem WOLT API za: {adr}", log_ph)
        wolt_rez = scrape_wolt_api(adr, log_ph)
        sve_ukupno.extend(wolt_rez)
        live_state["Wolt"] = len(wolt_rez)
        osvezi_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], adr)

    # 2. GLOVO RADIMO PREKO PLAYWRIGHT-a (kao i do sada)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # ... (Ostatak tvog Glovo koda ovde ide)
        # Ovde pozivaš svoju staru scrape_glovo funkciju
        for adr in adrese:
            log_msg(f"📱 Pokrećem GLOVO Browser za: {adr}", log_ph)
            # context_glovo = ... 
            # g_rez = await scrape_glovo(...)
            # sve_ukupno.extend(g_rez)
            # live_state["Glovo"] = len(g_rez)
            # osvezi_live_ui(...)
        await browser.close()
        
    return pd.DataFrame(sve_ukupno)

# ================= REZIME ZA NADOGRADNJU =================
"""
SAVET ZA IMPLEMENTACIJU:
1. Zameni staru 'scrape_wolt' funkciju ovom novom 'scrape_wolt_api'.
2. U glavnoj petlji 'proces_skeniranja' više ne moraš da otvaraš browser context za Wolt.
3. Wolt API će ti vratiti SVE restorane u jednom zahtevu, nema potrebe za skrolovanjem.
4. Ovo će smanjiti šansu da te Wolt blokira jer se ponašaš kao njihova mobilna aplikacija.
"""
