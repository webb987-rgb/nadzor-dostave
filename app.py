import asyncio
import datetime
import os
import re
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import random
import urllib.parse
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

st.markdown("""
<style>
    .live-card { display: flex; gap: 20px; background: #f8f9fa; padding: 15px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    .wolt-card { flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #00c2e8; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .glovo-card { flex: 1; text-align: center; background: white; padding: 15px; border-radius: 10px; border-left: 6px solid #ffc244; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    .metric-value { font-size: 32px; font-weight: bold; margin: 0; }
    .metric-title { font-size: 14px; color: #666; margin: 0; text-transform: uppercase; letter-spacing: 1px; }
    .kpi-wrapper { display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }
    .kpi-card { flex: 1; background: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.04); border: 1px solid #f0f2f6; text-align: center; }
    .kpi-title { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; font-weight: 700; }
    .kpi-value { font-size: 36px; font-weight: 800; color: #2c3e50; margin: 0; line-height: 1.1; }
    .kpi-wolt { border-bottom: 4px solid #00c2e8; }
    .kpi-glovo { border-bottom: 4px solid #ffc244; }
    .stTabs [data-baseweb="tab"] { height: 50px; background-color: #f0f2f6; border-radius: 8px 8px 0px 0px; padding: 10px 20px; color: #4f4f4f; }
    .stTabs [aria-selected="true"] { background-color: #e0e5ec !important; font-weight: bold; border-bottom: 3px solid #ff4b4b; }
</style>
""", unsafe_allow_html=True)

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

def timestamp(): return local_time().strftime("%Y%m%d_%H%M%S")
def format_time_short(): return local_time().strftime("%H:%M")
def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder: placeholder.text(msg)

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

def cyrillic_to_latin(text):
    if not text: return ""
    mapa = {'а':'a','б':'b','в':'v','г':'g','д':'d','ђ':'dj','е':'e','ж':'z','з':'z','и':'i','ј':'j','к':'k','л':'l','љ':'lj','м':'m','н':'n','њ':'nj','о':'o','п':'p','р':'r','с':'s','т':'t','ћ':'c','у':'u','ф':'f','х':'h','ц':'c','ч':'c','џ':'dz','ш':'s','А':'A','Б':'B','В':'V','Г':'G','Д':'D','Ђ':'Dj','Е':'E','Ж':'Z','З':'Z','И':'I','Ј':'J','К':'K','Л':'L','Љ':'Lj','М':'M','Н':'N','Њ':'Nj','О':'O','П':'P','Р':'R','С':'S','Т':'T','Ћ':'C','У':'U','Ф':'F','Х':'H','Ц':'C','Ч':'C','Џ':'Dz','Ш':'S'}
    for k, v in mapa.items(): text = text.replace(k, v)
    return text

def send_email(pdf_paths, recipients_str, log_ph=None):
    recipients_list = [e.strip() for e in recipients_str.split(",") if e.strip()]
    if not recipients_list: return
    try:
        log_msg(f"[SYSTEM] Sending email to: {', '.join(recipients_list)}...", log_ph)
        for recipient in recipients_list:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_SENDER
            msg['To'] = recipient
            msg['Subject'] = f"Delivery Reports - {local_time().strftime('%d.%m. u %H:%M')}"
            msg.attach(MIMEText("Hello,\n\nAttached are the reports.\n\nSystem completed the cycle.", 'plain'))
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

def save_to_history(df):
    time_now = format_time_short()
    date_now = local_time().strftime("%Y-%m-%d")
    history_data = []
    for adr in df["Address"].unique():
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Address"] == adr) & (df["Platform"] == plat)]
            if sub.empty: continue
            opened = len(sub[sub["Status"] == "Open"])
            closed = len(sub[sub["Status"] == "Closed"])
            avg_time = sub["Time_Num"].dropna().mean()
            avg_time = 0 if pd.isna(avg_time) else round(avg_time, 1)
            promo_count = len(sub[sub["Promo"] != "-"])
            history_data.append({"Date": date_now, "Time": time_now, "Address": adr, "Platform": plat, "Open": opened, "Closed": closed, "Avg_Time": avg_time, "Promo_Count": promo_count})
    df_new = pd.DataFrame(history_data)
    file_str = str(HISTORY_FILE)
    if os.path.exists(file_str):
        df_combined = pd.concat([pd.read_csv(file_str), df_new], ignore_index=True)
    else:
        df_combined = df_new
    try: df_combined.to_csv(file_str, index=False)
    except: pass
    st.session_state.df_history = df_combined
    return df_combined

def create_status_chart_ui(df_sub, title):
    wolt_o = len(df_sub[(df_sub["Platform"] == "Wolt") & (df_sub["Status"] == "Open")])
    wolt_z = len(df_sub[(df_sub["Platform"] == "Wolt") & (df_sub["Status"] == "Closed")])
    glovo_o = len(df_sub[(df_sub["Platform"] == "Glovo") & (df_sub["Status"] == "Open")])
    glovo_z = len(df_sub[(df_sub["Platform"] == "Glovo") & (df_sub["Status"] == "Closed")])
    data = [{"Category": "Total", "Platform": "Wolt", "Count": wolt_o+wolt_z}, {"Category": "Open", "Platform": "Wolt", "Count": wolt_o}, {"Category": "Closed", "Platform": "Wolt", "Count": wolt_z}, {"Category": "Total", "Platform": "Glovo", "Count": glovo_o+glovo_z}, {"Category": "Open", "Platform": "Glovo", "Count": glovo_o}, {"Category": "Closed", "Platform": "Glovo", "Count": glovo_z}]
    fig = px.bar(data, x="Category", y="Count", color="Platform", barmode="group", color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Count", title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", title_font_size=18)
    fig.update_traces(textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def create_delivery_time_chart_ui(df_sub, title):
    w_avg = df_sub[(df_sub["Platform"] == "Wolt")]["Time_Num"].dropna().mean()
    g_avg = df_sub[(df_sub["Platform"] == "Glovo")]["Time_Num"].dropna().mean()
    w_avg = 0 if pd.isna(w_avg) else round(w_avg, 1)
    g_avg = 0 if pd.isna(g_avg) else round(g_avg, 1)
    fig = px.bar([{"Platform": "Wolt", "Time": w_avg}, {"Platform": "Glovo", "Time": g_avg}], x="Platform", y="Time", color="Platform", color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Time", title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis_title="Average time (min)", title_font_size=18)
    fig.update_traces(texttemplate='%{text} min', textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def create_promo_chart_ui(df_sub, selected_promos, title):
    wolt_count, glovo_count = 0, 0
    if selected_promos:
        for _, row in df_sub.iterrows():
            restaurant_promos = []
            if pd.notna(row['Promo']) and row['Promo'] != "-":
                restaurant_promos = [a.replace("• ", "").strip() for a in str(row['Promo']).split('\n') if a.strip()]
            if any(promo in selected_promos for promo in restaurant_promos):
                if row['Platform'] == 'Wolt': wolt_count += 1
                elif row['Platform'] == 'Glovo': glovo_count += 1
    fig = px.bar([{"Platform": "Wolt", "Count": wolt_count}, {"Platform": "Glovo", "Count": glovo_count}], x="Platform", y="Count", color="Platform", color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Count", title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis_title="Number of restaurants", title_font_size=18)
    fig.update_traces(textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def create_timeline_chart_ui(df_hist, address=None, custom_title=None, metric="Open", ylabel="Number of open restaurants"):
    df_sub = df_hist.copy()
    if metric not in df_sub.columns: df_sub[metric] = 0
    if address:
        df_sub = df_sub[df_sub["Address"] == address]
        title = custom_title if custom_title else f'History - {address.upper()}'
    else:
        if not df_sub.empty and 'Platform' in df_sub.columns and 'Date' in df_sub.columns and 'Time' in df_sub.columns:
            if metric == "Avg_Time": df_sub = df_sub.groupby(["Date", "Time", "Platform"])[metric].mean().reset_index()
            else: df_sub = df_sub.groupby(["Date", "Time", "Platform"])[metric].sum().reset_index()
        title = custom_title if custom_title else 'Summary History'
    if len(df_sub) == 0: return go.Figure().update_layout(title="No history data", plot_bgcolor="rgba(0,0,0,0)")
    df_sub["Real_Datetime"] = pd.to_datetime(df_sub["Date"] + " " + df_sub["Time"])
    df_sub = df_sub.sort_values(by="Real_Datetime")
    fig = px.line(df_sub, x="Real_Datetime", y=metric, color="Platform", markers=True, color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", xaxis_title="", yaxis_title=ylabel, hovermode="x unified", title_font_size=18)
    fig.update_xaxes(tickformat="%d.%m. u %H:%M")
    fig.update_traces(line=dict(width=3), marker=dict(size=8))
    return fig

# ---------------- DATA EXTRACTION (Glovo helpers) ----------------
def remove_accents(text):
    if not text: return ""
    for k, v in {'č':'c','ć':'c','ž':'z','š':'s','đ':'dj','Č':'C','Ć':'C','Ž':'Z','Š':'S','Đ':'Dj'}.items(): text = text.replace(k, v)
    return text

def extract_name(text):
    if not text: return ""
    for line in str(text).split('\n'):
        line = line.strip()
        if not line or '%' in line or ("min" in line.lower() and re.search(r'\d+', line.lower())): continue
        if any(x in line.lower() for x in ["rsd", "din", "promo", "novo", "new", "odlično", "besplatna dostava", "artikli", "narudžb", "popust", "off", "discount"]): continue
        if len(line) >= 2: return line
    return ""

def analyze_status(text):
    t = text.lower()
    if any(x in t for x in ["uskoro se zatvara", "closing soon", "zatvara se za", "closes in"]): return "Open"
    if any(k in t for k in ["samo preuzimanje", "samo za preuzimanje", "pickup only", "dostava nije dostupna", "dostava trenutno nije", "samo licno preuzimanje", "zatvoreno", "zakažite", "zakaži", "zakazi", "nedostupno", "otvara se", "otvara", "closed", "schedule"]): return "Closed"
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
        if m1 and int(m1.group(1)) < 120 and int(m1.group(2)) < 120: return f"{m1.group(1)}-{m1.group(2)} min", (int(m1.group(1)) + int(m1.group(2))) / 2.0
        m2 = re.search(r'\b(\d{1,3})\s*(?:min|m|\')', clean)
        if m2 and int(m2.group(1)) < 120: return f"{m2.group(1)} min", float(m2.group(1))
    except: pass
    return "-", np.nan

def extract_promo(text, html_content, plat):
    clean_text = (str(text) + " \n " + str(html_content)).lower()
    clean_text = re.sub(r'<[^>]+>', ' ', clean_text)
    clean_numbers = re.sub(r'(?<=\d)[.,](?=\d)', '', clean_text)
    promos, seen, res = [], set(), []
    if plat == "Glovo" and html_content:
        glovo_tags = re.findall(r'data-style="promotion"[^>]*>([^<]+)<', str(html_content))
        for gp in glovo_tags: promos.append(gp.strip())
    if any(x in clean_text for x in ["besplatna dostava", "free delivery", "dostava 0", "0 rsd dostava", "delivery 0", "besplatna"]): promos.append("Free delivery")
    if any(x in clean_text for x in ["1+1", "1 + 1", "buy 1 get 1"]): promos.append("1+1 Free")
    if plat == "Glovo":
        for pm in re.findall(r'(\d{1,2}\s*%)\s*(?:popust|off|discount|-)', clean_text): promos.append(f"{pm.strip()} discount")
        for rm in re.findall(r'(\d{2,5})\s*(?:rsd|din)', clean_numbers):
            if int(rm) > 10: promos.append(f"{rm} RSD discount")
    if "prime" in clean_text: promos.append("Prime")
    for a in promos:
        ac = a[0].upper() + a[1:]
        if ac not in seen:
            seen.add(ac)
            res.append(f"• {ac}")
    return "\n".join(res) if res else "-"

def normalize_name(name): return re.sub(r'[^\w]', '', str(name).lower())

# ================= WOLT LOGIKA — PREUZETA IZ promo.py =================
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Multi-koordinate po gradu (identične sa promo.py) ────────────────────────
CITY_MULTI_COORDS = {
    "Beograd": [
        (44.8610, 20.3450), (44.8395, 20.3662), (44.8251, 20.4102), (44.8130, 20.4182), (44.8050, 20.3880),
        (44.8255, 20.4571), (44.8180, 20.4522), (44.8160, 20.4735), (44.8042, 20.4521), (44.8180, 20.4620),
        (44.8001, 20.4705), (44.8145, 20.4990), (44.8080, 20.4905), (44.7932, 20.4800), (44.8175, 20.5182),
        (44.8160, 20.4950), (44.8100, 20.5100), (44.7925, 20.4430), (44.7920, 20.4350), (44.7820, 20.4550),
        (44.7760, 20.4180), (44.7500, 20.4100), (44.7870, 20.4660), (44.7975, 20.4650), (44.8070, 20.4100),
    ],
    "Novi Sad": [(45.2671, 19.8335), (45.2500, 19.8100), (45.2850, 19.8600), (45.2400, 19.8700), (45.2900, 19.7900), (45.2750, 19.8450), (45.2600, 19.8650), (45.2450, 19.8200), (45.2550, 19.7950), (45.2350, 19.8500)],
    "Nis": [(43.3209, 21.8958), (43.3050, 21.8800), (43.3350, 21.9150), (43.3100, 21.9300), (43.2950, 21.8700), (43.3280, 21.9050), (43.3150, 21.8650), (43.3400, 21.8900), (43.3000, 21.9100), (43.3450, 21.9250)],
    "Kragujevac": [(44.0128, 20.9114), (44.0000, 20.8900), (44.0300, 20.9300), (43.9900, 20.9400), (44.0200, 20.9000), (44.0050, 20.9250), (44.0350, 20.9100), (43.9950, 20.9000), (44.0150, 20.9500), (44.0280, 20.8800)],
    "Cacak": [(43.8914, 20.3496), (43.8800, 20.3350), (43.9050, 20.3650), (43.8700, 20.3600), (43.9100, 20.3300), (43.8850, 20.3750), (43.8750, 20.3200), (43.9000, 20.3500), (43.8650, 20.3450), (43.9150, 20.3700)],
    "Pancevo": [(44.8708, 20.6408), (44.8580, 20.6260), (44.8850, 20.6560), (44.8500, 20.6500), (44.8920, 20.6300), (44.8640, 20.6650), (44.8750, 20.6150), (44.8980, 20.6480), (44.8450, 20.6400), (44.8800, 20.6750)],
    "Subotica": [(46.1003, 19.6675), (46.0880, 19.6520), (46.1150, 19.6840), (46.0800, 19.6800), (46.1250, 19.6600), (46.0950, 19.6950), (46.1050, 19.6400), (46.1300, 19.6750), (46.0750, 19.6700), (46.1150, 19.7050)],
    "Zrenjanin": [(45.3819, 20.3833), (45.3700, 20.3690), (45.3950, 20.3980), (45.3630, 20.3930), (45.4020, 20.3750), (45.3780, 20.4100), (45.3850, 20.3600), (45.4100, 20.3900), (45.3580, 20.3830), (45.3950, 20.4200)],
    "Novi Pazar": [(43.1367, 20.5122), (43.1250, 20.4980), (43.1500, 20.5280), (43.1180, 20.5200), (43.1550, 20.5000), (43.1300, 20.5400), (43.1420, 20.4880), (43.1600, 20.5250), (43.1120, 20.5100), (43.1450, 20.5500)],
    "Krusevac": [(43.5833, 21.3333), (43.5700, 21.3200), (43.5980, 21.3480), (43.5620, 21.3450), (43.6050, 21.3150), (43.5750, 21.3550), (43.5850, 21.3050), (43.6000, 21.3400), (43.5550, 21.3350), (43.5900, 21.3650)],
    "Leskovac": [(42.9981, 21.9461), (42.9850, 21.9320), (43.0120, 21.9600), (42.9780, 21.9550), (43.0180, 21.9300), (42.9920, 21.9680), (43.0050, 21.9200), (43.0200, 21.9500), (42.9700, 21.9450), (43.0100, 21.9750)],
    "Valjevo": [(44.2742, 19.8878), (44.2620, 19.8730), (44.2880, 19.9040), (44.2550, 19.9000), (44.2950, 19.8780), (44.2700, 19.9150), (44.2800, 19.8630), (44.3000, 19.8980), (44.2500, 19.8900), (44.2850, 19.9250)],
    "Smederevo": [(44.6644, 20.9278), (44.6520, 20.9130), (44.6780, 20.9430), (44.6450, 20.9380), (44.6850, 20.9180), (44.6580, 20.9550), (44.6700, 20.9050), (44.6920, 20.9350), (44.6380, 20.9280), (44.6750, 20.9650)],
    "Uzice": [(43.8567, 19.8483), (43.8450, 19.8340), (43.8700, 19.8640), (43.8380, 19.8600), (43.8780, 19.8350), (43.8500, 19.8750), (43.8620, 19.8220), (43.8850, 19.8550), (43.8320, 19.8500), (43.8700, 19.8850)],
    "Kraljevo": [(43.7236, 20.6894), (43.7120, 20.6750), (43.7350, 20.7050), (43.7050, 20.7000), (43.7400, 20.6700), (43.7180, 20.7150), (43.7280, 20.6600), (43.7450, 20.6950), (43.7000, 20.6850), (43.7320, 20.7200)],
    "Jagodina": [(43.9766, 21.2614), (43.9650, 21.2480), (43.9900, 21.2750), (43.9580, 21.2700), (43.9850, 21.2450), (43.9700, 21.2800), (43.9950, 21.2550), (43.9620, 21.2550), (43.9780, 21.2900), (43.9530, 21.2650)],
    "Obrenovac": [(44.6547, 20.2111), (44.6430, 20.1980), (44.6680, 20.2250), (44.6360, 20.2200), (44.6750, 20.2000), (44.6480, 20.2350), (44.6580, 20.1880), (44.6820, 20.2150), (44.6300, 20.2100), (44.6650, 20.2450)],
    "Lazarevac": [(44.3800, 20.2569), (44.3680, 20.2430), (44.3930, 20.2700), (44.3620, 20.2650), (44.3980, 20.2350), (44.3720, 20.2800), (44.3850, 20.2250), (44.4000, 20.2600), (44.3580, 20.2550), (44.3920, 20.2900)],
    "Pozarevac": [(44.6197, 21.1869), (44.6080, 21.1720), (44.6330, 21.2020), (44.6010, 21.2000), (44.6400, 21.1750), (44.6150, 21.2150), (44.6250, 21.1600), (44.6450, 21.1950), (44.5950, 21.1900), (44.6300, 21.2250)],
    "Sombor": [(45.7772, 19.1122), (45.7650, 19.0980), (45.7900, 19.1280), (45.7580, 19.1200), (45.7980, 19.1000), (45.7720, 19.1400), (45.7850, 19.0880), (45.8050, 19.1200), (45.7520, 19.1100), (45.7920, 19.1500)],
    "Arandelovac": [(44.3028, 20.5611), (44.2950, 20.5500), (44.3100, 20.5700), (44.2880, 20.5750), (44.3150, 20.5450), (44.3050, 20.5800), (44.2920, 20.5350), (44.3200, 20.5600), (44.2980, 20.5250), (44.3080, 20.5950)],
    "Bor": [(44.0769, 22.0958), (44.0650, 22.0800), (44.0900, 22.1100), (44.0580, 22.1050), (44.0850, 22.0700), (44.0700, 22.1200), (44.0950, 22.0900), (44.0620, 22.0650), (44.0780, 22.1300), (44.1000, 22.0800)],
    "Borca": [(44.8820, 20.5350), (44.8750, 20.5200), (44.8900, 20.5500), (44.8680, 20.5450), (44.8950, 20.5150), (44.8700, 20.5600), (44.8830, 20.5050), (44.8780, 20.5700), (44.8640, 20.5300), (44.8920, 20.5400)],
    "Vrsac": [(45.1167, 21.3000), (45.1050, 21.2860), (45.1300, 21.3150), (45.0980, 21.3100), (45.1380, 21.2900), (45.1120, 21.3280), (45.1230, 21.2750), (45.1450, 21.3050), (45.0920, 21.3000), (45.1300, 21.3380)],
    "Zlatibor": [(43.7253, 19.7036), (43.7150, 19.6900), (43.7380, 19.7180), (43.7080, 19.7150), (43.7450, 19.6980), (43.7200, 19.7300), (43.7300, 19.6800), (43.7500, 19.7100), (43.7050, 19.7050), (43.7350, 19.7400)],
}

# ── City slug mapa (isti ključevi kao CITY_MULTI_COORDS) ─────────────────────
CITY_SLUG_MAP = {
    "Beograd": "belgrade", "Novi Sad": "novi-sad", "Nis": "nis", "Kragujevac": "kragujevac",
    "Cacak": "cacak", "Pancevo": "pancevo", "Subotica": "subotica", "Zrenjanin": "zrenjanin",
    "Novi Pazar": "novi-pazar", "Krusevac": "krusevac", "Leskovac": "leskovac",
    "Valjevo": "valjevo", "Smederevo": "smederevo", "Uzice": "uzice", "Kraljevo": "kraljevo",
    "Jagodina": "jagodina", "Obrenovac": "obrenovac", "Lazarevac": "lazarevac",
    "Pozarevac": "pozarevac", "Sombor": "sombor", "Arandelovac": "arandelovac",
    "Bor": "bor", "Borca": "borca", "Vrsac": "vrsac", "Zlatibor": "zlatibor",
}

# Alias mapa za geocoding detekciju grada
CITY_ALIASES = {
    "belgrade": "Beograd", "beograd": "Beograd", "city of belgrade": "Beograd",
    "novi sad": "Novi Sad", "novi sad (city)": "Novi Sad",
    "nis": "Nis", "nish": "Nis", "niš": "Nis",
    "kragujevac": "Kragujevac", "cacak": "Cacak", "čačak": "Cacak",
    "pancevo": "Pancevo", "pančevo": "Pancevo", "subotica": "Subotica",
    "zrenjanin": "Zrenjanin", "novi pazar": "Novi Pazar",
    "krusevac": "Krusevac", "kruševac": "Krusevac", "leskovac": "Leskovac",
    "valjevo": "Valjevo", "smederevo": "Smederevo", "uzice": "Uzice", "užice": "Uzice",
    "kraljevo": "Kraljevo", "jagodina": "Jagodina", "obrenovac": "Obrenovac",
    "lazarevac": "Lazarevac", "pozarevac": "Pozarevac", "požarevac": "Pozarevac",
    "sombor": "Sombor", "arandelovac": "Arandelovac", "aranđelovac": "Arandelovac",
    "bor": "Bor", "borca": "Borca", "borča": "Borca",
    "vrsac": "Vrsac", "vršac": "Vrsac", "zlatibor": "Zlatibor",
}

def _detect_city_coords(lat: float, lon: float, city_raw: str) -> tuple:
    city_lower = city_raw.lower().strip()
    if city_lower in CITY_ALIASES:
        key = CITY_ALIASES[city_lower]
        return key, CITY_MULTI_COORDS[key], CITY_SLUG_MAP.get(key, key.lower().replace(" ", "-"))
    for alias, key in CITY_ALIASES.items():
        if alias in city_lower or city_lower.startswith(alias):
            return key, CITY_MULTI_COORDS[key], CITY_SLUG_MAP.get(key, key.lower().replace(" ", "-"))
    city_ascii = cyrillic_to_latin(city_raw).lower().strip()
    for key in CITY_MULTI_COORDS:
        key_ascii = cyrillic_to_latin(key).lower()
        if key_ascii == city_ascii or city_ascii.startswith(key_ascii) or key_ascii.startswith(city_ascii):
            return key, CITY_MULTI_COORDS[key], CITY_SLUG_MAP.get(key, key_ascii.replace(" ", "-"))
    print(f"[WOLT WARN] Grad '{city_raw}' nije prepoznat — fallback na jednu koordinatu.")
    slug_raw = cyrillic_to_latin(city_raw).lower().replace(" ", "-")
    return city_raw, [(lat, lon)], CITY_SLUG_MAP.get(city_raw, slug_raw)

# ── Wolt HTTP session — IDENTIČAN promo.py pristupu ──────────────────────────
# U promo.py globalni session se zove `session`, ne `wolt_session`.
# Isti semafor pristup sa FETCH_WORKERS = 2.

WOLT_FETCH_WORKERS = 2  # isto kao FETCH_WORKERS u promo.py

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "sr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://wolt.com",
    "Referer": "https://wolt.com/en/srb/",
    "W-PlatformType": "Web",
    "W-Wolt-Session-Id": "wolt-monitor-session",
}

# Jedan globalni requests.Session — tačno kao u promo.py
session = requests.Session()
session.headers.update(BROWSER_HEADERS)

_session_lock = threading.Lock()
_last_refresh_time = 0.0

_throttle_until = 0.0
_throttle_lock  = threading.Lock()
_fetch_log_lock = threading.Lock()

# Globalni semafor — isti pristup kao u promo.py
_global_http_sem = threading.Semaphore(WOLT_FETCH_WORKERS)


def _refresh_wolt_session() -> bool:
    """Identično sa promo.py — obnavlja session kroz anonimni init poziv."""
    global _last_refresh_time
    with _session_lock:
        now = time.time()
        if now - _last_refresh_time < 60:
            return True
        try:
            init_url = "https://restaurant-api.wolt.com/v1/pages/restaurants?lat=44.8178&lon=20.4569&skip=0"
            r = requests.get(init_url, headers=BROWSER_HEADERS, timeout=15)
            if r.status_code == 200:
                session.cookies.update(r.cookies)
                _last_refresh_time = now
                return True
        except Exception:
            pass
        return False


def wolt_get(url: str) -> tuple:
    """Identično sa promo.py."""
    try:
        with _global_http_sem:
            r = session.get(url, timeout=15)
        if r.status_code == 200:
            return r.json(), 200
        if r.status_code in (401, 403):
            _refresh_wolt_session()
            with _global_http_sem:
                r2 = session.get(url, timeout=15)
            if r2.status_code == 200:
                return r2.json(), 200
            return None, r2.status_code
        return None, r.status_code
    except Exception:
        return None, -1


def make_thread_session() -> requests.Session:
    """Identično sa promo.py — čita cookie iz fajla ako postoji."""
    s = requests.Session()
    for k, v in session.headers.items():
        s.headers[k] = v
    try:
        cookie_val = Path("_scan_cookie.txt").read_text().strip()
    except Exception:
        cookie_val = ""
    if cookie_val:
        s.headers["Cookie"] = cookie_val
    return s


def _log_fetch(msg: str):
    try:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        with _fetch_log_lock:
            with open("_wolt_fetch_debug.log", "a", encoding="utf-8") as f:
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


def _fetch_url(ts, url: str, label: str, stop_event: threading.Event) -> tuple:
    """
    Identično sa promo.py — prima stop_event i proverava ga pre svakog pokušaja.
    Ovo je KLJUČNA razlika u odnosu na staru verziju koda.
    """
    for attempt in range(4):
        if stop_event.is_set():
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


def _parse_dynamic_with_item_discount(data: dict) -> list:
    """Identično sa promo.py."""
    akcije = []
    seen = set()
    ignore_texts = {
        "prikaži detalje", "show details", "vidi sve", "see all",
        "detalji restorana", "restaurant details", "more", "još",
        "schedule order", "naruči", "see menu", "add {amount} more",
        "try for 30 days for free!", "get rsd0 delivery fee & more!",
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
        is_wp = (disc.get("has_wolt_plus") or
                 (disc.get("banner") or {}).get("show_wolt_plus", False) or
                 (disc.get("conditions") or {}).get("has_wolt_plus") == True)
        banner = disc.get("banner") or {}
        desc = disc.get("description") or {}
        primary_text = banner.get("formatted_text") or desc.get("title") or ""
        add(primary_text, wolt_plus=is_wp)
        effects = disc.get("effects") or {}
        item_discount_dict = effects.get("item_discount")
        if item_discount_dict and isinstance(item_discount_dict, dict):
            fraction = item_discount_dict.get("fraction")
            if fraction and float(fraction) > 0:
                pct = int(round(float(fraction) * 100))
                add(primary_text or f"{pct}% popust na izabrane artikle", wolt_plus=is_wp)
        basket_disc = effects.get("basket_discount")
        if basket_disc and isinstance(basket_disc, dict):
            amount = basket_disc.get("amount")
            fraction = basket_disc.get("fraction")
            if amount and int(amount) > 0:
                add(primary_text or f"{int(amount)//100} RSD popust na korpu", wolt_plus=is_wp)
            elif fraction and float(fraction) > 0:
                pct = int(round(float(fraction) * 100))
                add(primary_text or f"{pct}% popust na celu korpu", wolt_plus=is_wp)
        delivery_disc = effects.get("delivery_discount")
        if delivery_disc and isinstance(delivery_disc, dict):
            amount = delivery_disc.get("amount")
            fraction = delivery_disc.get("fraction")
            if (amount is not None and int(amount) == 0) or (fraction and float(fraction) >= 1.0):
                add(primary_text or "Besplatna dostava", wolt_plus=is_wp)
            elif amount and int(amount) > 0:
                add(primary_text or f"{int(amount)//100} RSD popust na dostavu", wolt_plus=is_wp)
        free_items = effects.get("free_items")
        if free_items and isinstance(free_items, (dict, list)):
            add(primary_text or "Gratis artikal uz porudžbinu", wolt_plus=is_wp)

    venue = data.get("venue") or {}
    for ban in venue.get("banners", []):
        if not isinstance(ban, dict): continue
        is_wp = ban.get("show_wolt_plus", False)
        disc = ban.get("discount") or {}
        add(disc.get("formatted_text"), wolt_plus=is_wp)

    offer_assistant = venue.get("offer_assistant") or {}
    for tracker in offer_assistant.get("offer_trackers", []):
        if not isinstance(tracker, dict): continue
        is_wp = tracker.get("offer_type") == "wolt_plus" or tracker.get("show_wolt_plus", False)
        add(tracker.get("title"), wolt_plus=is_wp)

    return akcije


def _fetch_one_wolt(slug: str, lat: float, lon: float, feed_akcije: list, stop_event: threading.Event) -> tuple:
    """
    Identično sa _fetch_one u promo.py.
    KLJUČNA razlika: prima stop_event i prosleđuje ga _fetch_url.
    """
    if stop_event.is_set():
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
            parsed = _parse_dynamic_with_item_discount(dyn_data)
            combined = list(dict.fromkeys(feed_akcije + parsed))
            akcije_str = "\n".join(combined) if combined else "-"
            if akcije_str == "-":
                _log_fetch(f"DYN {slug} → 200 ali NEMA akcija")
        except Exception as e:
            _log_fetch(f"DYN {slug} → parse EXC {e}")
    elif feed_akcije:
        akcije_str = "\n".join(feed_akcije)
    return slug, akcije_str


# ── Live progress (piše u JSON fajl, čita ga async UI loop) ──────────────────
WOLT_PROGRESS_FILE = Path("_wolt_progress.json")
_wolt_progress_lock = threading.Lock()

def _write_wolt_progress(data: dict):
    try:
        tmp = Path("_wolt_progress.json.tmp")
        tmp.write_text(__import__('json').dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(WOLT_PROGRESS_FILE)
    except Exception:
        pass

def _read_wolt_progress() -> dict:
    try:
        return __import__('json').loads(WOLT_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _clear_wolt_progress():
    try: WOLT_PROGRESS_FILE.unlink(missing_ok=True)
    except: pass

# ================= WOLT SCRAPER — logika identična fetch_city() iz promo.py =================
def scrape_wolt_sync(address: str, fast_mode: bool = False) -> list:
    """
    Requests-based Wolt scraper koji koristi istu logiku kao fetch_city() u promo.py.
    fast_mode=True: preskače dynamic API (samo feed) — ~30s
    fast_mode=False: puni detaljan sken sa promocijama — sporiji ali kompletan
    Piše live progress u _wolt_progress.json za prikaz u UI-u.
    """
    import urllib.request as _urllib_req
    import json as _json

    MAX_LOCS = 3 if fast_mode else 5

    stop_event = threading.Event()

    _write_wolt_progress({"status": "🌍 Geocoding...", "found": 0, "total": 0, "promo_done": 0, "fast_mode": fast_mode})
    print(f"[WOLT] START scrape_wolt_sync za: {address} | fast_mode={fast_mode}")
    print(f"[WOLT] Geocoding: {address}...")

    geo_data = []
    custom_agent = 'DeliveryMonitorApp/7.0 (wolt_scraper)'
    try:
        geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address + ', Serbia')}&format=json&limit=1&addressdetails=1"
        req = _urllib_req.Request(geo_url, headers={'User-Agent': custom_agent})
        with _urllib_req.urlopen(req) as response:
            geo_data = _json.loads(response.read().decode())
        if not geo_data:
            time.sleep(1)
            geo_url2 = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address)}&format=json&limit=1&addressdetails=1"
            req2 = _urllib_req.Request(geo_url2, headers={'User-Agent': custom_agent})
            with _urllib_req.urlopen(req2) as response2:
                geo_data = _json.loads(response2.read().decode())
    except Exception as e:
        print(f"[WOLT ERROR] Geocoding failed: {e}")
        _write_wolt_progress({"status": f"❌ Geocoding failed: {e}", "found": 0, "total": 0, "promo_done": 0, "fast_mode": fast_mode})
        return []

    if not geo_data:
        print(f"[WOLT ERROR] Cannot find coordinates for: {address}")
        _write_wolt_progress({"status": f"❌ Cannot geocode: {address}", "found": 0, "total": 0, "promo_done": 0, "fast_mode": fast_mode})
        return []

    geo_lat = float(geo_data[0]["lat"])
    geo_lon = float(geo_data[0]["lon"])
    addr_details = geo_data[0].get("address", {})
    city_raw = (
        addr_details.get("city") or addr_details.get("town") or
        addr_details.get("village") or addr_details.get("municipality") or "Belgrade"
    )

    city_key, multi_coords, city_wolt_slug = _detect_city_coords(geo_lat, geo_lon, city_raw)
    multi_coords = multi_coords[:MAX_LOCS]
    print(f"[WOLT] Coords: {geo_lat:.4f},{geo_lon:.4f} | City: {city_key} | "
          f"Locations: {len(multi_coords)} (max {MAX_LOCS}) | Slug: {city_wolt_slug}")
    _write_wolt_progress({"status": f"📍 City: {city_key} | {len(multi_coords)} locations | loading feed...", "found": 0, "total": 0, "promo_done": 0, "fast_mode": fast_mode})

    primary_lat, primary_lon = multi_coords[0]

    # ── Korak 1: Feed paginacija ──────────────────────────────────────────────
    restaurants = {}

    for loc_idx, (lat, lon) in enumerate(multi_coords):
        if stop_event.is_set():
            break
        loc_label = f"lok.{loc_idx+1}/{len(multi_coords)}"
        skip = 0

        for page_num in range(50):  # max 50 stranica po lokaciji (isto kao promo.py)
            if stop_event.is_set():
                break
            count_before = len(restaurants)
            endpoint = f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}&skip={skip}"
            data, _status = wolt_get(endpoint)
            items_in_response = 0

            if data:
                for section in data.get("sections", []):
                    for item in section.get("items", []):
                        venue = item.get("venue")
                        if not venue: continue
                        name = venue.get("name", "")
                        slug = venue.get("slug", "")
                        if not name or not slug or slug in restaurants: continue
                        items_in_response += 1

                        status_val = "Open" if venue.get("online") else "Closed"
                        rating_obj = venue.get("rating") or {}
                        r_score = rating_obj.get("score", "-") if isinstance(rating_obj, dict) else "-"
                        est = venue.get("estimate_range") or venue.get("estimate")
                        time_str = f"{est} min" if est else "-"

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
                                if txt.lower() in ["novo", "new"]: novo_status = True
                                else: feed_akcije.append(f"• {txt}")
                        label = venue.get("label", "")
                        if label:
                            if label.lower() in ["novo", "new"]: novo_status = True
                            else: feed_akcije.append(f"• {label}")

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
                            "Link":          f"https://wolt.com/en/srb/{city_wolt_slug}/restaurant/{slug}",
                            "_slug":         slug,
                            "_feed_akcije":  feed_akcije,
                        }

            new_this_page = len(restaurants) - count_before
            print(f"[WOLT] {loc_label} | page {page_num+1} | +{new_this_page} | total {len(restaurants)}")
            _write_wolt_progress({
                "status": f"📡 Feed | {loc_label} | str.{page_num+1} | +{new_this_page} novih",
                "found": len(restaurants), "total": 0, "promo_done": 0, "fast_mode": fast_mode
            })

            if items_in_response == 0:
                break
            skip += 40
            time.sleep(random.uniform(0.5, 1.8))

        print(f"[WOLT] Location {loc_idx+1}/{len(multi_coords)} done — total: {len(restaurants)}")
        _write_wolt_progress({
            "status": f"✅ Lokacija {loc_idx+1}/{len(multi_coords)} gotova | ukupno: {len(restaurants)}",
            "found": len(restaurants), "total": 0, "promo_done": 0, "fast_mode": fast_mode
        })

    if not restaurants:
        print(f"[WOLT ERROR] No restaurants found for {address}")
        _write_wolt_progress({"status": "⚠️ Nema restorana pronađeno", "found": 0, "total": 0, "promo_done": 0, "fast_mode": fast_mode})
        return []

    total_found = len(restaurants)
    print(f"[WOLT] Feed done: {total_found} restaurants.")

    # ── Korak 2: Dynamic API za promocije (preskači u fast_mode) ─────────────
    if fast_mode:
        print(f"[WOLT] Fast mode — preskačem dynamic API, vraćam feed podatke.")
        _write_wolt_progress({"status": f"⚡ Fast mode završen | {total_found} restorana", "found": total_found, "total": total_found, "promo_done": total_found, "fast_mode": True})
        result = []
        for r in restaurants.values():
            r.pop("_slug", None)
            r.pop("_feed_akcije", None)
            result.append(r)
        return result

    print(f"[WOLT] Loading promotions for {total_found} restaurants...")
    _write_wolt_progress({"status": f"🔍 Učitavam promocije (0/{total_found})...", "found": total_found, "total": total_found, "promo_done": 0, "fast_mode": False})

    slugs = list(restaurants.keys())
    total = len(slugs)
    completed = 0

    with ThreadPoolExecutor(max_workers=WOLT_FETCH_WORKERS) as executor:
        futures = {
            executor.submit(
                _fetch_one_wolt, slug, primary_lat, primary_lon,
                restaurants[slug]["_feed_akcije"], stop_event
            ): slug for slug in slugs
        }
        for future in as_completed(futures):
            if stop_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                break
            try:
                slug, akcije_str = future.result()
                restaurants[slug]["Promo"] = akcije_str
            except Exception:
                pass
            completed += 1
            if completed % 10 == 0 or completed == total:
                print(f"[WOLT] Promotions: {completed}/{total}")
                _write_wolt_progress({
                    "status": f"🔍 Promocije: {completed}/{total} restorana",
                    "found": total, "total": total, "promo_done": completed, "fast_mode": False
                })

    result = []
    for r in restaurants.values():
        r.pop("_slug", None)
        r.pop("_feed_akcije", None)
        result.append(r)

    print(f"[WOLT] Done. {len(result)} restaurants for '{address}'.")
    _write_wolt_progress({"status": f"✅ Završeno | {len(result)} restorana", "found": len(result), "total": len(result), "promo_done": len(result), "fast_mode": False})
    return result


# ---------------- SPARTAN MODE: FAKE PIXEL (za Glovo) ----------------
TINY_PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

async def smart_diet_mode(route):
    if route.request.resource_type in ["image", "media"]:
        await route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
    else:
        await route.continue_()

async def smart_scroll_and_extract(page, plat, address, log_ph=None, live_ph=None, live_state=None):
    results_dict = {}
    prev_count = 0
    attempts_at_bottom = 0
    while True:
        data = await page.evaluate('''() => {
            let rez = [];
            document.querySelectorAll("a:has(h3), a[data-testid='store-card'], .store-card a").forEach(c => {
                let link = c.href;
                if (!link.includes('/dostava') && !link.includes('/category')) { rez.push({link: link, text: c.innerText, html: c.innerHTML}); }
            });
            return rez;
        }''')
        for item in data:
            link = item['link']
            if not link or link in results_dict: continue
            text = item['text']
            html_content = item.get('html', '')
            all_text = text + " " + html_content
            name = remove_accents(extract_name(text))
            if len(name) < 2: continue
            rating = extract_rating(all_text, plat)
            time_str, time_num = extract_delivery_time(all_text)
            promo_str = extract_promo(text, html_content, plat)
            t_low = text.strip().lower()
            is_new = t_low.endswith('new') or t_low.endswith('novo') or bool(re.search(r'•.*?new\b', t_low))
            results_dict[link] = {"Address": address, "Platform": plat, "Name": name, "Rating": rating, "Delivery Time": time_str, "Promo": promo_str, "Status": analyze_status(all_text), "Time_Num": time_num, "Is_New": is_new, "Link": link}
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
            if attempts_at_bottom >= 5: break
    return list(results_dict.values())

async def scrape_glovo(context_glovo, address, log_ph=None, live_ph=None, live_state=None, error_screenshots=None, debug_mode=False):
    page = None
    try:
        page = await context_glovo.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.set_default_timeout(15000)
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")
        try:
            accept_btn = page.locator("button", has_text=re.compile(r"Accept All|Prihvati sve", re.IGNORECASE)).first
            await accept_btn.wait_for(state="visible", timeout=4000)
            await accept_btn.click()
            await asyncio.sleep(1.5)
        except: pass
        stranica_tekst = await page.content()
        if "Oh, no!" in stranica_tekst or "It looks like there's a problem" in stranica_tekst:
            log_msg(f"[GLOVO BLOCKED] {address}.", log_ph)
            return []
        try:
            hero_input = page.locator("#hero-container-input")
            await hero_input.wait_for(state="visible", timeout=5000)
            await hero_input.click()
            search = page.get_by_role("searchbox")
            await search.fill(address)
            dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
            await dropdown_item.wait_for(state="visible", timeout=8000)
            await dropdown_item.click()
        except PlaywrightTimeoutError:
            log_msg(f"[GLOVO] Changing address in header to: {address}", log_ph)
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
            except PlaywrightTimeoutError:
                log_msg(f"[GLOVO ABORT] Cannot change address for {address}.", log_ph)
                return []
        try:
            btn_drugo = page.locator("button:has-text('Drugo')")
            await btn_drugo.wait_for(state="visible", timeout=4000)
            await btn_drugo.click()
        except PlaywrightTimeoutError: pass
        try:
            btn_potvrdi = page.locator("button", has_text=re.compile(r"Potvrdi adresu", re.IGNORECASE)).first
            await btn_potvrdi.wait_for(state="visible", timeout=8000)
            await btn_potvrdi.click(force=True)
        except Exception: pass
        await asyncio.sleep(6)
        try:
            btn_pocetna = page.locator("text='Idi na početnu stranicu'")
            if await btn_pocetna.count() > 0 and await btn_pocetna.first.is_visible(timeout=4000):
                await btn_pocetna.first.click()
                await asyncio.sleep(6)
        except: pass
        try:
            kat_link = page.get_by_role("link", name=re.compile(r"Restorani|Hrana|Food|Restaurants", re.I)).first
            await kat_link.wait_for(state="visible", timeout=6000)
            await kat_link.click()
        except PlaywrightTimeoutError: pass
        try:
            await page.wait_for_selector("a[data-testid='store-card'], .store-card a", timeout=15000)
        except PlaywrightTimeoutError:
            log_msg(f"[GLOVO WARNING] Restaurants not loaded in time for {address}.", log_ph)
        page.set_default_timeout(60000)
        rez = await smart_scroll_and_extract(page, "Glovo", address, log_ph, live_ph, live_state)
        if len(rez) < 5 and debug_mode and error_screenshots is not None:
            err_path = str(ERRORS_DIR / f"Glovo_Warning_{remove_accents(address).replace(' ', '_')}_{timestamp()}.png")
            try:
                await page.screenshot(path=err_path)
                error_screenshots.append(err_path)
            except: pass
        return rez
    except Exception as e:
        log_msg(f"[GLOVO ERROR] {e}", log_ph)
        if page and error_screenshots is not None and debug_mode:
            try:
                err_path = str(ERRORS_DIR / f"Glovo_Error_{remove_accents(address).replace(' ', '_')}_{timestamp()}.png")
                await page.screenshot(path=err_path)
                error_screenshots.append(err_path)
            except: pass
        return []
    finally:
        if page: await page.close()

async def scan_process(addresses, log_ph, live_ph, live_state, generate_pdf=False, recipient_email="", debug_mode=False, fast_mode=False):
    all_data = []
    error_screenshots = []
    _clear_wolt_progress()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"])
        ga = {
            "permissions": ['geolocation'],
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9,sr;q=0.8"}
        }
        if debug_mode:
            ga["record_video_dir"] = str(ERRORS_DIR)
            ga["record_video_size"] = {"width": 1280, "height": 720}
        if os.path.exists(GLOVO_AUTH_FILE):
            log_msg("🔐 GLOVO: Loaded VIP pass.", log_ph)
            ga["storage_state"] = GLOVO_AUTH_FILE

        for i, adr in enumerate(addresses):
            live_state["Wolt"] = 0
            live_state["Glovo"] = 0
            refresh_live_ui(live_ph, 0, 0, adr)

            if i > 0:
                log_msg("⏳ Pausing for 5 seconds between addresses...", log_ph)
                await asyncio.sleep(5)

            log_msg(f"\n[SYSTEM] Starting scan for: {adr}", log_ph)

            # ── GLOVO (Playwright) ────────────────────────────────────────────
            log_msg("📱 Scrolling GLOVO...", log_ph)
            context_glovo = await browser.new_context(**ga)
            await context_glovo.route("**/*", smart_diet_mode)
            r_glovo = await scrape_glovo(context_glovo, adr, log_ph, live_ph, live_state, error_screenshots, debug_mode)
            all_data.extend(r_glovo)
            await context_glovo.close()

            # ── WOLT (requests, sa live progress UI) ─────────────────────────
            mode_label = "⚡ Fast" if fast_mode else "🔍 Detailed"
            log_msg(f"🚲 Calling WOLT API ({mode_label} mode)...", log_ph)
            _clear_wolt_progress()

            # Pokrenemo scraper u pozadini
            wolt_task = asyncio.create_task(
                asyncio.to_thread(scrape_wolt_sync, adr, fast_mode)
            )

            # Live progress UI dok scraper radi
            wolt_progress_ph = st.empty()
            while not wolt_task.done():
                prog = _read_wolt_progress()
                if prog:
                    found = prog.get("found", 0)
                    total_r = prog.get("total", 0)
                    promo_done = prog.get("promo_done", 0)
                    status_txt = prog.get("status", "")
                    pct_feed = min(found / max(total_r, found, 1), 1.0) if found > 0 else 0
                    pct_promo = promo_done / max(total_r, 1) if total_r > 0 else 0

                    wolt_progress_ph.markdown(f"""
<div style="background:#f0f8ff; border-radius:10px; padding:12px 18px; border-left:5px solid #00c2e8; margin-bottom:8px;">
  <b style="color:#00c2e8;">🚲 WOLT — {mode_label} mode</b><br>
  <span style="font-size:13px; color:#555;">{status_txt}</span><br>
  <div style="margin-top:6px; background:#ddd; border-radius:4px; height:8px;">
    <div style="width:{int(pct_feed*100)}%; background:#00c2e8; height:8px; border-radius:4px;"></div>
  </div>
  <span style="font-size:12px; color:#888;">Feed: {found} restorana{"  |  Promo: " + str(promo_done) + "/" + str(total_r) if total_r > 0 and not fast_mode else ""}</span>
</div>
""", unsafe_allow_html=True)
                await asyncio.sleep(1.5)

            wolt_progress_ph.empty()

            try:
                r_wolt = await asyncio.wait_for(asyncio.shield(wolt_task), timeout=5)
            except (asyncio.TimeoutError, Exception) as e:
                log_msg(f"[WOLT] ⚠️ Error/Timeout: {e}", log_ph)
                r_wolt = []

            if live_ph is not None and live_state is not None:
                live_state["Wolt"] = len(r_wolt)
                refresh_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], adr)
            log_msg(f"[WOLT] ✅ {len(r_wolt)} restaurants loaded for {adr}.", log_ph)
            all_data.extend(r_wolt)

        await browser.close()

    if all_data:
        df_s = pd.DataFrame(all_data)
        df_h = save_to_history(df_s)
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
                send_email(pdf_files, recipient_email, log_ph)
        else:
            log_msg("Scan complete. PDF option is disabled.", log_ph)
        return df_s, df_h, pdf_files, error_screenshots
    return pd.DataFrame(), pd.DataFrame(), [], error_screenshots

# ================= STREAMLIT UI =================
if 'is_running' not in st.session_state: st.session_state.is_running = False
if 'last_run' not in st.session_state: st.session_state.last_run = 0
if 'df_all' not in st.session_state: st.session_state.df_all = pd.DataFrame()
if 'pdf_files' not in st.session_state: st.session_state.pdf_files = []
if 'error_screenshots' not in st.session_state: st.session_state.error_screenshots = []
if 'loaded_history' not in st.session_state: st.session_state.loaded_history = False
if 'df_history' not in st.session_state:
    if os.path.exists(HISTORY_FILE): st.session_state.df_history = pd.read_csv(HISTORY_FILE)
    else: st.session_state.df_history = pd.DataFrame()

st.title("🍔 Delivery Monitor (Wolt & Glovo)")
with st.sidebar:
    st.header("⚙️ Settings")
    address_1 = st.text_input("📍 Address 1 (Required):", value="", placeholder="Makenzijeva 57, Belgrade")
    address_2 = st.text_input("📍 Address 2 (Optional):", value="", placeholder="Somborska 5, Niš")
    auto_refresh = st.checkbox("🔄 Auto-refresh", value=False)
    sleep_interval = st.number_input("⏱️ Interval (min):", min_value=1, value=60, disabled=not auto_refresh)
    generate_pdf = st.checkbox("📄 Generate PDF reports", value=False)
    email_input = st.text_input("📧 Send to email:", placeholder="your@email.com") if generate_pdf else ""
    st.markdown("---")
    debug_mode = st.checkbox("🛠️ Enable Debug Mode", value=False)
    st.markdown("---")
    st.markdown("**🚲 Wolt scan mode:**")
    fast_mode = st.radio(
        "Wolt mode",
        options=["⚡ Fast (samo status/vreme, ~1min)", "🔍 Detailed (+ promocije, ~5-8min)"],
        index=0,
        label_visibility="collapsed"
    ) == "⚡ Fast (samo status/vreme, ~1min)"
    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ START", type="primary", use_container_width=True):
            st.session_state.is_running = True
            st.session_state.loaded_history = False
            st.session_state.last_run = 0
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
        try: options[datetime.datetime.strptime(name, "%Y%m%d_%H%M%S").strftime("%d.%m.%Y u %H:%M:%S")] = f
        except: options[name] = f
    selected_file = st.selectbox("Previous scans:", list(options.keys()), label_visibility="collapsed")
    col_load, col_del = st.columns(2)
    with col_load:
        if st.button("📂 Load", use_container_width=True) and options[selected_file]:
            st.session_state.df_all = pd.read_csv(options[selected_file])
            st.session_state.is_running = False
            st.session_state.loaded_history = True
            st.session_state.last_run = os.path.getmtime(options[selected_file])
            st.rerun()
    with col_del:
        if st.button("🗑️ Delete", type="secondary", use_container_width=True) and options[selected_file]:
            os.remove(options[selected_file])
            if st.session_state.loaded_history: st.session_state.df_all = pd.DataFrame(); st.session_state.loaded_history = False
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
                    except: pass
                for f in OUTPUT_DIR.glob("*.pdf"):
                    try: os.remove(f)
                    except: pass
                st.session_state.df_all = pd.DataFrame()
                st.session_state.loaded_history = False
                st.session_state.is_running = False
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
                sl = st.empty()
                live_state = {"Wolt": 0, "Glovo": 0}
                df, hi, pdf, err_imgs = asyncio.run(scan_process(list_addresses, sl, live_ui_ph, live_state, generate_pdf, email_input, debug_mode, fast_mode))
                if not df.empty:
                    df.to_csv(OUTPUT_DIR / f"Detaljno_{timestamp()}.csv", index=False)
                live_ui_ph.empty()
                st.session_state.df_all = df
                st.session_state.df_history = hi
                st.session_state.pdf_files = pdf
                st.session_state.error_screenshots = err_imgs
                st.session_state.last_run = time.time()
                sl.empty()
            st.rerun()

    df = st.session_state.df_all
    if not df.empty:
        for col in ["Time_Num", "Delivery Time", "Rating", "Is_New"]:
            if col not in df.columns: df[col] = False if col == "Is_New" else (np.nan if "Num" in col else "-")

        if st.session_state.loaded_history: st.info("📂 **Viewing archived report.**")
        else: st.success(f"✅ Scan completed at: {datetime.datetime.fromtimestamp(st.session_state.last_run, LOCAL_TZ).strftime('%H:%M:%S')}")

        tab_dash, tab_list, tab_compare, tab_promo = st.tabs(["📊 Dashboard", "🔍 Restaurant List", "⚖️ Comparison", "🎁 Promos & Discounts"])
        unique_addresses = list(df["Address"].unique())

        with tab_dash:
            for adr in unique_addresses:
                st.markdown(f"<h3 style='color: #2c3e50;'>📍 {adr.upper()}</h3>", unsafe_allow_html=True)
                sd = df[df["Address"] == adr]
                w_total = len(sd[sd["Platform"] == "Wolt"])
                w_open = len(sd[(sd["Platform"] == "Wolt") & (sd["Status"] == "Open")])
                g_total = len(sd[sd["Platform"] == "Glovo"])
                g_open = len(sd[(sd["Platform"] == "Glovo") & (sd["Status"] == "Open")])
                st.markdown(f"""<div class="kpi-wrapper">
                    <div class="kpi-card kpi-wolt"><div class="kpi-title">Wolt Total</div><div class="kpi-value">{w_total}</div></div>
                    <div class="kpi-card kpi-wolt"><div class="kpi-title">Wolt Open</div><div class="kpi-value" style="color: #27ae60;">{w_open}</div></div>
                    <div class="kpi-card kpi-glovo"><div class="kpi-title">Glovo Total</div><div class="kpi-value">{g_total}</div></div>
                    <div class="kpi-card kpi-glovo"><div class="kpi-title">Glovo Open</div><div class="kpi-value" style="color: #27ae60;">{g_open}</div></div>
                </div>""", unsafe_allow_html=True)

            st.markdown("---")
            chart_addr = st.selectbox("📍 Filter Charts:", ["All addresses"] + unique_addresses, index=1 if len(unique_addresses) == 1 else 0)
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
                    with c_dt3: end_date = st.date_input("To date:", max_d, min_value=min_d, max_value=max_d)
                    with c_dt4: end_time = st.time_input("To time:", datetime.time(23, 59))
                    start_dt = pd.to_datetime(datetime.datetime.combine(start_date, start_time))
                    end_dt = pd.to_datetime(datetime.datetime.combine(end_date, end_time))
                    chart_hist = c_h.loc[(c_h['Datetime'] >= start_dt) & (c_h['Datetime'] <= end_dt)].copy()
                    st.plotly_chart(create_timeline_chart_ui(chart_hist, None, "History: Open restaurants", metric="Open", ylabel="Open restaurants"), use_container_width=True)
                    ch1, ch2 = st.columns(2)
                    with ch1: st.plotly_chart(create_timeline_chart_ui(chart_hist, None, "History: Avg delivery time", metric="Avg_Time", ylabel="Time (min)"), use_container_width=True)
                    with ch2: st.plotly_chart(create_timeline_chart_ui(chart_hist, None, "History: Restaurants on promo", metric="Promo_Count", ylabel="Promo count"), use_container_width=True)
                else: st.info("No history for selected address.")
            else: st.info("No historical data.")

        with tab_list:
            f1, f2, f3 = st.columns(3)
            with f1: fa = st.multiselect("📍 Address", df["Address"].unique(), df["Address"].unique())
            with f2: fp = st.multiselect("📱 Platform", df["Platform"].unique(), df["Platform"].unique())
            with f3: fs = st.multiselect("🚦 Status", ["Open", "Closed"], ["Open", "Closed"])
            c_filt1, c_filt2 = st.columns(2)
            with c_filt1: filt_new = st.checkbox("✨ Show only NEW restaurants")
            with c_filt2: filt_promo = st.checkbox("🔥 Show only restaurants ON PROMO")
            f_df = df[(df["Address"].isin(fa)) & (df["Platform"].isin(fp)) & (df["Status"].isin(fs))]
            if filt_new: f_df = f_df[f_df["Is_New"].isin([True, 'True', 'true', 1])]
            if filt_promo: f_df = f_df[f_df["Promo"] != "-"]
            disp_df = f_df.copy()
            disp_df["Badge"] = disp_df["Is_New"].apply(lambda x: "✨ NEW" if x in [True, 'True', 'true', 1] else "")
            disp_df = disp_df.drop(columns=['Naziv_Norm', 'Time_Num', 'Is_New'], errors='ignore')
            cols = ["Address", "Platform", "Name", "Status", "Rating", "Delivery Time", "Promo", "Badge", "Link"]
            disp_df = disp_df[[c for c in cols if c in disp_df.columns]]
            def style_rows(row):
                styles = [''] * len(row)
                if 'Status' in row.index:
                    styles[row.index.get_loc('Status')] = 'color: #27ae60; font-weight: bold;' if row['Status'] == 'Open' else 'color: #e74c3c; font-weight: bold;'
                if 'Promo' in row.index and row['Promo'] != '-':
                    styles[row.index.get_loc('Promo')] = 'color: #8e44ad; font-weight: bold;'
                return styles
            st.dataframe(disp_df.style.apply(style_rows, axis=1), use_container_width=True, hide_index=True, height=800,
                         column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open on site"), "Promo": st.column_config.TextColumn("Promo", width="large")})

        with tab_compare:
            c_up1, c_up2 = st.columns(2)
            with c_up1: filter_wolt_up = st.multiselect("🚦 Wolt filter:", ["Open", "Closed"], default=["Open", "Closed"], key="fw")
            with c_up2: filter_glovo_up = st.multiselect("🚦 Glovo filter:", ["Open", "Closed"], default=["Open", "Closed"], key="fg")
            df['Name_Norm'] = df['Name'].apply(normalize_name)
            compare_data = []
            for adr in unique_addresses:
                df_adr = df[df['Address'] == adr]
                common = set(df_adr[df_adr['Platform'] == 'Wolt']['Name_Norm']).intersection(set(df_adr[df_adr['Platform'] == 'Glovo']['Name_Norm']))
                for norm_name in common:
                    w_row = df_adr[(df_adr['Platform'] == 'Wolt') & (df_adr['Name_Norm'] == norm_name)].iloc[0]
                    g_row = df_adr[(df_adr['Platform'] == 'Glovo') & (df_adr['Name_Norm'] == norm_name)].iloc[0]
                    compare_data.append({"Address": adr, "Name (Wolt)": w_row['Name'], "Status Wolt": w_row['Status'], "Time Wolt": w_row['Delivery Time'], "Link Wolt": w_row['Link'], "Name (Glovo)": g_row['Name'], "Status Glovo": g_row['Status'], "Time Glovo": g_row['Delivery Time'], "Link Glovo": g_row['Link']})
            if compare_data:
                df_compare = pd.DataFrame(compare_data)
                df_compare = df_compare[(df_compare['Status Wolt'].isin(filter_wolt_up)) & (df_compare['Status Glovo'].isin(filter_glovo_up))]
                if not df_compare.empty:
                    st.dataframe(df_compare.style.map(lambda val: f'color: {"#27ae60" if val=="Open" else "#e74c3c"}; font-weight: bold;', subset=['Status Wolt', 'Status Glovo']),
                                 use_container_width=True, hide_index=True, height=800,
                                 column_config={"Link Wolt": st.column_config.LinkColumn("Link Wolt", display_text="Open Wolt"), "Link Glovo": st.column_config.LinkColumn("Link Glovo", display_text="Open Glovo")})
                else: st.info("No restaurants match these filters.")
            else: st.info("No common restaurants found on both platforms.")

        with tab_promo:
            unique_promos = set()
            for promo_str in c_df['Promo']:
                if pd.notna(promo_str) and str(promo_str) != "-":
                    for a in str(promo_str).split('\n'):
                        cl = a.replace("• ", "").strip()
                        if cl: unique_promos.add(cl)
            unique_promos = sorted(list(unique_promos))
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
            st.error("⚠️ ATTENTION: The script logged potential issues. Check them below:")
            for media_path in st.session_state.error_screenshots:
                if media_path.endswith('.webm'): st.video(media_path)
                elif media_path.endswith('.html'):
                    try:
                        with open(media_path, "rb") as f:
                            st.download_button(label=f"📥 Download HTML ({os.path.basename(media_path)})", data=f, file_name=os.path.basename(media_path), mime="text/html", key=media_path)
                    except: pass
                else: st.image(media_path, caption=os.path.basename(media_path), use_container_width=True)

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
