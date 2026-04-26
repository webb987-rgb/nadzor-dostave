import asyncio
import datetime
import os
import platform
import subprocess
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from io import BytesIO
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import time
import streamlit as st
import sys

# PODEŠAVANJE LOKALNOG VREMENA (Rešava problem 5 ujutru na Cloud serverima)
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

# Konfiguracija Streamlit stranice (MORA BITI NA POČETKU)
st.set_page_config(page_title="Nadzor Dostave", page_icon="🍔", layout="wide")

# ================= FIX ZA WINDOWS I PLAYWRIGHT =================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
# ===============================================================

# ================= INSTALACIJA BROWSERA NA CLOUD-u =================
@st.cache_resource
def install_playwright():
    import os
    os.system("playwright install chromium")

install_playwright()
# ===================================================================

# ================= GLOBALNA PODEŠAVANJA =================
EMAIL_POSILJAOCA = "webb987@gmail.com"
LOZINKA_POSILJAOCA = "sdehqzbnqefjlomo" 

OUTPUT_DIR = Path.cwd() / "izvestaji"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = OUTPUT_DIR / "istorija_dostave.csv"

# NOVA FASCIKLA ZA SLIKE GREŠAKA
ERRORS_DIR = Path.cwd() / "greske"
ERRORS_DIR.mkdir(parents=True, exist_ok=True)

# FAJLOVI ZA VIP PROPUSNICE
GLOVO_AUTH_FILE = "glovo_auth.json"
WOLT_AUTH_FILE = "wolt_auth.json"
# ========================================================

def timestamp():
    return lokalno_vreme().strftime("%Y%m%d_%H%M%S")

def format_time_short():
    return lokalno_vreme().strftime("%H:%M")

def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder:
        placeholder.text(msg)

# ---------------- PODRŠKA ZA ĆIRILICU I SPECIJALNA SLOVA ----------------
def cirilica_u_latinicu(tekst):
    if not tekst: return ""
    mapa = {
        'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s',
        'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'њ':'nj', 'О':'O', 'П':'P', 'р':'r', 'С':'S', 'Т':'T', 'Ћ':'C', 'у':'u', 'Ф':'F', 'Х':'H', 'ц':'c', 'ч':'c', 'џ':'dz', 'Ш':'S'
    }
    for k, v in mapa.items():
        tekst = tekst.replace(k, v)
    return tekst

# ---------------- EMAIL FUNKCIJA ----------------
def posalji_email(pdf_putanje, primaoci_str, log_ph=None):
    lista_primaoca = [e.strip() for e in primaoci_str.split(",") if e.strip()]
    if not lista_primaoca: 
        return
    try:
        log_msg(f"[SISTEM] Šaljem email na: {', '.join(lista_primaoca)}...", log_ph)
        for primalac in lista_primaoca:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_POSILJAOCA
            msg['To'] = primalac
            msg['Subject'] = f"Izveštaji o dostavi - {lokalno_vreme().strftime('%d.%m. u %H:%M')}"
            body = "Pozdrav šefe,\n\nU prilogu se nalaze zbirni i pojedinačni izveštaji o statusu restorana na platformama Wolt i Glovo.\n\nSistem je uspješno završio ciklus."
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
            text = msg.as_string()
            server.sendmail(EMAIL_POSILJAOCA, primalac, text)
            server.quit()
        log_msg("[USPEH] Svi emailovi su uspješno poslati!", log_ph)
    except Exception as e:
        log_msg(f"[GREŠKA] Slanje emaila nije uspjelo: {e}", log_ph)

# ---------------- ISTORIJA I GRAFICI ----------------
def sacuvaj_u_istoriju(df):
    vreme_sada = format_time_short()
    datum_sada = lokalno_vreme().strftime("%Y-%m-%d")
    istorija_podaci = []
    adrese = df["Adresa"].unique()
    for adr in adrese:
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Adresa"] == adr) & (df["Platforma"] == plat)]
            otvoreno = len(sub[sub["Status"] == "Otvoreno"])
            zatvoreno = len(sub[sub["Status"] == "Zatvoreno"])
            istorija_podaci.append({
                "Datum": datum_sada, "Vreme": vreme_sada, 
                "Adresa": adr, "Platforma": plat, 
                "Otvoreno": otvoreno, "Zatvoreno": zatvoreno
            })
    df_novo = pd.DataFrame(istorija_podaci)
    fajl_str = str(HISTORY_FILE)
    if os.path.exists(fajl_str):
        df_staro = pd.read_csv(fajl_str)
        df_kombinovano = pd.concat([df_staro, df_novo], ignore_index=True)
    else:
        df_kombinovano = df_novo
    try: df_kombinovano.to_csv(fajl_str, index=False)
    except: pass
    
    st.session_state.df_history = df_kombinovano
    return df_kombinovano

def kreiraj_timeline_grafikon(df_hist, adresa=None, custom_naslov=None, is_pdf=False):
    df_sub = df_hist.copy()
    if adresa:
        df_sub = df_sub[df_sub["Adresa"] == adresa]
        naslov = f'Istorijat aktivnosti - {adresa.upper()}'
    else:
        if not df_sub.empty and 'Platforma' in df_sub.columns:
            if 'Datum' in df_sub.columns and 'Vreme' in df_sub.columns:
                df_sub = df_sub.groupby(["Datum", "Vreme", "Platforma"]).sum(numeric_only=True).reset_index()
        naslov = 'Zbirni Istorijat aktivnosti (Sve Adrese)'
        
    fig, ax = plt.subplots(figsize=(10, 4), facecolor='#ffffff')
    ax.set_facecolor('#f8f9fa')
    
    if len(df_sub) == 0:
        ax.text(0.5, 0.5, "Nema istorijskih podataka za ovaj period", ha='center', va='center')
        ax.axis('off')
    else:
        jedan_dan = df_sub["Datum"].nunique() <= 1 if 'Datum' in df_sub.columns else True
        if jedan_dan and 'Vreme' in df_sub.columns:
            df_sub["X_Label"] = df_sub["Vreme"]
        elif 'Datum' in df_sub.columns and 'Vreme' in df_sub.columns:
            df_sub["X_Label"] = df_sub["Datum"].str[-5:].str.replace('-', '.') + " \n" + df_sub["Vreme"]
        else:
            df_sub["X_Label"] = "Nepoznato"

        prikazi_wolt = "Wolt" in df_sub["Platforma"].values
        prikazi_glovo = "Glovo" in df_sub["Platforma"].values
        wolt_data = df_sub[df_sub["Platforma"] == "Wolt"]
        glovo_data = df_sub[df_sub["Platforma"] == "Glovo"]

        if is_pdf:
            wolt_data = wolt_data.tail(48)
            glovo_data = glovo_data.tail(48)

        if prikazi_wolt and not wolt_data.empty:
            ax.plot(wolt_data["X_Label"], wolt_data["Otvoreno"], marker='o', markersize=4, linestyle='-', color='#00c2e8', linewidth=2.5, label='Wolt Otvoreni')
        if prikazi_glovo and not glovo_data.empty:
            ax.plot(glovo_data["X_Label"], glovo_data["Otvoreno"], marker='s', markersize=4, linestyle='-', color='#ffc244', linewidth=2.5, label='Glovo Otvoreni')
            
        ax.set_ylabel('Broj otvorenih restorana', fontsize=11, fontweight='bold')
        ax.set_title(naslov, fontsize=14, fontweight='bold', color='#2c3e50', pad=15)
        ax.legend(frameon=True, fontsize=10, loc='lower center', bbox_to_anchor=(0.5, -0.3), ncol=2) 
        ax.grid(True, linestyle='--', alpha=0.6)
        
        n_ticks = len(wolt_data) if prikazi_wolt else (len(glovo_data) if prikazi_glovo else 0)
        step = max(1, n_ticks // 15) 
        for index, label in enumerate(ax.xaxis.get_ticklabels()):
            if index % step != 0: 
                label.set_visible(False)
        plt.xticks(rotation=45 if not jedan_dan else 0, fontsize=9)
        plt.yticks(fontsize=10)
        
    plt.tight_layout()
    imgdata = BytesIO()
    fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150)
    imgdata.seek(0)
    plt.close(fig)
    return imgdata

def kreiraj_grafikon_status(df_sub, naslov):
    wolt_o = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Otvoreno")])
    wolt_z = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Zatvoreno")])
    glovo_o = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Otvoreno")])
    glovo_z = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Zatvoreno")])
    wolt_u = wolt_o + wolt_z
    glovo_u = glovo_o + glovo_z
    fig, ax = plt.subplots(figsize=(5, 4), facecolor='#ffffff')
    labels = ['Ukupno', 'Otvoreno', 'Zatvoreno']
    x = np.arange(len(labels))
    width = 0.35
    ax.bar(x - width/2, [wolt_u, wolt_o, wolt_z], width, color='#00c2e8', label='Wolt')
    ax.bar(x + width/2, [glovo_u, glovo_o, glovo_z], width, color='#ffc244', label='Glovo')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontweight='bold', fontsize=10)
    ax.set_title(naslov, fontsize=12, fontweight='bold', color='#2c3e50')
    ax.legend(frameon=False, fontsize=9)
    for i, v in enumerate([wolt_u, wolt_o, wolt_z]):
        if v > 0: ax.text(i - width/2, v + 0.5, str(v), ha='center', va='bottom', fontweight='bold', color='#2c3e50', fontsize=9)
    for i, v in enumerate([glovo_u, glovo_o, glovo_z]):
        if v > 0: ax.text(i + width/2, v + 0.5, str(v), ha='center', va='bottom', fontweight='bold', color='#2c3e50', fontsize=9)
    max_v = max([wolt_u, glovo_u]) if max([wolt_u, glovo_u]) > 0 else 10
    ax.set_ylim(0, max_v * 1.2)
    plt.tight_layout()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)
    return imgdata

def kreiraj_grafikon_vreme_dostave(df_sub, naslov):
    wolt_df = df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Vreme_Broj"].notna())]
    glovo_df = df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Vreme_Broj"].notna())]
    w_avg = wolt_df["Vreme_Broj"].mean() if not wolt_df.empty else 0
    g_avg = glovo_df["Vreme_Broj"].mean() if not glovo_df.empty else 0
    fig, ax = plt.subplots(figsize=(5, 4), facecolor='#ffffff')
    prikazi_wolt = "Wolt" in df_sub["Platforma"].values or df_sub.empty
    prikazi_glovo = "Glovo" in df_sub["Platforma"].values or df_sub.empty
    if prikazi_wolt and prikazi_glovo:
        bars = ax.bar([0.2, 0.8], [w_avg, g_avg], color=['#00c2e8', '#ffc244'], width=0.35)
        ax.set_xticks([0.2, 0.8]); ax.set_xticklabels(['Wolt', 'Glovo'], fontweight='bold'); ax.set_xlim(-0.2, 1.2)
        bar_list = [w_avg, g_avg]; pos_list = [0.2, 0.8]
    elif prikazi_wolt:
        bars = ax.bar([0.5], [w_avg], color=['#00c2e8'], width=0.35)
        ax.set_xticks([0.5]); ax.set_xticklabels(['Wolt'], fontweight='bold'); ax.set_xlim(0, 1)
        bar_list = [w_avg]; pos_list = [0.5]
    elif prikazi_glovo:
        bars = ax.bar([0.5], [g_avg], color=['#ffc244'], width=0.35)
        ax.set_xticks([0.5]); ax.set_xticklabels(['Glovo'], fontweight='bold'); ax.set_xlim(0, 1)
        bar_list = [g_avg]; pos_list = [0.5]
    ax.set_ylabel('Prosečno vreme (min)', fontsize=11, fontweight='bold'); ax.set_title(naslov, fontsize=12, fontweight='bold', color='#2c3e50')
    for i, v in zip(pos_list, bar_list
