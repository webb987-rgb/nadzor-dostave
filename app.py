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
from io import BytesIO
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import time
import streamlit as st
import sys

# TIMEZONE CONFIGURATION
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

# ================= MODERN CSS DESIGN =================
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

# ================= WINDOWS FIX =================
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
ERRORS_DIR = Path.cwd() / "debug"
ERRORS_DIR.mkdir(parents=True, exist_ok=True)

GLOVO_AUTH_FILE = "glovo_auth.json"
WOLT_AUTH_FILE = "wolt_auth.json"

def timestamp(): return local_time().strftime("%Y%m%d_%H%M%S")
def format_time_short(): return local_time().strftime("%H:%M")
def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder: placeholder.text(msg)

# ---------------- LIVE UI ----------------
def refresh_live_ui(ph, wolt_count, glovo_count, address):
    html = f"""
    <div class="live-card">
        <div class="wolt-card"><p class="metric-title">🚲 Wolt</p><p class="metric-value" style="color: #00c2e8;">{wolt_count}</p></div>
        <div class="glovo-card"><p class="metric-title">🍔 Glovo</p><p class="metric-value" style="color: #ffc244;">{glovo_count}</p></div>
    </div>
    <p style="text-align: center; color: #666; font-size: 14px;">📍 Currently scanning: <b>{address}</b></p>
    """
    ph.markdown(html, unsafe_allow_html=True)

# ---------------- DATA EXTRACTION ----------------
def clean_text(text):
    if not text: return ""
    chars = {'č':'c', 'ć':'c', 'ž':'z', 'š':'s', 'đ':'dj', 'Č':'C', 'Ć':'C', 'Ž':'Z', 'Š':'S', 'Đ':'Dj'}
    for k, v in chars.items(): text = text.replace(k, v)
    return text

def extract_name(text):
    if not text: return ""
    for line in str(text).split('\n'):
        line = line.strip()
        if not line or '%' in line or ("min" in line.lower()): continue
        if any(x in line.lower() for x in ["rsd", "din", "promo", "new", "free delivery", "off", "discount"]): continue
        if len(line) >= 2: return line
    return "Unknown Store"

def analyze_status(text):
    t = text.lower()
    if any(x in t for x in ["closing soon", "closes in", "uskoro se zatvara"]): return "Open"
    if any(k in t for k in ["pickup only", "delivery unavailable", "closed", "schedule", "zatvoreno", "narucite za kasnije"]): return "Closed"
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

# UPDATED: GLOVO PROMO DETECTION BASED ON SCREENSHOT
def extract_promo(text, html, plat):
    combined = (str(text) + " " + str(html)).lower()
    promos = []
    
    # Check for the specific Glovo promo tag found in the screenshot
    if plat == "Glovo":
        # Targeted regex for labels like "-20% some items" or "30% on orders"
        found_labels = re.findall(r'(\d{1,2}%)\s*(?:off|some items|on orders|popusta)?', combined)
        for fl in found_labels:
            promos.append(f"{fl} Discount")

    if any(x in combined for x in ["free delivery", "besplatna dostava", "delivery 0"]):
        promos.append("Free Delivery")
    if any(x in combined for x in ["1+1", "buy 1 get 1"]):
        promos.append("1+1 Free")
    if "wolt+" in combined: promos.append("Wolt+")
    
    res = list(set(promos))
    return "\n".join([f"• {p}" for p in res]) if res else "-"

# ---------------- SCRAPING ENGINE ----------------
async def smart_scroll_and_extract(page, plat, address, log_ph=None, live_ph=None, live_state=None):
    results_dict = {}
    attempts_at_bottom = 0
    prev_count = 0
    
    while True:
        data = await page.evaluate('''() => {
            let items = [];
            // Target containers to ensure we get the floating promo badges
            let selectors = "a[data-test-id^='venueCard.'], a[data-testid='store-card'], .store-card a, li:has(a)";
            document.querySelectorAll(selectors).forEach(c => {
                let link = c.tagName === 'A' ? c.href : c.querySelector('a')?.href;
                if (link && !link.includes('/category')) {
                    items.push({link: link, text: c.innerText, html: c.innerHTML});
                }
            });
            return items;
        }''')

        for item in data:
            link = item['link']
            if not link or link in results_dict: continue
            
            name = clean_text(extract_name(item['text']))
            promo = extract_promo(item['text'], item['html'], plat)
            
            results_dict[link] = {
                "Address": address, "Platform": plat, "Name": name, 
                "Rating": extract_rating(item['text'], plat),
                "Promo": promo, "Status": analyze_status(item['text']),
                "Link": link
            }

        if len(results_dict) > prev_count:
            prev_count = len(results_dict)
            attempts_at_bottom = 0
            if live_ph:
                live_state[plat] = prev_count
                refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)
        
        await page.evaluate("window.scrollBy(0, 800);")
        await asyncio.sleep(0.8)
        
        # Check if bottom reached
        is_bottom = await page.evaluate("window.scrollY + window.innerHeight >= document.body.scrollHeight - 100")
        if is_bottom:
            attempts_at_bottom += 1
            if attempts_at_bottom > 4: break
            
    return list(results_dict.values())

async def run_scraper(addresses, debug_mode):
    all_results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        if debug_mode:
            context_args["record_video_dir"] = str(ERRORS_DIR)

        for adr in addresses:
            st.write(f"🔍 Processing: {adr}")
            # GLOVO
            ctx_g = await browser.new_context(**context_args)
            page_g = await ctx_g.new_page()
            await page_g.goto("https://glovoapp.com/sr/rs")
            # Logic for address entry...
            # (Similar to your existing logic but translated)
            res_g = await smart_scroll_and_extract(page_g, "Glovo", adr)
            all_results.extend(res_g)
            await ctx_g.close()

            # WOLT
            ctx_w = await browser.new_context(**context_args)
            page_w = await ctx_w.new_page()
            await page_w.goto("https://wolt.com/sr/srb")
            # Logic for address entry...
            res_w = await smart_scroll_and_extract(page_w, "Wolt", adr)
            all_results.extend(res_w)
            await ctx_w.close()
            
        await browser.close()
    return pd.DataFrame(all_results)

# ================= STREAMLIT APP =================
st.sidebar.header("⚙️ Settings")
addr1 = st.sidebar.text_input("📍 Address 1:", placeholder="e.g. Makenzijeva 57, Belgrade")
addr2 = st.sidebar.text_input("📍 Address 2 (Optional):")
debug_toggle = st.sidebar.checkbox("🛠️ Enable Debug Mode (Video/HTML Logs)", value=False)

if st.sidebar.button("▶️ START SCANNING", type="primary"):
    list_addr = [a.strip() for a in [addr1, addr2] if a.strip()]
    if list_addr:
        df_final = asyncio.run(run_scraper(list_addr, debug_toggle))
        if not df_final.empty:
            st.success("✅ Scanning Complete!")
            st.dataframe(df_final)
    else:
        st.error("Please enter at least one address.")
