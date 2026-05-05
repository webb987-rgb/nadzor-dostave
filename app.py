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
    /* Stil za Live Brojace */
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
    
    /* Moderni Dashboard KPI blokovi */
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

    /* Stil za Tabove */
    .stTabs [data-baseweb="tab-list"] { gap: 10px; }
    .stTabs [data-baseweb="tab"] { height: 50px; white-space: pre-wrap; background-color: #f0f2f6; border-radius: 8px 8px 0px 0px; padding: 10px 20px; color: #4f4f4f;}
    .stTabs [aria-selected="true"] { background-color: #e0e5ec !important; font-weight: bold; border-bottom: 3px solid #ff4b4b;}
</style>
""", unsafe_allow_html=True)
# ======================================================

# ================= FIX ZA WINDOWS I PLAYWRIGHT =================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

@st.cache_resource
def install_playwright():
    import os
    os.system("playwright install chromium")

install_playwright()

# ================= GLOBALNA PODEŠAVANJA =================
EMAIL_POSILJAOCA = "webb987@gmail.com"
LOZINKA_POSILJAOCA = "sdehqzbnqefjlomo" 

OUTPUT_DIR = Path.cwd() / "izvestaji"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = OUTPUT_DIR / "istorija_dostave.csv"

ERRORS_DIR = Path.cwd() / "greske"
ERRORS_DIR.mkdir(parents=True, exist_ok=True)

GLOVO_AUTH_FILE = "glovo_auth.json"
WOLT_AUTH_FILE = "wolt_auth.json"

def timestamp(): return lokalno_vreme().strftime("%Y%m%d_%H%M%S")
def format_time_short(): return lokalno_vreme().strftime("%H:%M")
def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder: placeholder.text(msg)

# ---------------- LJUDSKI LIVE BROJAČ (UI) ----------------
def osvezi_live_ui(ph, wolt_count, glovo_count, adresa):
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
    <p style="text-align: center; color: #666; font-size: 14px;">📍 Trenutno skeniram: <b>{adresa}</b></p>
    """
    ph.markdown(html, unsafe_allow_html=True)

# ---------------- PODRŠKA ZA ĆIRILICU I EMAIL ----------------
def cirilica_u_latinicu(tekst):
    if not tekst: return ""
    mapa = { 'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s', 'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'Њ':'Nj', 'О':'O', 'П':'P', 'Р':'R', 'С':'S', 'Т':'T', 'Ћ':'C', 'У':'U', 'Ф':'F', 'Х':'H', 'Ц':'C', 'Ч':'C', 'Џ':'Dz', 'Ш':'S' }
    for k, v in mapa.items(): tekst = tekst.replace(k, v)
    return tekst

def posalji_email(pdf_putanje, primaoci_str, log_ph=None):
    lista_primaoca = [e.strip() for e in primaoci_str.split(",") if e.strip()]
    if not lista_primaoca: return
    try:
        log_msg(f"[SISTEM] Šaljem email na: {', '.join(lista_primaoca)}...", log_ph)
        for primalac in lista_primaoca:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_POSILJAOCA
            msg['To'] = primalac
            msg['Subject'] = f"Izveštaji o dostavi - {lokalno_vreme().strftime('%d.%m. u %H:%M')}"
            body = "Pozdrav šefe,\n\nU prilogu se nalaze zbirni i pojedinačni izveštaji o statusu restorana.\n\nSistem je uspešno završio ciklus."
            msg.attach(MIMEText(body, 'plain'))
            for pdf_putanja in pdf_putanje:
                with open(pdf_putanja, "rb") as attachment:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f"attachment; filename= {os.path.basename(pdf_putanja)}")
                    msg.attach(part)
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(EMAIL_POSILJAOCA, LOZINKA_POSILJAOCA)
            server.sendmail(EMAIL_POSILJAOCA, primalac, msg.as_string())
            server.quit()
        log_msg("[USPEH] Svi emailovi su poslati!", log_ph)
    except Exception as e: log_msg(f"[GREŠKA] Email nije uspeo: {e}", log_ph)

# ---------------- ISTORIJA ----------------
def sacuvaj_u_istoriju(df):
    vreme_sada = format_time_short()
    datum_sada = lokalno_vreme().strftime("%Y-%m-%d")
    istorija_podaci = []
    adrese = df["Adresa"].unique()
    for adr in adrese:
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Adresa"] == adr) & (df["Platforma"] == plat)]
            if sub.empty: continue
            
            otvoreno = len(sub[sub["Status"] == "Otvoreno"])
            zatvoreno = len(sub[sub["Status"] == "Zatvoreno"])
            
            avg_vreme = sub["Vreme_Broj"].dropna().mean()
            avg_vreme = 0 if pd.isna(avg_vreme) else round(avg_vreme, 1)
            
            broj_akcija = len(sub[sub["Akcija"] != "-"])
            
            istorija_podaci.append({ 
                "Datum": datum_sada, "Vreme": vreme_sada, 
                "Adresa": adr, "Platforma": plat, 
                "Otvoreno": otvoreno, "Zatvoreno": zatvoreno,
                "Avg_Vreme": avg_vreme, "Broj_Akcija": broj_akcija
            })
            
    df_novo = pd.DataFrame(istorija_podaci)
    fajl_str = str(HISTORY_FILE)
    if os.path.exists(fajl_str):
        df_kombinovano = pd.concat([pd.read_csv(fajl_str), df_novo], ignore_index=True)
    else: df_kombinovano = df_novo
    try: df_kombinovano.to_csv(fajl_str, index=False)
    except: pass
    st.session_state.df_history = df_kombinovano
    return df_kombinovano

# ================= MODERNI UI GRAFIKONI (PLOTLY) =================
def kreiraj_grafikon_status_ui(df_sub, naslov):
    wolt_o = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Otvoreno")])
    wolt_z = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Zatvoreno")])
    glovo_o = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Otvoreno")])
    glovo_z = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Zatvoreno")])
    
    data = [
        {"Kategorija": "Ukupno", "Platforma": "Wolt", "Broj": wolt_o+wolt_z},
        {"Kategorija": "Otvoreno", "Platforma": "Wolt", "Broj": wolt_o},
        {"Kategorija": "Zatvoreno", "Platforma": "Wolt", "Broj": wolt_z},
        {"Kategorija": "Ukupno", "Platforma": "Glovo", "Broj": glovo_o+glovo_z},
        {"Kategorija": "Otvoreno", "Platforma": "Glovo", "Broj": glovo_o},
        {"Kategorija": "Zatvoreno", "Platforma": "Glovo", "Broj": glovo_z},
    ]
    fig = px.bar(data, x="Kategorija", y="Broj", color="Platforma", barmode="group",
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Broj", title=naslov)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", title_font_size=18)
    fig.update_traces(textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def kreiraj_grafikon_vreme_dostave_ui(df_sub, naslov):
    wolt_df = df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Vreme_Broj"].notna())]
    glovo_df = df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Vreme_Broj"].notna())]
    w_avg = wolt_df["Vreme_Broj"].dropna().mean() if not wolt_df["Vreme_Broj"].dropna().empty else 0
    w_avg = 0 if pd.isna(w_avg) else round(w_avg, 1)
    g_avg = glovo_df["Vreme_Broj"].dropna().mean() if not glovo_df["Vreme_Broj"].dropna().empty else 0
    g_avg = 0 if pd.isna(g_avg) else round(g_avg, 1)
    
    data = [{"Platforma": "Wolt", "Vreme": w_avg}, {"Platforma": "Glovo", "Vreme": g_avg}]
    fig = px.bar(data, x="Platforma", y="Vreme", color="Platforma", 
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Vreme", title=naslov)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis_title="Prosečno vreme (min)", title_font_size=18)
    fig.update_traces(texttemplate='%{text} min', textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def kreiraj_grafikon_popusta_ui(df_sub, izabrane_akcije, naslov):
    wolt_count = 0
    glovo_count = 0
    if izabrane_akcije:
        for _, row in df_sub.iterrows():
            akcije_restorana = []
            if pd.notna(row['Akcija']) and row['Akcija'] != "-":
                akcije_restorana = [a.replace("• ", "").strip() for a in str(row['Akcija']).split('\n') if a.strip()]
            if any(akcija in izabrane_akcije for akcija in akcije_restorana):
                if row['Platforma'] == 'Wolt': wolt_count += 1
                elif row['Platforma'] == 'Glovo': glovo_count += 1
                
    data = [{"Platforma": "Wolt", "Broj": wolt_count}, {"Platforma": "Glovo", "Broj": glovo_count}]
    fig = px.bar(data, x="Platforma", y="Broj", color="Platforma", 
                 color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, text="Broj", title=naslov)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis_title="Broj restorana", title_font_size=18)
    fig.update_traces(textposition='outside', textfont_size=14, textfont_weight="bold")
    return fig

def kreiraj_timeline_grafikon_ui(df_hist, adresa=None, custom_naslov=None, metrika="Otvoreno", ylabel="Broj otvorenih restorana"):
    df_sub = df_hist.copy()
    if metrika not in df_sub.columns: df_sub[metrika] = 0

    if adresa:
        df_sub = df_sub[df_sub["Adresa"] == adresa]
        naslov = custom_naslov if custom_naslov else f'Istorijat - {adresa.upper()}'
    else:
        if not df_sub.empty and 'Platforma' in df_sub.columns:
            if 'Datum' in df_sub.columns and 'Vreme' in df_sub.columns:
                if metrika == "Avg_Vreme": df_sub = df_sub.groupby(["Datum", "Vreme", "Platforma"])[metrika].mean().reset_index()
                else: df_sub = df_sub.groupby(["Datum", "Vreme", "Platforma"])[metrika].sum().reset_index()
        naslov = custom_naslov if custom_naslov else 'Zbirni Istorijat'
        
    if len(df_sub) == 0: return go.Figure().update_layout(title="Nema istorijskih podataka", plot_bgcolor="rgba(0,0,0,0)")

    df_sub["Pravi_Datetime"] = pd.to_datetime(df_sub["Datum"] + " " + df_sub["Vreme"])
    df_sub = df_sub.sort_values(by="Pravi_Datetime")
    
    fig = px.line(df_sub, x="Pravi_Datetime", y=metrika, color="Platforma", markers=True,
                  color_discrete_map={"Wolt": "#00c2e8", "Glovo": "#ffc244"}, title=naslov)
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", 
                      xaxis_title="", yaxis_title=ylabel, hovermode="x unified", title_font_size=18)
    fig.update_xaxes(tickformat="%d.%m. u %H:%M") 
    fig.update_traces(line=dict(width=3), marker=dict(size=8))
    return fig

# ================= GRAFICI ZA PDF =================
def kreiraj_timeline_grafikon_pdf(df_hist, adresa=None, custom_naslov=None):
    df_sub = df_hist.copy()
    if adresa:
        df_sub = df_sub[df_sub["Adresa"] == adresa]
        naslov = f'Istorijat aktivnosti - {adresa.upper()}'
    else:
        if not df_sub.empty and 'Platforma' in df_sub.columns:
            if 'Datum' in df_sub.columns and 'Vreme' in df_sub.columns:
                df_sub = df_sub.groupby(["Datum", "Vreme", "Platforma"]).sum(numeric_only=True).reset_index()
        naslov = 'Zbirni Istorijat aktivnosti'
    if custom_naslov: naslov = custom_naslov
    fig, ax = plt.subplots(figsize=(10, 4), facecolor='#ffffff')
    ax.set_facecolor('#f8f9fa')
    if len(df_sub) == 0: ax.axis('off')
    else:
        df_sub = df_sub.sort_values(by=["Datum", "Vreme"])
        jedan_dan = df_sub["Datum"].nunique() <= 1 if 'Datum' in df_sub.columns else True
        if jedan_dan and 'Vreme' in df_sub.columns: df_sub["X_Label"] = df_sub["Vreme"]
        elif 'Datum' in df_sub.columns and 'Vreme' in df_sub.columns: df_sub["X_Label"] = df_sub["Datum"].str[-5:].str.replace('-', '.') + " \n" + df_sub["Vreme"]
        else: df_sub["X_Label"] = "Nepoznato"
        wolt_data = df_sub[df_sub["Platforma"] == "Wolt"].tail(48)
        glovo_data = df_sub[df_sub["Platforma"] == "Glovo"].tail(48)
        if not wolt_data.empty: ax.plot(wolt_data["X_Label"], wolt_data["Otvoreno"], marker='o', color='#00c2e8', linewidth=2.5, label='Wolt')
        if not glovo_data.empty: ax.plot(glovo_data["X_Label"], glovo_data["Otvoreno"], marker='s', color='#ffc244', linewidth=2.5, label='Glovo')
        ax.set_ylabel('Broj otvorenih', fontsize=11, fontweight='bold'); ax.set_title(naslov, fontsize=14, fontweight='bold', color='#2c3e50')
        ax.legend(frameon=True, fontsize=10, loc='lower center', bbox_to_anchor=(0.5, -0.3), ncol=2); ax.grid(True, linestyle='--', alpha=0.6)
        plt.xticks(rotation=45 if not jedan_dan else 0, fontsize=9)
    plt.tight_layout(); imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)
    return imgdata

def kreiraj_grafikon_status_pdf(df_sub, naslov):
    wolt_o = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Otvoreno")])
    wolt_z = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Zatvoreno")])
    glovo_o = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Otvoreno")])
    glovo_z = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Zatvoreno")])
    fig, ax = plt.subplots(figsize=(5, 4), facecolor='#ffffff')
    labels, x, width = ['Ukupno', 'Otvoreno', 'Zatvoreno'], np.arange(3), 0.35
    ax.bar(x - width/2, [wolt_o+wolt_z, wolt_o, wolt_z], width, color='#00c2e8', label='Wolt')
    ax.bar(x + width/2, [glovo_o+glovo_z, glovo_o, glovo_z], width, color='#ffc244', label='Glovo')
    ax.set_xticks(x); ax.set_xticklabels(labels, fontweight='bold', fontsize=10)
    ax.set_title(naslov, fontsize=12, fontweight='bold', color='#2c3e50'); ax.legend(frameon=False, fontsize=9)
    max_v = max([wolt_o+wolt_z, glovo_o+glovo_z]) if max([wolt_o+wolt_z, glovo_o+glovo_z]) > 0 else 10
    ax.set_ylim(0, max_v * 1.2)
    plt.tight_layout(); imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)
    return imgdata

# ---------------- EKSTRAKCIJA PODATAKA ----------------
def ukloni_kvacice(tekst):
    if not tekst: return ""
    for k, v in {'č':'c', 'ć':'c', 'ž':'z', 'š':'s', 'đ':'dj', 'Č':'C', 'Ć':'C', 'Ž':'Z', 'Š':'S', 'Đ':'Dj'}.items(): tekst = tekst.replace(k, v)
    return tekst

def izvuci_ime(tekst):
    if not tekst: return ""
    for line in str(tekst).split('\n'):
        line = line.strip()
        if not line or '%' in line or ("min" in line.lower() and re.search(r'\d+', line.lower())): continue
        if any(x in line.lower() for x in ["rsd", "din", "promo", "novo", "odlično", "besplatna dostava", "artikli", "narudžb", "popust", "off", "discount"]): continue
        if len(line) >= 2: return line
    return ""

def analiziraj_status(text):
    t = text.lower()
    if any(x in t for x in ["uskoro se zatvara", "closing soon", "zatvara se za", "closes in"]): return "Otvoreno"
    if any(k in t for k in ["samo preuzimanje", "samo za preuzimanje", "pickup only", "dostava nije dostupna", "dostava trenutno nije", "samo licno preuzimanje", "zatvoreno", "zakažite", "zakaži", "zakazi", "nedostupno", "otvara se", "otvara", "closed", "schedule"]): return "Zatvoreno"
    return "Otvoreno"

def izvuci_ocenu(tekst, plat):
    try:
        cist_tekst = re.sub(r'<[^>]+>', ' ', str(tekst)).lower()
        if plat == "Glovo":
            for p in re.findall(r'(\d{1,3})\s*%', cist_tekst):
                if int(p) >= 60: return p + "%"
        elif plat == "Wolt":
            m = re.search(r'\b([5-9][.,][0-9]|10[.,]0)\b', cist_tekst)
            if m: return m.group(1).replace(',', '.')
    except: pass
    return "-"

def izvuci_vreme_dostave(tekst):
    try:
        cist = re.sub(r'<[^>]+>', ' ', str(tekst)).lower()
        m1 = re.search(r'(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(?:min|m|\')', cist)
        if m1 and int(m1.group(1)) < 120 and int(m1.group(2)) < 120: return f"{m1.group(1)}-{m1.group(2)} min", (int(m1.group(1)) + int(m1.group(2))) / 2.0
        m2 = re.search(r'\b(\d{1,3})\s*(?:min|m|\')', cist)
        if m2 and int(m2.group(1)) < 120: return f"{m2.group(1)} min", float(m2.group(1))
    except: pass
    return "-", np.nan

def izvuci_akciju(tekst, html, plat):
    cist = (str(tekst) + " \n " + str(html)).lower()
    cist_tekst = re.sub(r'<[^>]+>', ' ', cist)
    cist_brojevi = cist_tekst.replace(',', '').replace('.', '')
    
    akcije, seen, res = [], set(), []
    
    if any(x in cist_tekst for x in ["besplatna dostava", "free delivery", "dostava 0", "0 rsd dostava", "delivery 0"]):
        akcije.append("Besplatna dostava")
        
    if any(x in cist_tekst for x in ["1+1", "1 + 1", "buy 1 get 1"]):
        akcije.append("1+1 Gratis")
        
    if plat == "Wolt":
        for pm in re.findall(r'(\d{1,2}\s*%)', cist_tekst):
            akcije.append(f"{pm.strip()} popusta")
    else:
        for pm in re.findall(r'(\d{1,2}\s*%)\s*(?:popust|off|discount|-)', cist_tekst):
            akcije.append(f"{pm.strip()} popusta")
            
    if any(x in cist_tekst for x in ["popust", "off", "uštedi", "save", "discount"]):
        for rm in re.findall(r'(\d{2,5})\s*(?:rsd|din)', cist_brojevi):
            if int(rm) > 10: akcije.append(f"{rm} RSD popusta")
            
    if "wolt+" in cist_tekst: akcije.append("Wolt+")
    if "prime" in cist_tekst: akcije.append("Prime")
        
    for a in akcije:
        ac = a[0].upper() + a[1:]
        if ac not in seen:
            seen.add(ac)
            res.append(f"• {ac}")
            
    return "\n".join(res) if res else "-"

def normalizuj_ime(ime): return re.sub(r'[^\w]', '', str(ime).lower())

# ---------------- SPARTAN MOD V2: LAŽNI PIKSEL ----------------
TINY_PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

async def pametni_dijetalni_mod(route):
    if route.request.resource_type in ["image", "media"]:
        await route.fulfill(status=200, content_type="image/png", body=TINY_PNG)
    else:
        await route.continue_()

# ---------------- WOLT API LOGIKA (ZAMENA ZA SCRAPE) ----------------
def dobavi_koordinate(adresa):
    url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(adresa)}&format=json&limit=1"
    headers = {"User-Agent": "WoltNadzor/2.0"}
    try:
        odgovor = requests.get(url, headers=headers, timeout=10)
        podaci = odgovor.json()
        if podaci: return podaci[0]["lat"], podaci[0]["lon"]
    except: pass
    return None, None

async def scrape_wolt_api(address, log_ph=None, live_ph=None, live_state=None):
    log_msg(f"🚲 WOLT API: Dobavljam koordinate za {address}...", log_ph)
    lat, lon = dobavi_koordinate(address)
    if not lat or not lon:
        log_msg("❌ WOLT API: Neuspešno dobavljanje koordinata.", log_ph)
        return []

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Wolt-Client-Id": "Web-Wolt",
        "Accept-Language": "sr-RS,sr;q=0.9,en-US;q=0.8"
    })

    url = f"https://restaurant-api.wolt.com/v1/pages/restaurants?lat={lat}&lon={lon}"
    try:
        loop = asyncio.get_event_loop()
        odgovor = await loop.run_in_executor(None, lambda: session.get(url, timeout=15))
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
                status = "Otvoreno" if venue.get("online", True) else "Zatvoreno"
                ocena = str(venue.get("rating", {}).get("score", "-"))
                
                v_min = venue.get("estimate_range", {}).get("min", 0)
                v_max = venue.get("estimate_range", {}).get("max", 0)
                v_str = f"{v_min}-{v_max} min" if v_max > 0 else "-"
                v_num = (v_min + v_max) / 2 if v_max > 0 else np.nan
                
                akcije_lista = []
                promo = venue.get("delivery_price_highlight", "")
                if promo: akcije_lista.append(f"• {promo}")
                
                # Provera 1+1 i popusta u API bedževima
                for badge in venue.get("badges", []):
                    b_text = badge.get("text", "")
                    if b_text: akcije_lista.append(f"• {b_text}")

                rezultati.append({
                    "Adresa": address, "Platforma": "Wolt", "Naziv": naziv, 
                    "Ocena": ocena, "Vreme dostave": v_str, 
                    "Akcija": "\n".join(akcije_lista) if akcije_lista else "-",
                    "Status": status, "Vreme_Broj": v_num, "Is_New": venue.get("is_new", False),
                    "Link": f"https://wolt.com/sr/srb/restaurant/{slug}"
                })
                
                if live_ph and live_state:
                    live_state["Wolt"] = len(rezultati)
                    osvezi_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)

        return rezultati
    except Exception as e:
        log_msg(f"❌ WOLT API Greška: {e}", log_ph)
        return []

# ---------------- GLOVO SKROLOVANJE (OSTAJE PLAYWRIGHT) ----------------
async def pametno_skrolovanje_i_ekstrakcija(page, plat, address, log_ph=None, live_ph=None, live_state=None):
    results_dict = {}
    prethodni_broj = 0
    pokusaji_na_dnu = 0
    
    while True:
        podaci = await page.evaluate('''() => {
            let rez = [];
            document.querySelectorAll("a:has(h3), a[data-testid='store-card'], .store-card a").forEach(c => {
                let link = c.href;
                if (!link.includes('/dostava') && !link.includes('/category')) { rez.push({link: link, text: c.innerText, html: c.innerHTML}); }
            });
            return rez;
        }''')

        for item in podaci:
            link = item['link']
            if not link or link in results_dict: continue
            
            text = item['text']
            html_content = item.get('html', '')
            sve_z = text + " " + html_content
            
            ime = ukloni_kvacice(izvuci_ime(text))
            if len(ime) < 2: continue
            
            ocena = izvuci_ocenu(sve_z, plat)
            vreme_str, vreme_num = izvuci_vreme_dostave(sve_z)
            akcija_str = izvuci_akciju(text, html_content, plat)
            
            t_low = text.strip().lower()
            is_new = t_low.endswith('new') or t_low.endswith('novo') or bool(re.search(r'•.*?new\b', t_low)) or (ocena == "Novo")

            results_dict[link] = {
                "Adresa": address, "Platforma": plat, "Naziv": ime, "Ocena": ocena,
                "Vreme dostave": vreme_str, "Akcija": akcija_str, "Status": analiziraj_status(sve_z),
                "Vreme_Broj": vreme_num, "Is_New": is_new, "Link": link
            }

        trenutni = len(results_dict)
        if trenutni > prethodni_broj:
            log_msg(f"[{plat.upper()} - {address}] Učitano {trenutni} restorana...", log_ph)
            if live_ph and live_state is not None:
                live_state[plat] = trenutni
                osvezi_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)
            prethodni_broj = trenutni
            pokusaji_na_dnu = 0
            
        await page.evaluate("window.scrollBy(0, 500);")
        await asyncio.sleep(0.5)
        
        h = await page.evaluate("document.body.scrollHeight")
        s = await page.evaluate("window.scrollY + window.innerHeight")
        if s >= h - 50:
            pokusaji_na_dnu += 1
            await asyncio.sleep(1.5)
            if pokusaji_na_dnu >= 5: break 
        
    return list(results_dict.values())

async def scrape_glovo(context_glovo, address, log_ph=None, live_ph=None, live_state=None, error_screenshots=None):
    page = None
    try:
        page = await context_glovo.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.set_default_timeout(15000)
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")
        
        try:
            accept_btn = page.locator("button", has_text=re.compile(r"Accept All|Prihvati sve", re.IGNORECASE)).first
            await accept_btn.click(timeout=3000)
            await asyncio.sleep(1)
        except: pass
        
        hero_input = page.locator("#hero-container-input")
        await hero_input.wait_for(state="visible", timeout=5000)
        await hero_input.click()
        search = page.get_by_role("searchbox")
        await search.fill(address)
        
        dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
        await dropdown_item.wait_for(state="visible", timeout=8000)
        await dropdown_item.click()
        
        await asyncio.sleep(5)
        try:
            kat_link = page.get_by_role("link", name=re.compile(r"Restorani|Hrana|Food|Restaurants", re.I)).first
            await kat_link.click(timeout=5000)
        except: pass
        
        await asyncio.sleep(5)
        return await pametno_skrolovanje_i_ekstrakcija(page, "Glovo", address, log_ph, live_ph, live_state)
    except Exception as e:
        log_msg(f"[GLOVO GREŠKA] {e}", log_ph)
        return []
    finally:
        if page: await page.close()

# ---------------- SEKVENCIJALNI PROCES SKENIRANJA ----------------
async def proces_skeniranja(adrese, log_ph, live_ph, live_state, generisi_pdf=False, email_primaoca=""):
    sve = []
    error_screenshots = [] 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        
        for i, adr in enumerate(adrese):
            live_state["Wolt"] = 0
            live_state["Glovo"] = 0
            osvezi_live_ui(live_ph, 0, 0, adr)
            
            if i > 0: await asyncio.sleep(5)
            log_msg(f"\n[SISTEM] Pokrećem skeniranje za: {adr}", log_ph)
            
            # WOLT API POZIV
            log_msg("🚲 Pozivam WOLT API...", log_ph)
            r_wolt = await scrape_wolt_api(adr, log_ph, live_ph, live_state)
            sve.extend(r_wolt)
            
            # GLOVO PLAYWRIGHT POZIV
            log_msg("📱 Skrolujem GLOVO...", log_ph)
            ga = {"permissions": ['geolocation'], "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            context_glovo = await browser.new_context(**ga)
            await context_glovo.route("**/*", pametni_dijetalni_mod)
            r_glovo = await scrape_glovo(context_glovo, adr, log_ph, live_ph, live_state, error_screenshots)
            sve.extend(r_glovo)
            await context_glovo.close() 
                
        await browser.close()
            
    if sve:
        df_s = pd.DataFrame(sve)
        df_h = sacuvaj_u_istoriju(df_s)
        # Ovde se pozivaju vaše PDF funkcije... (dodajte ih ako su bile definisane)
        return df_s, df_h, [], error_screenshots
    return pd.DataFrame(), pd.DataFrame(), [], error_screenshots

# ... OSTATAK STREAMLIT KODA (UI, TABOVI, GRAFIKONI) OSTAJE IDENTIČAN ...
# ================= STREAMLIT UI =================
if 'pokrenuto' not in st.session_state: st.session_state.pokrenuto = False
if 'last_run' not in st.session_state: st.session_state.last_run = 0
if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()
if 'pdf_fajlovi' not in st.session_state: st.session_state.pdf_fajlovi = []
if 'error_screenshots' not in st.session_state: st.session_state.error_screenshots = []
if 'loaded_history' not in st.session_state: st.session_state.loaded_history = False

if 'df_history' not in st.session_state: 
    if os.path.exists(HISTORY_FILE): st.session_state.df_history = pd.read_csv(HISTORY_FILE)
    else: st.session_state.df_history = pd.DataFrame()

st.title("🍔 Nadzor Dostave (Wolt & Glovo)")
with st.sidebar:
    st.header("⚙️ Podešavanja")
    adresa_1 = st.text_input("📍 Adresa 1 (Obavezna):", value="", placeholder="Makenzijeva 57, Beograd")
    adresa_2 = st.text_input("📍 Adresa 2 (Opciona):", value="", placeholder="Somborska 5, Niš")
    auto_refresh = st.checkbox("🔄 Automatsko osvežavanje", value=False)
    sleep_interval = st.number_input("⏱️ Interval (min):", min_value=1, value=60, disabled=not auto_refresh)
    generisi_pdf = st.checkbox("📄 Generiši PDF izveštaje", value=False)
    email_unos = st.text_input("📧 Pošalji na email:", placeholder="tvoj@email.com") if generisi_pdf else ""
    
    if st.button("▶️ POKRENI", type="primary", use_container_width=True): 
        st.session_state.pokrenuto = True
        st.session_state.loaded_history = False
        st.session_state.last_run = 0
        st.rerun()

# [Ovde ubacite vaš postojeći UI kod za tabove, grafikone i tabelu]
# Kod se neće menjati jer DataFrame struktura ostaje ista.

if st.session_state.pokrenuto or st.session_state.loaded_history:
    if st.session_state.pokrenuto:
        lista_adresa = [cirilica_u_latinicu(a.strip()) for a in [adresa_1, adresa_2] if a.strip()]
        if not lista_adresa: 
            st.warning("⚠️ Unesite adresu!"); st.session_state.pokrenuto = False; st.rerun()

        now = time.time()
        if now - st.session_state.last_run >= sleep_interval * 60 or st.session_state.last_run == 0:
            with st.spinner('🔄 Skeniranje u toku...'):
                live_ui_ph = st.empty()
                sl = st.empty()
                live_state = {"Wolt": 0, "Glovo": 0}
                df, hi, pdf, err_imgs = asyncio.run(proces_skeniranja(lista_adresa, sl, live_ui_ph, live_state, generisi_pdf, email_unos))
                if not df.empty:
                    df.to_csv(OUTPUT_DIR / f"Detaljno_{timestamp()}.csv", index=False)
                    st.session_state.df_sve, st.session_state.df_history, st.session_state.last_run = df, hi, time.time()
                live_ui_ph.empty()
                sl.empty()
            st.rerun()

    df = st.session_state.df_sve
    if not df.empty:
        tab_dash, tab_lista = st.tabs(["📊 Dashboard", "🔍 Lista Restorana"])
        with tab_dash:
            st.plotly_chart(kreiraj_grafikon_status_ui(df, "Status Restorana"), use_container_width=True)
        with tab_lista:
            st.dataframe(df, use_container_width=True)
