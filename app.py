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

# Importi za PDF koji su bili u tvojoj skripti
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Streamlit Page Config
st.set_page_config(page_title="Delivery Monitor", page_icon="🍔", layout="wide")

# ================= MODERN CSS DESIGN =================
st.markdown("""
<style>
    /* Live Counters Style */
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
    
    /* Modern Dashboard KPI blocks */
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

    /* Tabs Style */
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 8px 8px 0px 0px; padding: 10px 20px; color: #4f4f4f;}
    .stTabs [aria-selected="true"] { background-color: #e0e5ec !important; font-weight: bold; border-bottom: 3px solid #ff4b4b;}
</style>
""", unsafe_allow_html=True)

# ================= WINDOWS & PLAYWRIGHT FIX =================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

@st.cache_resource
def install_playwright():
    import os
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
    mapa = { 'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s', 'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'њ':'nj', 'О':'O', 'П':'P', 'Р':'R', 'С':'S', 'Т':'T', 'Ћ':'C', 'У':'U', 'Ф':'F', 'Х':'H', 'Ц':'C', 'Ч':'C', 'Џ':'Dz', 'Ш':'S' }
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
    except Exception as e: log_msg(f"[ERROR] Email failed: {e}", log_ph)

# ---------------- HISTORY ----------------
def save_to_history(df):
    time_now = format_time_short()
    date_now = local_time().strftime("%Y-%m-%d")
    history_data = []
    addresses = df["Address"].unique()
    for adr in addresses:
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Address"] == adr) & (df["Platform"] == plat)]
            if sub.empty: continue
            
            opened = len(sub[sub["Status"] == "Open"])
            closed = len(sub[sub["Status"] == "Closed"])
            
            avg_time = sub["Time_Num"].dropna().mean()
            avg_time = 0 if pd.isna(avg_time) else round(avg_time, 1)
            
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
    else: df_combined = df_new
    try: df_combined.to_csv(file_str, index=False)
    except: pass
    st.session_state.df_history = df_combined
    return df_combined

# ================= MODERN UI CHARTS (PLOTLY) =================
def create_status_chart_ui(df_sub, title):
    wolt_o = len(df_sub[(df_sub["Platform"] == "Wolt") & (df_sub["Status"] == "Open")])
    wolt_z = len(df_sub[(df_sub["Platform"] == "Wolt") & (df_sub["Status"] == "Closed")])
    glovo_o = len(df_sub[(df_sub["Platform"] == "Glovo") & (df_sub["Status"] == "Open")])
    glovo_z = len(df_sub[(df_sub["Platform"] == "Glovo") & (df_sub["Status"] == "Closed")])
    
    data = [
        {"Category": "Total", "Platform": "Wolt", "Count": wolt_o+wolt_z},
        {"Category": "Open", "Platform": "Wolt", "Count": wolt_o},
        {"Category": "Closed", "Platform": "Wolt", "Count": wolt_z},
        {"Category": "Total", "Platform": "Glovo", "Count": glovo_o+glovo_z},
        {"Category": "Open", "Platform": "Glovo", "Count": glovo_o},
        {"Category": "Closed", "Platform": "Glovo", "Count": glovo_z},
    ]
    fig = px.bar(data, x="Category", y="Count", color="Platform", barmode="group",
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Count", title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", title_font_size=18)
    fig.update_traces(textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def create_delivery_time_chart_ui(df_sub, title):
    wolt_df = df_sub[(df_sub["Platform"] == "Wolt") & (df_sub["Time_Num"].notna())]
    glovo_df = df_sub[(df_sub["Platform"] == "Glovo") & (df_sub["Time_Num"].notna())]
    w_avg = wolt_df["Time_Num"].dropna().mean() if not wolt_df["Time_Num"].dropna().empty else 0
    w_avg = 0 if pd.isna(w_avg) else round(w_avg, 1)
    g_avg = glovo_df["Time_Num"].dropna().mean() if not glovo_df["Time_Num"].dropna().empty else 0
    g_avg = 0 if pd.isna(g_avg) else round(g_avg, 1)
    
    data = [{"Platform": "Wolt", "Time": w_avg}, {"Platform": "Glovo", "Time": g_avg}]
    fig = px.bar(data, x="Platform", y="Time", color="Platform", 
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Time", title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis_title="Average time (min)", title_font_size=18)
    fig.update_traces(texttemplate='%{text} min', textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def create_promo_chart_ui(df_sub, selected_promos, title):
    wolt_count = 0
    glovo_count = 0
    if selected_promos:
        for _, row in df_sub.iterrows():
            restaurant_promos = []
            if pd.notna(row['Promo']) and row['Promo'] != "-":
                restaurant_promos = [a.replace("• ", "").strip() for a in str(row['Promo']).split('\n') if a.strip()]
            if any(promo in selected_promos for promo in restaurant_promos):
                if row['Platform'] == 'Wolt': wolt_count += 1
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
        title = custom_title if custom_title else f'History - {address.upper()}'
    else:
        if not df_sub.empty and 'Platform' in df_sub.columns:
            if 'Date' in df_sub.columns and 'Time' in df_sub.columns:
                if metric == "Avg_Time": df_sub = df_sub.groupby(["Date", "Time", "Platform"])[metric].mean().reset_index()
                else: df_sub = df_sub.groupby(["Date", "Time", "Platform"])[metric].sum().reset_index()
        title = custom_title if custom_title else 'Summary History'
        
    if len(df_sub) == 0: return go.Figure().update_layout(title="No history data", plot_bgcolor="rgba(0,0,0,0)")

    df_sub["Real_Datetime"] = pd.to_datetime(df_sub["Date"] + " " + df_sub["Time"])
    df_sub = df_sub.sort_values(by="Real_Datetime")
    
    fig = px.line(df_sub, x="Real_Datetime", y=metric, color="Platform", markers=True,
                  color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, title=title)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", 
                      xaxis_title="", yaxis_title=ylabel, hovermode="x unified", title_font_size=18)
    fig.update_xaxes(tickformat="%d.%m. u %H:%M") 
    fig.update_traces(line=dict(width=3), marker=dict(size=8))
    return fig

# ---------------- DATA EXTRACTION ----------------
def remove_accents(text):
    if not text: return ""
    for k, v in {'č':'c', 'ć':'c', 'ž':'z', 'š':'s', 'đ':'dj', 'Č':'C', 'Ć':'C', 'Ž':'Z', 'Š':'S', 'Đ':'Dj'}.items(): text = text.replace(k, v)
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

# ================= REKURZIVNO ČUPANJE JSON TEKSTOVA I PAMETNI PROMO EXTRACT =================
def get_all_json_strings(obj):
    if isinstance(obj, dict):
        return " ".join(get_all_json_strings(v) for v in obj.values() if v is not None)
    elif isinstance(obj, list):
        return " ".join(get_all_json_strings(i) for i in obj if i is not None)
    elif isinstance(obj, str):
        return obj
    return ""

# UNAPREĐEN PROMO EXTRACTOR KOJI KOMBINUJE TVOJU LOGIKU I BANNER LOGIKU
def extract_promo(text, html_content, plat):
    clean_text = (str(text) + " \n " + str(html_content)).lower()
    clean_text = re.sub(r'<[^>]+>', ' ', clean_text)
    clean_numbers = re.sub(r'(?<=\d)[.,](?=\d)', '', clean_text)
    
    promos, seen, res = [], set(), []

    if plat == "Glovo" and html_content:
        glovo_tags = re.findall(r'data-style="promotion"[^>]*>([^<]+)<', str(html_content))
        for gp in glovo_tags:
            promos.append(gp.strip())
    
    if any(x in clean_text for x in ["besplatna dostava", "free delivery", "dostava 0", "0 rsd dostava", "delivery 0", "besplatna"]):
        promos.append("Free delivery")
        
    if any(x in clean_text for x in ["1+1", "1 + 1", "buy 1 get 1"]):
        promos.append("1+1 Free")
        
    if plat == "Wolt":
        for pm in re.findall(r'(\d{1,3}\s*%\s*[^.\n]*)', clean_text):
            promos.append(pm.strip())
        for rsd in re.findall(r'((?:rsd|din)\s*\d+\s*(?:off|popust|iznad|over|discount)[^.\n]*)', clean_numbers):
            promos.append(rsd.strip())
    else:
        for pm in re.findall(r'(\d{1,2}\s*%)\s*(?:popust|off|discount|-)', clean_text):
            promos.append(f"{pm.strip()} discount")
        for rm in re.findall(r'(\d{2,5})\s*(?:rsd|din)', clean_numbers):
            if int(rm) > 10: promos.append(f"{rm} RSD discount")
            
    if "wolt+" in clean_text: promos.append("Wolt+")
    if "prime" in clean_text: promos.append("Prime")
        
    for a in promos:
        ac = a.replace("rsd", "RSD").replace("din", "RSD").strip()
        ac = ac[0].upper() + ac[1:]
        if ac not in seen:
            seen.add(ac)
            res.append(f"• {ac}")
            
    return "\n".join(res) if res else "-"

def normalize_name(name): return re.sub(r'[^\w]', '', str(name).lower())

# ---------------- SPARTAN MODE ----------------
TINY_PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

async def smart_diet_mode(route):
    if route.request.resource_type in ["image", "media"]:
        await route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
    else:
        await route.continue_()

# ---------------- SMART SCROLLING (GLOVO) ----------------
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
            
            is_new = "novo" in text.lower() or "new" in text.lower()

            results_dict[link] = {
                "Address": address, "Platform": plat, "Name": name, "Rating": rating,
                "Delivery Time": time_str, "Promo": promo_str, "Status": analyze_status(all_text),
                "Time_Num": time_num, "Is_New": is_new, "Link": link
            }

        current = len(results_dict)
        if current > prev_count:
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

# ---------------- WOLT API HIBRID (IZMENJEN DA ČITA SVE POPUSTE I NOVO) ----------------
async def scrape_wolt_api(context_wolt, address, log_ph=None, live_ph=None, live_state=None, error_screenshots=None, debug_mode=False):
    results_dict = {}
    page = None
    try:
        import urllib.request, json
        geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address + ', Serbia')}&format=json&limit=1"
        req = urllib.request.Request(geo_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as res: geo_data = json.loads(res.read().decode())
        if not geo_data: return []
        lat, lon = geo_data[0]["lat"], geo_data[0]["lon"]

        page = await context_wolt.new_page()
        await page.goto("https://wolt.com/sr/srb")
        
        wolt_data = await page.evaluate(f'async () => {{ let r = await fetch("https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}"); return r.ok ? await r.json() : null; }}')
        if not wolt_data: return []

        for section in wolt_data.get("sections", []):
            for item in section.get("items", []):
                venue = item.get("venue")
                if not venue: continue
                slug = venue.get("slug")
                results_dict[slug] = {
                    "Address": address, "Platform": "Wolt", "Name": remove_accents(venue.get("name")),
                    "Rating": str(venue.get("rating", {}).get("score", "-")),
                    "Status": "Open" if venue.get("online") else "Closed",
                    "Link": f"https://wolt.com/sr/srb/city/restaurant/{slug}",
                    "Is_New": False, "Promo": "-", "Time_Num": np.nan, "Delivery Time": "-"
                }

        # KLJUČNA IZMENA: MUNJEVITI FETCH ZA BANERE I NOVO
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
                
                # Skupljanje svih popusta iz "discounts" liste (Banneri)
                all_banners = []
                for disc in v_raw.get("discounts", []):
                    txt = disc.get("banner", {}).get("formatted_text")
                    if txt: all_banners.append(f"• {txt}")
                
                # Provera "tags" za precizan Novo status
                is_new = "new-restaurant" in v_raw.get("tags", []) or data.get("is_new") is True
                
                # Precizno vreme
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
    except: return []
    finally:
        if page: await page.close()

# ---------------- GLOVO SCRAPER (Originalna navigacija) ----------------
async def scrape_glovo(context_glovo, address, log_ph=None, live_ph=None, live_state=None, error_screenshots=None, debug_mode=False):
    page = None
    try:
        page = await context_glovo.new_page()
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")
        try:
            accept_btn = page.locator("button", has_text=re.compile(r"Accept All|Prihvati sve", re.IGNORECASE)).first
            await accept_btn.click(timeout=4000)
        except: pass
        
        # Originalna hero logika za adresu
        try:
            hero_input = page.locator("#hero-container-input")
            await hero_input.click(timeout=5000)
            search = page.get_by_role("searchbox")
            await search.fill(address)
            dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
            await dropdown_item.click(timeout=8000)
        except:
            # Header fallback
            header_btn = page.locator('header div[role="button"]').first
            await header_btn.click(timeout=6000)
            search_modal = page.get_by_role("searchbox").last
            await search_modal.fill(address)
            dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
            await dropdown_item.click(timeout=8000)

        await asyncio.sleep(6)
        rez = await smart_scroll_and_extract(page, "Glovo", address, log_ph, live_ph, live_state)
        return rez
    except: return []
    finally:
        if page: await page.close()

# ---------------- PROCES SKENIRANJA (Originalni proces) ----------------
async def scan_process(addresses, log_ph, live_ph, live_state, generate_pdf=False, recipient_email="", debug_mode=False):
    all_data = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        wa = {"permissions": ['geolocation'], "user_agent": "Mozilla/5.0"}
        ga = {"permissions": ['geolocation'], "user_agent": "Mozilla/5.0"}
            
        for i, adr in enumerate(addresses):
            live_state["Wolt"] = 0
            live_state["Glovo"] = 0
            refresh_live_ui(live_ph, 0, 0, adr)
            
            log_msg(f"📱 Scrolling GLOVO for {adr}...", log_ph)
            context_glovo = await browser.new_context(**ga)
            all_data.extend(await scrape_glovo(context_glovo, adr, log_ph, live_ph, live_state))
            await context_glovo.close() 
            
            log_msg(f"🚲 Calling WOLT for {adr}...", log_ph)
            context_wolt = await browser.new_context(**wa)
            all_data.extend(await scrape_wolt_api(context_wolt, adr, log_ph, live_ph, live_state))
            await context_wolt.close() 
                
        await browser.close()
            
    if all_data:
        df_s = pd.DataFrame(all_data)
        df_h = save_to_history(df_s)
        return df_s, df_h, [], []
    return pd.DataFrame(), pd.DataFrame(), [], []

# ================= STREAMLIT UI (Originalni interfejs) =================
if 'is_running' not in st.session_state: st.session_state.is_running = False
if 'last_run' not in st.session_state: st.session_state.last_run = 0
if 'df_all' not in st.session_state: st.session_state.df_all = pd.DataFrame()
if 'df_history' not in st.session_state: 
    if os.path.exists(HISTORY_FILE): st.session_state.df_history = pd.read_csv(HISTORY_FILE)
    else: st.session_state.df_history = pd.DataFrame()

with st.sidebar:
    st.header("⚙️ Settings")
    address_1 = st.text_input("📍 Address 1 (Required):", value="")
    address_2 = st.text_input("📍 Address 2 (Optional):", value="")
    if st.button("▶️ START", type="primary"):
        st.session_state.is_running = True
        st.rerun()
    if st.button("⏹️ STOP"):
        st.session_state.is_running = False
        st.rerun()

if st.session_state.is_running:
    list_addresses = [cyrillic_to_latin(a.strip()) for a in [address_1, address_2] if a.strip()]
    if list_addresses:
        with st.spinner('🔄 Searching...'):
            live_ui_ph = st.empty()
            sl = st.empty()
            live_state = {"Wolt": 0, "Glovo": 0}
            df, hi, pdf, err = asyncio.run(scan_process(list_addresses, sl, live_ui_ph, live_state))
            st.session_state.df_all = df
            st.session_state.is_running = False
            st.rerun()

# PRIKAZ REZULTATA (Originalni tabovi)
df = st.session_state.df_all
if not df.empty:
    tab_dash, tab_list, tab_compare, tab_promo = st.tabs(["📊 Dashboard", "🔍 Restaurant List", "⚖️ Comparison", "🎁 Promos"])
    
    with tab_dash:
        st.plotly_chart(create_status_chart_ui(df, "Status Comparison"), use_container_width=True)
        st.plotly_chart(create_delivery_time_chart_ui(df, "Average Delivery Time"), use_container_width=True)
    
    with tab_list:
        st.dataframe(df, use_container_width=True, hide_index=True)
    
    with tab_promo:
        unique_promos = set()
        for p in df['Promo']:
            if p != "-":
                for a in str(p).split('\n'): unique_promos.add(a.replace("• ", "").strip())
        sel = st.multiselect("Filter promos:", sorted(list(unique_promos)), default=list(unique_promos))
        st.plotly_chart(create_promo_chart_ui(df, sel, "Promo Distribution"), use_container_width=True)
