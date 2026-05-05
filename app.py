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
st.set_page_config(page_title="Nadzor Dostave", page_icon="🍔", layout="wide")

# ================= MODERNI CSS DIZAJN =================
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

# ================= POMOĆNE FUNKCIJE =================
def timestamp(): return lokalno_vreme().strftime("%Y%m%d_%H%M%S")
def format_time_short(): return lokalno_vreme().strftime("%H:%M")
def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder: placeholder.text(msg)

def osvezi_live_ui(ph, wolt_count, glovo_count, adresa):
    html = f"""
    <div class="live-card">
        <div class="wolt-card"><p class="metric-title">🚲 Wolt (API)</p><p class="metric-value" style="color: #00c2e8;">{wolt_count}</p></div>
        <div class="glovo-card"><p class="metric-title">🍔 Glovo (Web)</p><p class="metric-value" style="color: #ffc244;">{glovo_count}</p></div>
    </div>
    <p style="text-align: center; color: #666; font-size: 14px;">📍 Trenutno skeniram: <b>{adresa}</b></p>
    """
    ph.markdown(html, unsafe_allow_html=True)

# Funkcija za koordinate (Wolt API zahteva lat/lon)
def dobavi_koordinate(adresa):
    url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(adresa)}&format=json&limit=1"
    headers = {"User-Agent": "WoltNadzor/2.0"}
    try:
        odgovor = requests.get(url, headers=headers, timeout=10)
        podaci = odgovor.json()
        if podaci: return podaci[0]["lat"], podaci[0]["lon"]
    except: pass
    return None, None

def ukloni_kvacice(tekst):
    if not tekst: return ""
    for k, v in {'č':'c', 'ć':'c', 'ž':'z', 'š':'s', 'đ':'dj', 'Č':'C', 'Ć':'C', 'Ž':'Z', 'Š':'S', 'Đ':'Dj'}.items(): tekst = tekst.replace(k, v)
    return tekst

def cirilica_u_latinicu(tekst):
    if not tekst: return ""
    mapa = { 'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s', 'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'Њ':'Nj', 'О':'O', 'П':'P', 'Р':'R', 'С':'S', 'Т':'T', 'Ћ':'C', 'У':'U', 'Ф':'F', 'Х':'H', 'Ц':'C', 'Ч':'C', 'Џ':'Dz', 'Š':'S' }
    for k, v in mapa.items(): tekst = tekst.replace(k, v)
    return tekst

def normalizuj_ime(ime): return re.sub(r'[^\w]', '', str(ime).lower())

# ================= WOLT API LOGIKA (ZAMENA ZA SCRAPE) =================
def scrape_wolt_api(address, log_ph=None, live_ph=None, live_state=None):
    log_msg(f"🚲 WOLT API: Pretvaram adresu u koordinate...", log_ph)
    lat, lon = dobavi_koordinate(address)
    if not lat or not lon:
        log_msg("❌ WOLT API: Neuspešno dobavljanje koordinata.", log_ph)
        return []

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Wolt-Client-Id": "Web-Wolt"
    })

    url = f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}"
    try:
        odgovor = session.get(url, timeout=15)
        if odgovor.status_code != 200: return []
        podaci = odgovor.json()
        
        rezultati = []
        obradjeni_slugovi = set()
        
        for sekcija in podaci.get("sections", []):
            for stavka in sekcija.get("items", []):
                venue = stavka.get("venue")
                if not venue: continue
                
                slug = venue.get("slug")
                if slug in obradjeni_slugovi: continue
                obradjeni_slugovi.add(slug)
                
                naziv = ukloni_kvacice(venue.get("name", ""))
                status = "Otvoreno" if venue.get("online", False) else "Zatvoreno"
                ocena = str(venue.get("rating", {}).get("score", "-"))
                
                # Vreme dostave
                vreme_min = venue.get("estimate_range", {}).get("min", 0)
                vreme_max = venue.get("estimate_range", {}).get("max", 0)
                vreme_str = f"{vreme_min}-{vreme_max} min" if vreme_max > 0 else "-"
                vreme_num = (vreme_min + vreme_max) / 2 if vreme_max > 0 else np.nan
                
                # Akcije
                akcije_lista = []
                # Provera popusta na nivou restorana
                promo_tekst = venue.get("delivery_price_highlight", "")
                if promo_tekst: akcije_lista.append(f"• {promo_tekst}")
                
                # Provera "Novo" statusa
                is_new = venue.get("is_new", False)
                
                rezultati.append({
                    "Adresa": address, "Platforma": "Wolt", "Naziv": naziv, 
                    "Ocena": ocena, "Vreme dostave": vreme_str, 
                    "Akcija": "\n".join(akcije_lista) if akcije_lista else "-",
                    "Status": status, "Vreme_Broj": vreme_num, "Is_New": is_new,
                    "Link": f"https://wolt.com/sr/srb/beograd/restaurant/{slug}"
                })
                
                if live_ph and live_state:
                    live_state["Wolt"] = len(rezultati)
                    osvezi_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)

        log_msg(f"✅ WOLT API: Pronađeno {len(rezultati)} restorana.", log_ph)
        return rezultati
    except Exception as e:
        log_msg(f"❌ WOLT API Greška: {e}", log_ph)
        return []

# ================= GLOVO OSTOJE ISTI (PLAYWRIGHT) =================
# ... (ovde ide tvoja funkcija analiziraj_status, izvuci_ime itd. iz prve skripte) ...

async def pametni_dijetalni_mod(route):
    if route.request.resource_type in ["image", "media"]:
        await route.fulfill(status=200, content_type="image/png", body=b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    else: await route.continue_()

# ... (ovde idu funkcije izvuci_ocenu, izvuci_vreme_dostave, izvuci_akciju koje si već imao) ...
def analiziraj_status(text):
    t = text.lower()
    if any(x in t for x in ["uskoro se zatvara", "closing soon", "zatvara se za"]): return "Otvoreno"
    if any(k in t for k in ["samo preuzimanje", "zatvoreno", "zakažite", "nedostupno", "closed"]): return "Zatvoreno"
    return "Otvoreno"

def izvuci_ime(tekst):
    if not tekst: return ""
    for line in str(tekst).split('\n'):
        line = line.strip()
        if not line or '%' in line or ("min" in line.lower()): continue
        if any(x in line.lower() for x in ["rsd", "din", "promo", "besplatna"]): continue
        if len(line) >= 2: return line
    return ""

def izvuci_ocenu(tekst, plat):
    try:
        cist_tekst = re.sub(r'<[^>]+>', ' ', str(tekst)).lower()
        if plat == "Glovo":
            for p in re.findall(r'(\d{1,3})\s*%', cist_tekst):
                if int(p) >= 60: return p + "%"
        return "-"
    except: return "-"

def izvuci_vreme_dostave(tekst):
    try:
        cist = re.sub(r'<[^>]+>', ' ', str(tekst)).lower()
        m1 = re.search(r'(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(?:min|m)', cist)
        if m1: return f"{m1.group(1)}-{m1.group(2)} min", (int(m1.group(1)) + int(m1.group(2))) / 2.0
        m2 = re.search(r'\b(\d{1,3})\s*(?:min|m)', cist)
        if m2: return f"{m2.group(1)} min", float(m2.group(1))
    except: pass
    return "-", np.nan

def izvuci_akciju(tekst, html, plat):
    cist = (str(tekst) + " " + str(html)).lower()
    akcije = []
    if any(x in cist for x in ["besplatna dostava", "free delivery", "dostava 0"]): akcije.append("• Besplatna dostava")
    if "1+1" in cist: akcije.append("• 1+1 Gratis")
    m = re.findall(r'(\d{1,2}\s*%)', cist)
    for p in m: akcije.append(f"• {p} popusta")
    return "\n".join(list(set(akcije))) if akcije else "-"

async def pametno_skrolovanje_i_ekstrakcija(page, plat, address, log_ph, live_ph, live_state):
    results_dict = {}
    prethodni = 0
    zaustavi = 0
    while zaustavi < 5:
        podaci = await page.evaluate('''() => {
            let rez = [];
            document.querySelectorAll("a:has(h3), a[data-testid='store-card'], .store-card a").forEach(c => {
                let link = c.href;
                if (!link.includes('/category')) rez.push({link, text: c.innerText, html: c.innerHTML});
            });
            return rez;
        }''')
        for item in podaci:
            link = item['link']
            if link not in results_dict:
                ime = ukloni_kvacice(izvuci_ime(item['text']))
                if len(ime) < 2: continue
                v_str, v_num = izvuci_vreme_dostave(item['text'])
                results_dict[link] = {
                    "Adresa": address, "Platforma": plat, "Naziv": ime, "Ocena": izvuci_ocenu(item['text'], plat),
                    "Vreme dostave": v_str, "Akcija": izvuci_akciju(item['text'], item['html'], plat),
                    "Status": analiziraj_status(item['text']), "Vreme_Broj": v_num, "Is_New": "novo" in item['text'].lower(), "Link": link
                }
        if len(results_dict) > prethodni:
            prethodni = len(results_dict)
            live_state[plat] = prethodni
            osvezi_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)
            zaustavi = 0
        else: zaustavi += 1
        await page.evaluate("window.scrollBy(0, 600);")
        await asyncio.sleep(0.8)
    return list(results_dict.values())

async def scrape_glovo(context_glovo, address, log_ph, live_ph, live_state):
    page = await context_glovo.new_page()
    try:
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded", timeout=20000)
        try:
            btn = page.locator("button", has_text=re.compile(r"Accept|Prihvati", re.I)).first
            await btn.click(timeout=3000)
        except: pass
        
        # Unos adrese
        inp = page.locator("#hero-container-input")
        await inp.fill(address)
        await asyncio.sleep(2)
        await page.locator("div[data-actionable='true'][role='button']").first.click()
        await asyncio.sleep(5)
        
        # Idi na restorane
        try: await page.get_by_role("link", name=re.compile(r"Restorani|Food", re.I)).first.click()
        except: pass
        await asyncio.sleep(3)
        
        return await pametno_skrolovanje_i_ekstrakcija(page, "Glovo", address, log_ph, live_ph, live_state)
    except Exception as e:
        log_msg(f"❌ GLOVO Greška: {e}", log_ph)
        return []
    finally: await page.close()

# ================= SEKVENCIJALNI PROCES SKENIRANJA =================
async def proces_skeniranja(adrese, log_ph, live_ph, live_state, generisi_pdf=False, email_primaoca=""):
    sve = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        for adr in adrese:
            live_state["Wolt"] = 0
            live_state["Glovo"] = 0
            osvezi_live_ui(live_ph, 0, 0, adr)
            
            # 1. WOLT API (Novi način)
            r_wolt = scrape_wolt_api(adr, log_ph, live_ph, live_state)
            sve.extend(r_wolt)
            
            # 2. GLOVO (Playwright - ostaje isto)
            log_msg(f"📱 GLOVO: Pokrećem browser za {adr}...", log_ph)
            ctx = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            await ctx.route("**/*", pametni_dijetalni_mod)
            r_glovo = await scrape_glovo(ctx, adr, log_ph, live_ph, live_state)
            sve.extend(r_glovo)
            await ctx.close()
            
        await browser.close()
        
    if sve:
        df_s = pd.DataFrame(sve)
        # Ovde bi išle tvoje funkcije za PDF i istoriju koje si već imao
        return df_s, pd.DataFrame(), [], []
    return pd.DataFrame(), pd.DataFrame(), [], []

# ================= OSTATAK STREAMLIT UI =================
# (Ovde ubaci sav preostali kod iz tvoje originalne skripte: 
# sacuvaj_u_istoriju, grafikone, PDF funkcije i Streamlit tabove)
# Kod ostaje identičan jer dataframe struktura ("Adresa", "Platforma", "Naziv"...) nije menjana.

# ... [Ostatak tvog originalnog koda za UI i PDF] ...

# GLAVNI UI START
if 'pokrenuto' not in st.session_state: st.session_state.pokrenuto = False
if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()

# Sidebar (Isto kao tvoje)
with st.sidebar:
    st.header("⚙️ Podešavanja")
    adresa_1 = st.text_input("📍 Adresa 1:", value="Makenzijeva 57, Beograd")
    if st.button("▶️ POKRENI"):
        st.session_state.pokrenuto = True
        st.rerun()

if st.session_state.pokrenuto:
    with st.spinner('🔄 Skeniranje u toku...'):
        live_ui_ph = st.empty()
        sl = st.empty()
        live_state = {"Wolt": 0, "Glovo": 0}
        df, hi, pdf, err = asyncio.run(proces_skeniranja([adresa_1], sl, live_ui_ph, live_state))
        st.session_state.df_sve = df
        st.session_state.pokrenuto = False
        st.success("Skeniranje završeno!")

if not st.session_state.df_sve.empty:
    st.dataframe(st.session_state.df_sve, use_container_width=True)
