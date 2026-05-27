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
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Streamlit Page Config
st.set_page_config(page_title="Delivery Monitor", page_icon="🍔", layout="wide")

# ================= MODERN CSS DESIGN =================
st.markdown("""
<style>
    .live-card {
        display: flex; gap: 20px; background: #f8f9fa; padding: 15px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }
    .wolt-card {
        flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #00c2e8; box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .glovo-card {
        flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #ffc244; box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .metric-value { font-size: 32px; font-weight: bold; margin: 0; }
    .metric-title { font-size: 14px; color: #666; margin: 0; text-transform: uppercase; letter-spacing: 1px;}
    .kpi-wrapper { display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }
    .kpi-card {
        flex: 1; background: #ffffff; padding: 20px; border-radius: 12px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.04); border: 1px solid #f0f2f6;
        text-align: center; transition: transform 0.2s, box-shadow 0.2s;
    }
    .kpi-card:hover { transform: translateY(-5px); box-shadow: 0 8px 25px rgba(0,0,0,0.1); }
    .kpi-title { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; font-weight: 700;}
    .kpi-value { font-size: 36px; font-weight: 800; color: #2c3e50; margin: 0; line-height: 1.1;}
    .kpi-wolt { border-bottom: 4px solid #00c2e8; }
    .kpi-glovo { border-bottom: 4px solid #ffc244; }
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 8px 8px 0px 0px; padding: 10px 20px; color: #4f4f4f;}
    .stTabs [aria-selected="true"] { background-color: #e0e5ec !important; font-weight: bold; border-bottom: 3px solid #ff4b4b;}
</style>
""", unsafe_allow_html=True)
# ======================================================

# ================= WINDOWS & PLAYWRIGHT FIX =================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

@st.cache_resource
def install_playwright():
    os.system("playwright install chromium")

install_playwright()

# ================= GLOBAL SETTINGS =================
EMAIL_SENDER   = "webb987@gmail.com"
EMAIL_PASSWORD = "sdehqzbnqefjlomo"

OUTPUT_DIR   = Path.cwd() / "reports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = OUTPUT_DIR / "delivery_history.csv"

ERRORS_DIR = Path.cwd() / "errors"
ERRORS_DIR.mkdir(parents=True, exist_ok=True)

GLOVO_AUTH_FILE = "glovo_auth.json"
WOLT_AUTH_FILE  = "wolt_auth.json"

# ── Wolt HTTP sesija (iz skripte 2) ──────────────────────────────────────────
WOLT_FETCH_WORKERS = 10   # broj paralelnih HTTP niti za dohvat akcija

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "sr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://wolt.com",
    "Referer": "https://wolt.com/en/srb/",
    "W-PlatformType": "Web",
    "W-Wolt-Session-Id": "wolt-monitor-session",
}

wolt_session = requests.Session()
wolt_session.headers.update(BROWSER_HEADERS)

_throttle_until = 0.0
_throttle_lock  = threading.Lock()
_fetch_log_lock = threading.Lock()

def _log_fetch(msg: str):
    try:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        with _fetch_log_lock:
            with open("_fetch_debug.log", "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def _wait_throttle():
    now = time.time()
    with _throttle_lock:
        wait = _throttle_until - now
    if wait > 0:
        time.sleep(wait)

def _set_throttle(seconds: float):
    with _throttle_lock:
        global _throttle_until
        _throttle_until = max(_throttle_until, time.time() + seconds)

def _make_thread_session() -> requests.Session:
    s = requests.Session()
    for k, v in wolt_session.headers.items():
        s.headers[k] = v
    return s

def _fetch_url_http(ts, url: str, label: str) -> tuple:
    for attempt in range(4):
        _wait_throttle()
        try:
            time.sleep(random.uniform(0.3, 1.2))
            r = ts.get(url, timeout=12)
            if r.status_code == 200:
                return r.json(), 200
            if r.status_code in (401, 403):
                _log_fetch(f"{label} → {r.status_code} (auth fail)")
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

# ──────────────────────────────────────────────────────────────────────────────

def timestamp():           return local_time().strftime("%Y%m%d_%H%M%S")
def format_time_short():   return local_time().strftime("%H:%M")
def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder:
        placeholder.text(msg)

# ---------------- LIVE COUNTER UI ----------------
def refresh_live_ui(ph, wolt_count, glovo_count, address, custom_text=None):
    txt = custom_text if custom_text else f"📍 Currently scanning: <b>{address}</b>"
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

# ---------------- CYRILLIC & EMAIL SUPPORT ----------------
def cyrillic_to_latin(text):
    if not text: return ""
    mapa = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','ђ':'dj','е':'e','ж':'z','з':'z',
        'и':'i','ј':'j','к':'k','л':'l','љ':'lj','м':'m','н':'n','њ':'nj','о':'o',
        'п':'p','р':'r','с':'s','т':'t','ћ':'c','у':'u','ф':'f','х':'h','ц':'c',
        'ч':'c','џ':'dz','ш':'s',
        'А':'A','Б':'B','В':'V','Г':'G','Д':'D','Ђ':'Dj','Е':'E','Ж':'Z','З':'Z',
        'И':'I','Ј':'J','К':'K','Л':'L','Љ':'Lj','М':'M','Н':'N','Њ':'Nj','О':'O',
        'П':'P','Р':'R','С':'S','Т':'T','Ћ':'C','У':'U','Ф':'F','Х':'H','Ц':'C',
        'Ч':'C','Џ':'Dz','Ш':'S'
    }
    for k, v in mapa.items():
        text = text.replace(k, v)
    return text

def send_email(pdf_paths, recipients_str, log_ph=None):
    recipients_list = [e.strip() for e in recipients_str.split(",") if e.strip()]
    if not recipients_list: return
    try:
        log_msg(f"[SYSTEM] Sending email to: {', '.join(recipients_list)}...", log_ph)
        for recipient in recipients_list:
            msg = MIMEMultipart()
            msg['From']    = EMAIL_SENDER
            msg['To']      = recipient
            msg['Subject'] = f"Delivery Reports - {local_time().strftime('%d.%m. u %H:%M')}"
            body = "Hello,\n\nAttached are the summary and detailed restaurant status reports.\n\nThe system has successfully completed the cycle."
            msg.attach(MIMEText(body, 'plain'))
            for pdf_path in pdf_paths:
                with open(pdf_path, "rb") as attachment:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f"attachment; filename= {os.path.basename(pdf_path)}")
                    msg.attach(part)
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, recipient, msg.as_string())
            server.quit()
        log_msg("[SUCCESS] All emails sent!", log_ph)
    except Exception as e:
        log_msg(f"[ERROR] Email failed: {e}", log_ph)

# ---------------- HISTORY ----------------
def save_to_history(df):
    time_now  = format_time_short()
    date_now  = local_time().strftime("%Y-%m-%d")
    history_data = []
    addresses = df["Address"].unique()
    for adr in addresses:
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Address"] == adr) & (df["Platform"] == plat)]
            if sub.empty: continue
            opened     = len(sub[sub["Status"] == "Open"])
            closed     = len(sub[sub["Status"] == "Closed"])
            avg_time   = sub["Time_Num"].dropna().mean()
            avg_time   = 0 if pd.isna(avg_time) else round(avg_time, 1)
            promo_count = len(sub[sub["Promo"] != "-"])
            history_data.append({
                "Date": date_now, "Time": time_now,
                "Address": adr, "Platform": plat,
                "Open": opened, "Closed": closed,
                "Avg_Time": avg_time, "Promo_Count": promo_count
            })
    df_new = pd.DataFrame(history_data)
    file_str = str(HISTORY_FILE)
    if os.path.exists(file_str):
        df_combined = pd.concat([pd.read_csv(file_str), df_new], ignore_index=True)
    else:
        df_combined = df_new
    try:
        df_combined.to_csv(file_str, index=False)
    except Exception:
        pass
    st.session_state.df_history = df_combined
    return df_combined

# ================= CHARTS =================
def create_status_chart_ui(df_sub, title):
    wolt_o  = len(df_sub[(df_sub["Platform"] == "Wolt")  & (df_sub["Status"] == "Open")])
    wolt_z  = len(df_sub[(df_sub["Platform"] == "Wolt")  & (df_sub["Status"] == "Closed")])
    glovo_o = len(df_sub[(df_sub["Platform"] == "Glovo") & (df_sub["Status"] == "Open")])
    glovo_z = len(df_sub[(df_sub["Platform"] == "Glovo") & (df_sub["Status"] == "Closed")])
    data = [
        {"Category": "Total",  "Platform": "Wolt",  "Count": wolt_o+wolt_z},
        {"Category": "Open",   "Platform": "Wolt",  "Count": wolt_o},
        {"Category": "Closed", "Platform": "Wolt",  "Count": wolt_z},
        {"Category": "Total",  "Platform": "Glovo", "Count": glovo_o+glovo_z},
        {"Category": "Open",   "Platform": "Glovo", "Count": glovo_o},
        {"Category": "Closed", "Platform": "Glovo", "Count": glovo_z},
    ]
    fig = px.bar(data, x="Category", y="Count", color="Platform", barmode="group",
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Count", title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", title_font_size=18)
    fig.update_traces(textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def create_delivery_time_chart_ui(df_sub, title):
    wolt_df  = df_sub[(df_sub["Platform"] == "Wolt")  & (df_sub["Time_Num"].notna())]
    glovo_df = df_sub[(df_sub["Platform"] == "Glovo") & (df_sub["Time_Num"].notna())]
    w_avg = wolt_df["Time_Num"].mean()  if not wolt_df.empty  else 0
    g_avg = glovo_df["Time_Num"].mean() if not glovo_df.empty else 0
    w_avg = 0 if pd.isna(w_avg) else round(w_avg, 1)
    g_avg = 0 if pd.isna(g_avg) else round(g_avg, 1)
    data = [{"Platform": "Wolt", "Time": w_avg}, {"Platform": "Glovo", "Time": g_avg}]
    fig = px.bar(data, x="Platform", y="Time", color="Platform",
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Time", title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis_title="Average time (min)", title_font_size=18)
    fig.update_traces(texttemplate='%{text} min', textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def create_promo_chart_ui(df_sub, selected_promos, title):
    wolt_count = glovo_count = 0
    if selected_promos:
        for _, row in df_sub.iterrows():
            restaurant_promos = []
            if pd.notna(row['Promo']) and row['Promo'] != "-":
                restaurant_promos = [a.replace("• ", "").strip() for a in str(row['Promo']).split('\n') if a.strip()]
            if any(promo in selected_promos for promo in restaurant_promos):
                if row['Platform'] == 'Wolt':  wolt_count  += 1
                elif row['Platform'] == 'Glovo': glovo_count += 1
    data = [{"Platform": "Wolt", "Count": wolt_count}, {"Platform": "Glovo", "Count": glovo_count}]
    fig = px.bar(data, x="Platform", y="Count", color="Platform",
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Count", title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis_title="Number of restaurants", title_font_size=18)
    fig.update_traces(textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def create_timeline_chart_ui(df_hist, address=None, custom_title=None, metric="Open", ylabel="Number of open restaurants"):
    df_sub = df_hist.copy()
    if metric not in df_sub.columns: df_sub[metric] = 0
    if address:
        df_sub = df_sub[df_sub["Address"] == address]
        title  = custom_title if custom_title else f'History - {address.upper()}'
    else:
        if not df_sub.empty and 'Platform' in df_sub.columns:
            if 'Date' in df_sub.columns and 'Time' in df_sub.columns:
                if metric == "Avg_Time":
                    df_sub = df_sub.groupby(["Date", "Time", "Platform"])[metric].mean().reset_index()
                else:
                    df_sub = df_sub.groupby(["Date", "Time", "Platform"])[metric].sum().reset_index()
        title = custom_title if custom_title else 'Summary History'
    if len(df_sub) == 0:
        return go.Figure().update_layout(title="No history data", plot_bgcolor="rgba(0,0,0,0)")
    df_sub["Real_Datetime"] = pd.to_datetime(df_sub["Date"] + " " + df_sub["Time"])
    df_sub = df_sub.sort_values(by="Real_Datetime")
    fig = px.line(df_sub, x="Real_Datetime", y=metric, color="Platform", markers=True,
                  color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      xaxis_title="", yaxis_title=ylabel, hovermode="x unified", title_font_size=18)
    fig.update_xaxes(tickformat="%d.%m. u %H:%M")
    fig.update_traces(line=dict(width=3), marker=dict(size=8))
    return fig

# ---------------- DATA EXTRACTION HELPERS ----------------
def remove_accents(text):
    if not text: return ""
    for k, v in {'č':'c','ć':'c','ž':'z','š':'s','đ':'dj','Č':'C','Ć':'C','Ž':'Z','Š':'S','Đ':'Dj'}.items():
        text = text.replace(k, v)
    return text

def normalize_name(name):
    return re.sub(r'[^\w]', '', str(name).lower())

def extract_name(text):
    if not text: return ""
    for line in str(text).split('\n'):
        line = line.strip()
        if not line or '%' in line or ("min" in line.lower() and re.search(r'\d+', line.lower())):
            continue
        if any(x in line.lower() for x in ["rsd","din","promo","novo","new","odlično","besplatna dostava","artikli","narudžb","popust","off","discount"]):
            continue
        if len(line) >= 2:
            return line
    return ""

def analyze_status(text):
    t = text.lower()
    if any(x in t for x in ["uskoro se zatvara","closing soon","zatvara se za","closes in"]):
        return "Open"
    if any(k in t for k in ["samo preuzimanje","samo za preuzimanje","pickup only","dostava nije dostupna",
                              "dostava trenutno nije","samo licno preuzimanje","zatvoreno","zakažite",
                              "zakaži","zakazi","nedostupno","otvara se","otvara","closed","schedule"]):
        return "Closed"
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
    except Exception:
        pass
    return "-"

def extract_delivery_time(text):
    try:
        clean = re.sub(r'<[^>]+>', ' ', str(text)).lower()
        m1 = re.search(r'(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(?:min|m|\')', clean)
        if m1 and int(m1.group(1)) < 120 and int(m1.group(2)) < 120:
            return f"{m1.group(1)}-{m1.group(2)} min", (int(m1.group(1)) + int(m1.group(2))) / 2.0
        m2 = re.search(r'\b(\d{1,3})\s*(?:min|m|\')', clean)
        if m2 and int(m2.group(1)) < 120:
            return f"{m2.group(1)} min", float(m2.group(1))
    except Exception:
        pass
    return "-", np.nan

def extract_promo(text, html_content, plat):
    clean_text   = (str(text) + " \n " + str(html_content)).lower()
    clean_text   = re.sub(r'<[^>]+>', ' ', clean_text)
    clean_numbers = re.sub(r'(?<=\d)[.,](?=\d)', '', clean_text)
    promos, seen, res = [], set(), []
    if plat == "Glovo" and html_content:
        glovo_tags = re.findall(r'data-style="promotion"[^>]*>([^<]+)<', str(html_content))
        for gp in glovo_tags:
            promos.append(gp.strip())
    if any(x in clean_text for x in ["besplatna dostava","free delivery","dostava 0","0 rsd dostava","delivery 0","besplatna"]):
        promos.append("Free delivery")
    if any(x in clean_text for x in ["1+1","1 + 1","buy 1 get 1"]):
        promos.append("1+1 Free")
    if plat == "Wolt":
        for pm in re.findall(r'(\d{1,3}\s*%)', clean_text):
            promos.append(f"{pm.strip()} discount")
        rsd_patterns = [
            r'(?:rsd|din)\s*(\d{2,5})\s*(?:off|popust|discount)',
            r'(\d{2,5})\s*(?:rsd|din)\s*(?:off|popust|discount)',
            r'(?:uštedi|save|popust)\s*(\d{2,5})\s*(?:rsd|din)'
        ]
        for pat in rsd_patterns:
            for match in re.findall(pat, clean_numbers):
                if int(match) > 10: promos.append(f"{match} RSD discount")
    else:
        for pm in re.findall(r'(\d{1,2}\s*%)\s*(?:popust|off|discount|-)', clean_text):
            promos.append(f"{pm.strip()} discount")
        for rm in re.findall(r'(\d{2,5})\s*(?:rsd|din)', clean_numbers):
            if int(rm) > 10: promos.append(f"{rm} RSD discount")
    if "wolt+" in clean_text: promos.append("Wolt+")
    if "prime" in clean_text:  promos.append("Prime")
    for a in promos:
        ac = a[0].upper() + a[1:]
        if ac not in seen:
            seen.add(ac)
            res.append(f"• {ac}")
    return "\n".join(res) if res else "-"

# ================= WOLT — ČISTI HTTP (iz skripte 2) =================

CITY_SLUG_MAP = {
    # skripta će automatski pokušati slug iz geocodera; ovo je fallback
}

def _parse_dynamic_wolt(data: dict) -> list:
    """Parsira venue dynamic API odgovor i vraća listu akcija."""
    akcije = []
    seen   = set()
    ignore_texts = {
        "prikaži detalje","show details","vidi sve","see all",
        "detalji restorana","restaurant details","more","još",
        "schedule order","naruči","see menu","add {amount} more",
        "try for 30 days for free!","get rsd0 delivery fee & more!",
    }

    def add(text, wolt_plus=False):
        t = (text or "").strip()
        if not t or len(t) <= 3 or t.lower() in ignore_texts:
            return
        prefix = "• [Wolt+] " if wolt_plus else "• "
        key = t.lower()
        if key not in seen:
            seen.add(key)
            akcije.append(f"{prefix}{t}")

    venue_raw = data.get("venue_raw") or {}
    for disc in venue_raw.get("discounts", []):
        if not isinstance(disc, dict): continue
        is_wp  = (disc.get("has_wolt_plus") or
                  (disc.get("banner") or {}).get("show_wolt_plus", False) or
                  (disc.get("conditions") or {}).get("has_wolt_plus") == True)
        banner = disc.get("banner") or {}
        desc   = disc.get("description") or {}
        primary_text = banner.get("formatted_text") or desc.get("title") or ""
        add(primary_text, wolt_plus=is_wp)
        effects = disc.get("effects") or {}

        item_disc = effects.get("item_discount")
        if item_disc and isinstance(item_disc, dict):
            fraction = item_disc.get("fraction")
            if fraction and float(fraction) > 0:
                pct = int(round(float(fraction) * 100))
                add(primary_text or f"{pct}% popust na izabrane artikle", wolt_plus=is_wp)

        basket_disc = effects.get("basket_discount")
        if basket_disc and isinstance(basket_disc, dict):
            amount   = basket_disc.get("amount")
            fraction = basket_disc.get("fraction")
            if amount and int(amount) > 0:
                rsd = int(amount) // 100
                add(primary_text or f"{rsd} RSD popust na korpu", wolt_plus=is_wp)
            elif fraction and float(fraction) > 0:
                pct = int(round(float(fraction) * 100))
                add(primary_text or f"{pct}% popust na celu korpu", wolt_plus=is_wp)

        delivery_disc = effects.get("delivery_discount")
        if delivery_disc and isinstance(delivery_disc, dict):
            amount   = delivery_disc.get("amount")
            fraction = delivery_disc.get("fraction")
            if (amount is not None and int(amount) == 0) or (fraction and float(fraction) >= 1.0):
                add(primary_text or "Besplatna dostava", wolt_plus=is_wp)
            elif amount and int(amount) > 0:
                rsd = int(amount) // 100
                add(primary_text or f"{rsd} RSD popust na dostavu", wolt_plus=is_wp)

        free_items = effects.get("free_items")
        if free_items and isinstance(free_items, (dict, list)):
            add(primary_text or "Gratis artikal uz porudžbinu", wolt_plus=is_wp)

    venue = data.get("venue") or {}
    for ban in venue.get("banners", []):
        if not isinstance(ban, dict): continue
        is_wp = ban.get("show_wolt_plus", False)
        disc  = ban.get("discount") or {}
        add(disc.get("formatted_text"), wolt_plus=is_wp)

    offer_assistant = venue.get("offer_assistant") or {}
    for tracker in offer_assistant.get("offer_trackers", []):
        if not isinstance(tracker, dict): continue
        is_wp = tracker.get("offer_type") == "wolt_plus" or tracker.get("show_wolt_plus", False)
        add(tracker.get("title"), wolt_plus=is_wp)

    return akcije


def _fetch_one_wolt(slug: str, lat: float, lon: float) -> tuple:
    """Dohvata dynamic endpoint za jedan restoran i vraća (slug, promo_str)."""
    ts = _make_thread_session()
    time.sleep(random.uniform(0.8, 2.0))
    url = (
        f"https://consumer-api.wolt.com/order-xp/web/v1/venue/slug/{slug}/dynamic/"
        f"?lat={lat}&lon={lon}&selected_delivery_method=homedelivery"
    )
    data, _ = _fetch_url_http(ts, url, f"DYN {slug}")
    if not data:
        return slug, "-"
    try:
        akcije = _parse_dynamic_wolt(data)
        return slug, ("\n".join(akcije) if akcije else "-")
    except Exception as e:
        _log_fetch(f"DYN {slug} parse EXC: {e}")
        return slug, "-"


def scrape_wolt_http(address: str, log_ph=None, live_ph=None, live_state=None) -> list:
    """
    Čisti HTTP scraper za Wolt (bez Playwright).
    Koristi Nominatim za geocoding, zatim Wolt restaurant-api + consumer-api
    sa ThreadPoolExecutor za paralelno dohvatanje akcija.
    """
    import urllib.request, json as _json

    results_dict = {}
    custom_agent = 'DeliveryMonitorApp/6.0 (wolt_scraper)'

    # ── 1. Geocoding ─────────────────────────────────────────────────────────
    log_msg(f"[WOLT] Geocoding: {address}...", log_ph)
    geo_data = None
    for query in [f"{address}, Serbia", address]:
        try:
            geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query)}&format=json&limit=1&addressdetails=1"
            req = urllib.request.Request(geo_url, headers={'User-Agent': custom_agent})
            with urllib.request.urlopen(req) as resp:
                geo_data = _json.loads(resp.read().decode())
            if geo_data:
                break
            time.sleep(1)
        except Exception as e:
            log_msg(f"[WOLT] Geocoding error: {e}", log_ph)

    if not geo_data:
        log_msg(f"[WOLT ERROR] Cannot find coordinates for: {address}", log_ph)
        return []

    lat      = float(geo_data[0]["lat"])
    lon      = float(geo_data[0]["lon"])
    addr_obj = geo_data[0].get("address", {})
    city_raw = addr_obj.get("city") or addr_obj.get("town") or addr_obj.get("village") or "srb"
    city_slug = normalize_name(cyrillic_to_latin(city_raw))
    log_msg(f"[WOLT] Coordinates: {lat:.4f}, {lon:.4f} (city slug: {city_slug})", log_ph)

    # ── 2. Paginovano učitavanje restorana ────────────────────────────────────
    log_msg(f"[WOLT] Loading restaurant list (paginated)...", log_ph)
    skip      = 0
    page_size = 40
    max_pages = 30

    for page_num in range(max_pages):
        batch_found = 0
        for endpoint in [
            f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}&skip={skip}",
            f"https://restaurant-api.wolt.com/v1/pages/delivery?lat={lat}&lon={lon}&skip={skip}",
        ]:
            data, status = _fetch_url_http(wolt_session, endpoint, f"FEED p{page_num}")
            if not data:
                continue
            for section in data.get("sections", []):
                for item in section.get("items", []):
                    venue = item.get("venue")
                    if not venue: continue
                    name = venue.get("name", "")
                    slug = venue.get("slug", "")
                    if not name or not slug or slug in results_dict:
                        continue

                    batch_found += 1
                    online   = venue.get("online", False)
                    r_score  = (venue.get("rating") or {}).get("score", "-")
                    est      = venue.get("estimate_range") or venue.get("estimate")
                    time_str = f"{est} min" if est else "-"
                    try:
                        parts    = str(est).split('-') if est else []
                        time_num = (int(parts[0]) + int(parts[1])) / 2.0 if len(parts) == 2 else (float(est) if est else np.nan)
                    except Exception:
                        time_num = np.nan

                    # Wolt+ / badge promo iz feed-a
                    feed_promos = []
                    for badge in venue.get("badges", []):
                        txt = badge.get("text", "")
                        if txt and txt.lower() not in ["novo","new"]:
                            feed_promos.append(f"• {txt}")
                    label = venue.get("label", "")
                    if label and label.lower() not in ["novo","new"]:
                        feed_promos.append(f"• {label}")

                    is_new = any(
                        (badge.get("text","").lower() in ["novo","new"]) for badge in venue.get("badges",[])
                    ) or (label.lower() in ["novo","new"])

                    link_target = item.get("link", {}).get("target", "")
                    if link_target.startswith("http"):
                        link = link_target
                    elif link_target.startswith("/"):
                        link = f"https://wolt.com{link_target}"
                    else:
                        link = f"https://wolt.com/sr/srb/{city_slug}/restaurant/{slug}"

                    results_dict[slug] = {
                        "Address": address, "Platform": "Wolt",
                        "Name": remove_accents(name),
                        "Rating": str(r_score) if r_score != "-" else "-",
                        "Delivery Time": time_str,
                        "Promo": "\n".join(feed_promos) if feed_promos else "-",
                        "Status": "Open" if online else "Closed",
                        "Time_Num": time_num, "Is_New": is_new, "Link": link,
                        "_feed_promos": feed_promos,
                    }
            if batch_found > 0:
                break  # uspeli smo sa prvim endpointom

        log_msg(f"[WOLT] Page {page_num+1}: total {len(results_dict)} restaurants", log_ph)

        if live_ph and live_state is not None:
            live_state["Wolt"] = len(results_dict)
            refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)

        if batch_found < 5:
            break  # poslednja stranica
        skip += page_size
        time.sleep(random.uniform(0.4, 1.0))

    if not results_dict:
        log_msg(f"[WOLT ERROR] No restaurants found for: {address}", log_ph)
        return []

    log_msg(f"[WOLT] Found {len(results_dict)} restaurants. Fetching promotions ({WOLT_FETCH_WORKERS} workers)...", log_ph)
    if live_ph and live_state is not None:
        refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address,
                        custom_text=f"📍 Wolt: {len(results_dict)} restaurants found. Fetching promotions...")

    # ── 3. Paralelno dohvatanje akcija ────────────────────────────────────────
    slugs     = list(results_dict.keys())
    completed = 0
    total     = len(slugs)

    with ThreadPoolExecutor(max_workers=WOLT_FETCH_WORKERS) as executor:
        futures = {executor.submit(_fetch_one_wolt, slug, lat, lon): slug for slug in slugs}
        for future in as_completed(futures):
            try:
                slug, promo_str = future.result()
                feed_promos = results_dict[slug].pop("_feed_promos", [])
                if promo_str != "-":
                    # Merge: API promo ima prioritet, feed_promos su dopuna
                    api_lines  = [l for l in promo_str.split("\n") if l.strip()]
                    feed_lines = [l for l in feed_promos if l.strip()]
                    all_lines  = list(dict.fromkeys(api_lines + feed_lines))
                    results_dict[slug]["Promo"] = "\n".join(all_lines)
                elif feed_promos:
                    results_dict[slug]["Promo"] = "\n".join(feed_promos)
                # else: ostaje "-"
            except Exception as ex:
                _log_fetch(f"future EXC: {ex}")
            completed += 1
            if completed % 20 == 0 or completed == total:
                log_msg(f"[WOLT] Promotions: {completed}/{total}", log_ph)

    # Ukloni interni ključ ako je ostao
    for v in results_dict.values():
        v.pop("_feed_promos", None)

    log_msg(f"[WOLT] Done. {len(results_dict)} restaurants scraped.", log_ph)
    if live_ph and live_state is not None:
        live_state["Wolt"] = len(results_dict)
        refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)

    return list(results_dict.values())


# ================= GLOVO — PLAYWRIGHT (popravljen popup) =================

TINY_PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

async def smart_diet_mode(route):
    if route.request.resource_type in ["image", "media"]:
        await route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
    else:
        await route.continue_()

async def smart_scroll_and_extract(page, plat, address, log_ph=None, live_ph=None, live_state=None):
    results_dict = {}
    prev_count   = 0
    attempts_at_bottom = 0
    while True:
        data = await page.evaluate('''() => {
            let rez = [];
            document.querySelectorAll("a:has(h3), a[data-testid='store-card'], .store-card a").forEach(c => {
                let link = c.href;
                if (!link.includes('/dostava') && !link.includes('/category')) {
                    rez.push({link: link, text: c.innerText, html: c.innerHTML});
                }
            });
            return rez;
        }''')
        for item in data:
            link = item['link']
            if not link or link in results_dict: continue
            text         = item['text']
            html_content = item.get('html', '')
            all_text     = text + " " + html_content
            name         = remove_accents(extract_name(text))
            if len(name) < 2: continue
            rating        = extract_rating(all_text, plat)
            time_str, time_num = extract_delivery_time(all_text)
            promo_str     = extract_promo(text, html_content, plat)
            t_low         = text.strip().lower()
            is_new        = (t_low.endswith('new') or t_low.endswith('novo') or
                             bool(re.search(r'•.*?new\b', t_low)))
            results_dict[link] = {
                "Address": address, "Platform": plat, "Name": name, "Rating": rating,
                "Delivery Time": time_str, "Promo": promo_str,
                "Status": analyze_status(all_text),
                "Time_Num": time_num, "Is_New": is_new, "Link": link
            }
        current = len(results_dict)
        if current > prev_count:
            log_msg(f"[{plat.upper()} - {address}] Loaded {current} restaurants...", log_ph)
            if live_ph and live_state is not None:
                live_state[plat] = current
                refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)
            prev_count = current
            attempts_at_bottom = 0
        await page.evaluate("window.scrollBy(0, 500);")
        await asyncio.sleep(0.5)
        h = await page.evaluate("document.body.scrollHeight")
        s = await page.evaluate("window.scrollY + window.innerHeight")
        if s >= h - 50:
            attempts_at_bottom += 1
            await asyncio.sleep(1.5)
            if attempts_at_bottom >= 5:
                break
    return list(results_dict.values())


async def _handle_glovo_address_popups(page, log_ph=None):
    """
    Obrađuje SVE moguće popupove koji se pojavljuju pri unosu adrese na Glovu:
      1. Cookie/GDPR (Accept All)
      2. Tip adrese (Kuća / Posao / Drugo)  ← NOVI FIX
      3. Potvrda adrese ('Potvrdi adresu')
      4. 'Idi na početnu stranicu' dugme
    Vraća True ako je sve prošlo OK, False ako je nešto fatalno pošlo naopako.
    """
    # 1. Cookie popup
    try:
        accept_btn = page.locator("button", has_text=re.compile(r"Accept All|Prihvati sve", re.IGNORECASE)).first
        await accept_btn.wait_for(state="visible", timeout=4000)
        await accept_btn.click()
        await asyncio.sleep(1.5)
        log_msg("[GLOVO] Cookie popup prihvaćen.", log_ph)
    except Exception:
        pass

    # 2. Popup za tip adrese: Kuća / Posao / Drugo
    #    Glovo prikazuje ovaj modal kada unese adresu bez tipa.
    #    Klikamo "Drugo" (ili "Other") jer je najsigurniji izbor.
    for attempt in range(3):
        try:
            btn_drugo = page.locator("button", has_text=re.compile(r"^Drugo$|^Other$|^Home$|^Kuća$", re.IGNORECASE)).first
            await btn_drugo.wait_for(state="visible", timeout=5000)
            await btn_drugo.click()
            await asyncio.sleep(1.5)
            log_msg("[GLOVO] Tip adrese popup kliknut ('Drugo').", log_ph)
            break
        except Exception:
            # Probamo direktnim CSS selektorom koji Glovo koristi za ove dugmiće
            try:
                btn_css = page.locator("button[data-testid='address-type-button']").first
                if await btn_css.count() > 0:
                    await btn_css.click()
                    await asyncio.sleep(1.5)
                    log_msg("[GLOVO] Tip adrese popup kliknut (CSS selector).", log_ph)
                    break
            except Exception:
                pass
        await asyncio.sleep(1)

    # 3. Potvrdi adresu
    for attempt in range(3):
        try:
            btn_potvrdi = page.locator("button", has_text=re.compile(r"Potvrdi adresu|Confirm address|Continue", re.IGNORECASE)).first
            await btn_potvrdi.wait_for(state="visible", timeout=6000)
            await btn_potvrdi.click(force=True)
            await asyncio.sleep(2)
            log_msg("[GLOVO] Adresa potvrđena.", log_ph)
            break
        except Exception:
            await asyncio.sleep(1)

    # 4. 'Idi na početnu stranicu' — pojavljuje se ponekad posle potvrde
    try:
        btn_pocetna = page.locator("text='Idi na početnu stranicu'")
        if await btn_pocetna.count() > 0 and await btn_pocetna.first.is_visible(timeout=3000):
            await btn_pocetna.first.click()
            await asyncio.sleep(4)
            log_msg("[GLOVO] Kliknut 'Idi na početnu stranicu'.", log_ph)
    except Exception:
        pass

    return True


async def scrape_glovo(context_glovo, address, log_ph=None, live_ph=None, live_state=None, error_screenshots=None, debug_mode=False):
    page = None
    try:
        page = await context_glovo.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.set_default_timeout(15000)

        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Provjera soft-ban
        stranica_tekst = await page.content()
        if "Oh, no!" in stranica_tekst or "It looks like there's a problem" in stranica_tekst:
            log_msg(f"[GLOVO BLOCKED] {address}.", log_ph)
            if error_screenshots is not None and debug_mode:
                try:
                    err_path = str(ERRORS_DIR / f"Glovo_SoftBan_{remove_accents(address).replace(' ','_')}_{timestamp()}.png")
                    await page.screenshot(path=err_path)
                    error_screenshots.append(err_path)
                except Exception:
                    pass
            return []

        # ── Unos adrese ─────────────────────────────────────────────────────
        address_entered = False
        # Pokušaj 1: hero input (početna stranica)
        try:
            hero_input = page.locator("#hero-container-input")
            await hero_input.wait_for(state="visible", timeout=5000)
            await hero_input.click()
            search = page.get_by_role("searchbox")
            await search.fill(address)
            await asyncio.sleep(1.5)
            dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
            await dropdown_item.wait_for(state="visible", timeout=8000)
            await dropdown_item.click()
            address_entered = True
            log_msg(f"[GLOVO] Address entered via hero input: {address}", log_ph)
        except PlaywrightTimeoutError:
            pass

        # Pokušaj 2: header dugme za promenu adrese
        if not address_entered:
            log_msg(f"[GLOVO] Trying header address change for: {address}", log_ph)
            try:
                header_btn = page.locator('header div[role="button"]').first
                await header_btn.wait_for(state="visible", timeout=6000)
                await header_btn.click()
                await asyncio.sleep(1.5)
                search_modal = page.get_by_role("searchbox").last
                await search_modal.wait_for(state="visible", timeout=6000)
                await search_modal.click()
                await search_modal.fill(address)
                await asyncio.sleep(2.5)
                dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
                await dropdown_item.wait_for(state="visible", timeout=8000)
                await dropdown_item.click()
                address_entered = True
                log_msg(f"[GLOVO] Address entered via header: {address}", log_ph)
            except PlaywrightTimeoutError:
                log_msg(f"[GLOVO ABORT] Cannot enter address for {address}.", log_ph)
                if error_screenshots is not None and debug_mode:
                    try:
                        err_path = str(ERRORS_DIR / f"Glovo_Nav_Error_{remove_accents(address).replace(' ','_')}_{timestamp()}.png")
                        await page.screenshot(path=err_path)
                        error_screenshots.append(err_path)
                    except Exception:
                        pass
                return []

        await asyncio.sleep(2)

        # ── Obrada SVIH popupova (uključujući Kuća/Posao/Drugo) ─────────────
        await _handle_glovo_address_popups(page, log_ph)

        await asyncio.sleep(5)

        # ── Navigacija do restorana ──────────────────────────────────────────
        try:
            kat_link = page.get_by_role("link", name=re.compile(r"Restorani|Hrana|Food|Restaurants", re.I)).first
            await kat_link.wait_for(state="visible", timeout=6000)
            await kat_link.click()
        except PlaywrightTimeoutError:
            pass

        try:
            await page.wait_for_selector("a[data-testid='store-card'], .store-card a", timeout=15000)
        except PlaywrightTimeoutError:
            log_msg(f"[GLOVO WARNING] Restaurants not loaded in time for {address}.", log_ph)

        page.set_default_timeout(60000)
        rez = await smart_scroll_and_extract(page, "Glovo", address, log_ph, live_ph, live_state)

        if len(rez) < 5 and debug_mode and error_screenshots is not None:
            err_path = str(ERRORS_DIR / f"Glovo_Warning_{remove_accents(address).replace(' ','_')}_{timestamp()}.png")
            try:
                await page.screenshot(path=err_path)
                error_screenshots.append(err_path)
            except Exception:
                pass

        return rez

    except Exception as e:
        log_msg(f"[GLOVO ERROR] {e}", log_ph)
        if page and error_screenshots is not None and debug_mode:
            try:
                err_path = str(ERRORS_DIR / f"Glovo_Error_{remove_accents(address).replace(' ','_')}_{timestamp()}.png")
                await page.screenshot(path=err_path)
                error_screenshots.append(err_path)
            except Exception:
                pass
        return []
    finally:
        if page:
            await page.close()


# ================= MAIN SCAN PROCESS =================

async def scan_process(addresses, log_ph, live_ph, live_state, generate_pdf=False, recipient_email="", debug_mode=False):
    all_data        = []
    error_screenshots = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox"
            ]
        )

        ga = {
            "permissions": ['geolocation'],
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9,sr;q=0.8"}
        }
        if debug_mode:
            ga["record_video_dir"]  = str(ERRORS_DIR)
            ga["record_video_size"] = {"width": 1280, "height": 720}
        if os.path.exists(GLOVO_AUTH_FILE):
            log_msg("🔐 GLOVO: Loaded VIP pass.", log_ph)
            ga["storage_state"] = GLOVO_AUTH_FILE

        for i, adr in enumerate(addresses):
            live_state["Wolt"]  = 0
            live_state["Glovo"] = 0
            refresh_live_ui(live_ph, 0, 0, adr)

            if i > 0:
                log_msg("⏳ Pausing for 5 seconds between addresses...", log_ph)
                await asyncio.sleep(5)

            log_msg(f"\n[SYSTEM] Starting scan for: {adr}", log_ph)

            # ── Glovo (Playwright) ───────────────────────────────────────────
            log_msg("📱 Scrolling GLOVO...", log_ph)
            context_glovo = await browser.new_context(**ga)
            await context_glovo.route("**/*", smart_diet_mode)
            r_glovo = await scrape_glovo(context_glovo, adr, log_ph, live_ph, live_state, error_screenshots, debug_mode)
            all_data.extend(r_glovo)
            await context_glovo.close()

            # ── Wolt (čisti HTTP, bez Playwright) ───────────────────────────
            log_msg("🚲 Calling WOLT API (HTTP)...", log_ph)
            # Wolt scraper je sinhroni — pokretamo u asyncio executor da ne blokira event loop
            loop    = asyncio.get_event_loop()
            r_wolt  = await loop.run_in_executor(
                None,
                lambda a=adr: scrape_wolt_http(a, log_ph, live_ph, live_state)
            )
            all_data.extend(r_wolt)

        await browser.close()

    if all_data:
        df_s  = pd.DataFrame(all_data)
        df_h  = save_to_history(df_s)

        pdf_files = []
        if generate_pdf:
            log_msg("Generating PDF reports...", log_ph)
            try:
                zbirni = napravi_zbirni_pdf(df_s, df_h)
                if zbirni: pdf_files.append(zbirni)
                for adr in df_s["Address"].unique():
                    df_sub = df_s[df_s["Address"] == adr]
                    p_file = napravi_pdf_za_adresu(df_sub, adr, df_h)
                    if p_file: pdf_files.append(p_file)
            except NameError:
                log_msg("[WARNING] PDF functions are missing from the code context.", log_ph)

            if recipient_email.strip() and pdf_files:
                log_msg(f"Sending reports to: {recipient_email}", log_ph)
                send_email(pdf_files, recipient_email, log_ph)
        else:
            log_msg("Scan complete. PDF option is disabled.", log_ph)

        return df_s, df_h, pdf_files, error_screenshots

    return pd.DataFrame(), pd.DataFrame(), [], error_screenshots


# ================= STREAMLIT UI =================
if 'is_running'          not in st.session_state: st.session_state.is_running          = False
if 'last_run'            not in st.session_state: st.session_state.last_run            = 0
if 'df_all'              not in st.session_state: st.session_state.df_all              = pd.DataFrame()
if 'pdf_files'           not in st.session_state: st.session_state.pdf_files           = []
if 'error_screenshots'   not in st.session_state: st.session_state.error_screenshots   = []
if 'loaded_history'      not in st.session_state: st.session_state.loaded_history      = False

if 'df_history' not in st.session_state:
    if os.path.exists(HISTORY_FILE):
        st.session_state.df_history = pd.read_csv(HISTORY_FILE)
    else:
        st.session_state.df_history = pd.DataFrame()

st.title("🍔 Delivery Monitor (Wolt & Glovo)")

with st.sidebar:
    st.header("⚙️ Settings")
    address_1 = st.text_input("📍 Address 1 (Required):", value="", placeholder="Makenzijeva 57, Belgrade")
    address_2 = st.text_input("📍 Address 2 (Optional):", value="", placeholder="Somborska 5, Niš")
    auto_refresh   = st.checkbox("🔄 Auto-refresh", value=False)
    sleep_interval = st.number_input("⏱️ Interval (min):", min_value=1, value=60, disabled=not auto_refresh)
    generate_pdf   = st.checkbox("📄 Generate PDF reports", value=False)
    email_input    = st.text_input("📧 Send to email:", placeholder="your@email.com") if generate_pdf else ""
    st.markdown("---")
    debug_mode = st.checkbox("🛠️ Enable Debug Mode (Video/HTML Logs)", value=False)
    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ START", type="primary", use_container_width=True):
            st.session_state.is_running   = True
            st.session_state.loaded_history = False
            st.session_state.last_run     = 0
            st.rerun()
    with c2:
        if st.button("⏹️ STOP", use_container_width=True):
            st.session_state.is_running = False
            st.rerun()

    st.markdown("---")
    st.header("📂 Scan Archive")
    history_files = sorted(list(OUTPUT_DIR.glob("Detaljno_*.csv")), reverse=True)
    options = {"--- Choose old report ---": None}
    for f in history_files:
        name = f.stem.replace("Detaljno_", "")
        try:
            options[datetime.datetime.strptime(name, "%Y%m%d_%H%M%S").strftime("%d.%m.%Y u %H:%M:%S")] = f
        except Exception:
            options[name] = f
    selected_file = st.selectbox("Previous scans:", list(options.keys()), label_visibility="collapsed")
    col_load, col_del = st.columns(2)
    with col_load:
        if st.button("📂 Load", use_container_width=True) and options[selected_file]:
            st.session_state.df_all      = pd.read_csv(options[selected_file])
            st.session_state.is_running  = False
            st.session_state.loaded_history = True
            st.session_state.last_run    = os.path.getmtime(options[selected_file])
            st.rerun()
    with col_del:
        if st.button("🗑️ Delete", type="secondary", use_container_width=True) and options[selected_file]:
            os.remove(options[selected_file])
            if st.session_state.loaded_history:
                st.session_state.df_all      = pd.DataFrame()
                st.session_state.loaded_history = False
            st.rerun()

    st.markdown("---")
    st.header("⚠️ System Reset")
    with st.expander("Danger Zone (Delete Everything)"):
        st.warning("This deletes ALL old reports and history!")
        reset_pass = st.text_input("Password:", type="password", key="reset_pass")
        if st.button("🚨 DELETE ALL", use_container_width=True):
            if reset_pass == "zekapeka":
                if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
                st.session_state.df_history = pd.DataFrame()
                for f in OUTPUT_DIR.glob("Detaljno_*.csv"):
                    try: os.remove(f)
                    except Exception: pass
                for f in OUTPUT_DIR.glob("*.pdf"):
                    try: os.remove(f)
                    except Exception: pass
                st.session_state.df_all      = pd.DataFrame()
                st.session_state.loaded_history = False
                st.session_state.is_running  = False
                st.success("✅ System successfully reset!")
                time.sleep(1.5)
                st.rerun()
            else:
                st.error("❌ Incorrect password!")

# ================= MAIN INTERFACE =================
if st.session_state.is_running or st.session_state.loaded_history:

    if st.session_state.is_running:
        list_addresses = [cyrillic_to_latin(a.strip()) for a in [address_1, address_2] if a.strip()]
        if not list_addresses:
            st.warning("⚠️ Enter at least the first address to scan!")
            st.session_state.is_running = False
            st.rerun()

        now = time.time()
        if now - st.session_state.last_run >= sleep_interval * 60 or st.session_state.last_run == 0:
            with st.spinner('🔄 Script is searching for restaurants, please wait...'):
                live_ui_ph = st.empty()
                sl         = st.empty()
                live_state = {"Wolt": 0, "Glovo": 0}
                df, hi, pdf, err_imgs = asyncio.run(
                    scan_process(list_addresses, sl, live_ui_ph, live_state, generate_pdf, email_input, debug_mode)
                )
                if not df.empty:
                    df.to_csv(OUTPUT_DIR / f"Detaljno_{timestamp()}.csv", index=False)
                live_ui_ph.empty()
                st.session_state.df_all          = df
                st.session_state.df_history      = hi
                st.session_state.pdf_files       = pdf
                st.session_state.error_screenshots = err_imgs
                st.session_state.last_run        = time.time()
                sl.empty()
            st.rerun()

    df = st.session_state.df_all
    if not df.empty:
        for col in ["Time_Num", "Delivery Time", "Rating", "Is_New"]:
            if col not in df.columns:
                df[col] = False if col == "Is_New" else (np.nan if "Num" in col else "-")

        if st.session_state.loaded_history:
            st.info("📂 **Viewing archived report.**")
        else:
            st.success(f"✅ Scan completed at: {datetime.datetime.fromtimestamp(st.session_state.last_run, LOCAL_TZ).strftime('%H:%M:%S')}")

        tab_dash, tab_list, tab_compare, tab_promo = st.tabs([
            "📊 Dashboard", "🔍 Restaurant List", "⚖️ Comparison", "🎁 Promos & Discounts"
        ])

        unique_addresses = list(df["Address"].unique())

        with tab_dash:
            for adr in unique_addresses:
                st.markdown(f"<h3 style='color: #2c3e50;'>📍 {adr.upper()}</h3>", unsafe_allow_html=True)
                sd      = df[df["Address"] == adr]
                w_total = len(sd[sd["Platform"] == "Wolt"])
                w_open  = len(sd[(sd["Platform"] == "Wolt")  & (sd["Status"] == "Open")])
                g_total = len(sd[sd["Platform"] == "Glovo"])
                g_open  = len(sd[(sd["Platform"] == "Glovo") & (sd["Status"] == "Open")])
                html_kpi = f"""
                <div class="kpi-wrapper">
                    <div class="kpi-card kpi-wolt">
                        <div class="kpi-title">Wolt Total</div>
                        <div class="kpi-value">{w_total}</div>
                    </div>
                    <div class="kpi-card kpi-wolt">
                        <div class="kpi-title">Wolt Open</div>
                        <div class="kpi-value" style="color: #27ae60;">{w_open}</div>
                    </div>
                    <div class="kpi-card kpi-glovo">
                        <div class="kpi-title">Glovo Total</div>
                        <div class="kpi-value">{g_total}</div>
                    </div>
                    <div class="kpi-card kpi-glovo">
                        <div class="kpi-title">Glovo Open</div>
                        <div class="kpi-value" style="color: #27ae60;">{g_open}</div>
                    </div>
                </div>
                """
                st.markdown(html_kpi, unsafe_allow_html=True)

            st.markdown("---")
            chart_addr = st.selectbox("📍 Filter Charts:", ["All addresses"] + unique_addresses,
                                      index=1 if len(unique_addresses) == 1 else 0)
            c_df = df if chart_addr == "All addresses" else df[df["Address"] == chart_addr]
            ca, cb = st.columns(2)
            with ca: st.plotly_chart(create_status_chart_ui(c_df, "Status Comparison"), use_container_width=True)
            with cb: st.plotly_chart(create_delivery_time_chart_ui(c_df, "Average Delivery Time"), use_container_width=True)

            st.markdown("---")
            st.markdown("##### 📅 Activity History")
            hist_df = st.session_state.df_history.copy()
            if not hist_df.empty:
                c_h = hist_df if chart_addr == "All addresses" else hist_df[hist_df["Address"] == chart_addr]
                if not c_h.empty and 'Date' in c_h.columns and 'Time' in c_h.columns:
                    c_h['Datetime'] = pd.to_datetime(c_h['Date'] + ' ' + c_h['Time'])
                    min_d = c_h['Datetime'].min().date()
                    max_d = c_h['Datetime'].max().date()
                    c_dt1, c_dt2, c_dt3, c_dt4 = st.columns(4)
                    with c_dt1: start_date = st.date_input("From date:", min_d, min_value=min_d, max_value=max_d)
                    with c_dt2: start_time = st.time_input("From time:", datetime.time(0, 0))
                    with c_dt3: end_date   = st.date_input("To date:", max_d, min_value=min_d, max_value=max_d)
                    with c_dt4: end_time   = st.time_input("To time:", datetime.time(23, 59))
                    start_dt   = pd.to_datetime(datetime.datetime.combine(start_date, start_time))
                    end_dt     = pd.to_datetime(datetime.datetime.combine(end_date, end_time))
                    mask       = (c_h['Datetime'] >= start_dt) & (c_h['Datetime'] <= end_dt)
                    chart_hist = c_h.loc[mask].copy()
                    st.plotly_chart(create_timeline_chart_ui(chart_hist, None, "History: Open restaurants", metric="Open", ylabel="Open restaurants"), use_container_width=True)
                    ch1, ch2 = st.columns(2)
                    with ch1: st.plotly_chart(create_timeline_chart_ui(chart_hist, None, "History: Avg delivery time", metric="Avg_Time", ylabel="Time (min)"), use_container_width=True)
                    with ch2: st.plotly_chart(create_timeline_chart_ui(chart_hist, None, "History: Restaurants on promo", metric="Promo_Count", ylabel="Promo count"), use_container_width=True)
                else:
                    st.info("No history for selected address.")
            else:
                st.info("No historical data.")

        with tab_list:
            f1, f2, f3 = st.columns(3)
            with f1: fa = st.multiselect("📍 Address",  df["Address"].unique(),  df["Address"].unique())
            with f2: fp = st.multiselect("📱 Platform", df["Platform"].unique(), df["Platform"].unique())
            with f3: fs = st.multiselect("🚦 Status",   ["Open", "Closed"],      ["Open", "Closed"])
            c_filt1, c_filt2 = st.columns(2)
            with c_filt1: filt_new   = st.checkbox("✨ Show only NEW restaurants")
            with c_filt2: filt_promo = st.checkbox("🔥 Show only restaurants ON PROMO")
            f_df = df[(df["Address"].isin(fa)) & (df["Platform"].isin(fp)) & (df["Status"].isin(fs))]
            if filt_new:   f_df = f_df[f_df["Is_New"].isin([True, 'True', 'true', 1])]
            if filt_promo: f_df = f_df[f_df["Promo"] != "-"]
            disp_df         = f_df.copy()
            disp_df["Badge"] = disp_df["Is_New"].apply(lambda x: "✨ NEW" if x in [True, 'True', 'true', 1] else "")
            disp_df = disp_df.drop(columns=['Naziv_Norm', 'Time_Num', 'Is_New'], errors='ignore')
            cols    = ["Address", "Platform", "Name", "Status", "Rating", "Delivery Time", "Promo", "Badge", "Link"]
            disp_df = disp_df[[c for c in cols if c in disp_df.columns]]

            def style_rows(row):
                styles = [''] * len(row)
                styles[row.index.get_loc('Status')] = 'color: #27ae60; font-weight: bold;' if row['Status'] == 'Open' else 'color: #e74c3c; font-weight: bold;'
                if row['Promo'] != '-': styles[row.index.get_loc('Promo')] = 'color: #8e44ad; font-weight: bold;'
                return styles

            st.dataframe(
                disp_df.style.apply(style_rows, axis=1),
                use_container_width=True, hide_index=True, height=800,
                column_config={
                    "Link":  st.column_config.LinkColumn("Link", display_text="Open on site"),
                    "Promo": st.column_config.TextColumn("Promo", width="large")
                }
            )

        with tab_compare:
            c_up1, c_up2 = st.columns(2)
            with c_up1: filter_wolt_up  = st.multiselect("🚦 Wolt filter:",  ["Open", "Closed"], default=["Open", "Closed"], key="fw")
            with c_up2: filter_glovo_up = st.multiselect("🚦 Glovo filter:", ["Open", "Closed"], default=["Open", "Closed"], key="fg")
            df['Name_Norm'] = df['Name'].apply(normalize_name)
            compare_data = []
            for adr in unique_addresses:
                df_adr  = df[df['Address'] == adr]
                common  = set(df_adr[df_adr['Platform'] == 'Wolt']['Name_Norm']).intersection(
                              set(df_adr[df_adr['Platform'] == 'Glovo']['Name_Norm']))
                for norm_name in common:
                    w_row = df_adr[(df_adr['Platform'] == 'Wolt')  & (df_adr['Name_Norm'] == norm_name)].iloc[0]
                    g_row = df_adr[(df_adr['Platform'] == 'Glovo') & (df_adr['Name_Norm'] == norm_name)].iloc[0]
                    compare_data.append({
                        "Address": adr,
                        "Name (Wolt)": w_row['Name'],  "Status Wolt": w_row['Status'],  "Time Wolt": w_row['Delivery Time'],  "Link Wolt": w_row['Link'],
                        "Name (Glovo)": g_row['Name'], "Status Glovo": g_row['Status'], "Time Glovo": g_row['Delivery Time'], "Link Glovo": g_row['Link']
                    })
            if compare_data:
                df_compare = pd.DataFrame(compare_data)
                df_compare = df_compare[(df_compare['Status Wolt'].isin(filter_wolt_up)) & (df_compare['Status Glovo'].isin(filter_glovo_up))]
                if not df_compare.empty:
                    st.dataframe(
                        df_compare.style.map(
                            lambda val: f'color: {"#27ae60" if val=="Open" else "#e74c3c"}; font-weight: bold;',
                            subset=['Status Wolt', 'Status Glovo']
                        ),
                        use_container_width=True, hide_index=True, height=800,
                        column_config={
                            "Link Wolt":  st.column_config.LinkColumn("Link Wolt",  display_text="Open Wolt"),
                            "Link Glovo": st.column_config.LinkColumn("Link Glovo", display_text="Open Glovo")
                        }
                    )
                else:
                    st.info("No restaurants match these filters.")
            else:
                st.info("No common restaurants found on both platforms.")

        with tab_promo:
            unique_promos = set()
            for promo_str in c_df['Promo']:
                if pd.notna(promo_str) and str(promo_str) != "-":
                    for a in str(promo_str).split('\n'):
                        cl = a.replace("• ", "").strip()
                        if cl: unique_promos.add(cl)
            unique_promos   = sorted(list(unique_promos))
            selected_promos = st.multiselect("Select promos to show on chart:", unique_promos, default=unique_promos)
            st.plotly_chart(create_promo_chart_ui(c_df, selected_promos, "Number of restaurants with selected promos"), use_container_width=True)

        if st.session_state.get('pdf_files'):
            st.markdown("---")
            st.subheader("📥 PDF Reports")
            pc = st.columns(4)
            for i, p in enumerate(st.session_state.pdf_files):
                with pc[i % 4]:
                    with open(p, "rb") as f:
                        st.download_button(f"Download {os.path.basename(p)}", f.read(), os.path.basename(p), "application/pdf")

        if st.session_state.get('error_screenshots'):
            st.markdown("---")
            st.error("⚠️ ATTENTION: The script logged potential issues or debug files. Check them below:")
            for media_path in st.session_state.error_screenshots:
                if media_path.endswith('.webm'):
                    st.video(media_path)
                elif media_path.endswith('.html'):
                    try:
                        with open(media_path, "rb") as f:
                            st.download_button(
                                label=f"📥 Download HTML ({os.path.basename(media_path)})",
                                data=f, file_name=os.path.basename(media_path),
                                mime="text/html", key=media_path
                            )
                    except Exception:
                        pass
                else:
                    st.image(media_path, caption=os.path.basename(media_path), use_container_width=True)

    if st.session_state.is_running:
        if auto_refresh:
            rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
            countdown_ph = st.sidebar.empty()
            while rem > 0:
                countdown_ph.info(f"⏳ Next auto-scan in: **{rem//60:02d}:{rem%60:02d}**")
                time.sleep(1)
                rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
                st.rerun()
        else:
            st.sidebar.success("✅ Scan completed. Click 'Start' for a new scan.")

else:
    st.info("System is ready. Enter parameters in the left menu and click 'Start'.")
