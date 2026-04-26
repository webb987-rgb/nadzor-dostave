import asyncio
import datetime
import os
import platform
import subprocess
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import random
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
            <p class="metric-title">🚲 Wolt Restorana</p>
            <p class="metric-value" style="color: #00c2e8;">{wolt_count}</p>
        </div>
        <div class="glovo-card">
            <p class="metric-title">🍔 Glovo Restorana</p>
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

# ---------------- ISTORIJA I GRAFICI ----------------
def sacuvaj_u_istoriju(df):
    vreme_sada = format_time_short()
    datum_sada = lokalno_vreme().strftime("%Y-%m-%d")
    istorija_podaci = []
    adrese = df["Adresa"].unique()
    for adr in adrese:
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Adresa"] == adr) & (df["Platforma"] == plat)]
            istorija_podaci.append({ "Datum": datum_sada, "Vreme": vreme_sada, "Adresa": adr, "Platforma": plat, "Otvoreno": len(sub[sub["Status"] == "Otvoreno"]), "Zatvoreno": len(sub[sub["Status"] == "Zatvoreno"]) })
    df_novo = pd.DataFrame(istorija_podaci)
    fajl_str = str(HISTORY_FILE)
    if os.path.exists(fajl_str):
        df_kombinovano = pd.concat([pd.read_csv(fajl_str), df_novo], ignore_index=True)
    else: df_kombinovano = df_novo
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
    if custom_naslov: naslov = custom_naslov
    fig, ax = plt.subplots(figsize=(10, 4), facecolor='#ffffff')
    ax.set_facecolor('#f8f9fa')
    if len(df_sub) == 0:
        ax.text(0.5, 0.5, "Nema istorijskih podataka", ha='center', va='center')
        ax.axis('off')
    else:
        jedan_dan = df_sub["Datum"].nunique() <= 1 if 'Datum' in df_sub.columns else True
        if jedan_dan and 'Vreme' in df_sub.columns: df_sub["X_Label"] = df_sub["Vreme"]
        elif 'Datum' in df_sub.columns and 'Vreme' in df_sub.columns: df_sub["X_Label"] = df_sub["Datum"].str[-5:].str.replace('-', '.') + " \n" + df_sub["Vreme"]
        else: df_sub["X_Label"] = "Nepoznato"
        wolt_data = df_sub[df_sub["Platforma"] == "Wolt"]
        glovo_data = df_sub[df_sub["Platforma"] == "Glovo"]
        if is_pdf: wolt_data, glovo_data = wolt_data.tail(48), glovo_data.tail(48)
        if not wolt_data.empty: ax.plot(wolt_data["X_Label"], wolt_data["Otvoreno"], marker='o', markersize=4, linestyle='-', color='#00c2e8', linewidth=2.5, label='Wolt Otvoreni')
        if not glovo_data.empty: ax.plot(glovo_data["X_Label"], glovo_data["Otvoreno"], marker='s', markersize=4, linestyle='-', color='#ffc244', linewidth=2.5, label='Glovo Otvoreni')
        ax.set_ylabel('Broj otvorenih restorana', fontsize=11, fontweight='bold')
        ax.set_title(naslov, fontsize=14, fontweight='bold', color='#2c3e50', pad=15)
        ax.legend(frameon=True, fontsize=10, loc='lower center', bbox_to_anchor=(0.5, -0.3), ncol=2) 
        ax.grid(True, linestyle='--', alpha=0.6)
        n_ticks = len(wolt_data) if not wolt_data.empty else (len(glovo_data) if not glovo_data.empty else 0)
        step = max(1, n_ticks // 15) 
        for index, label in enumerate(ax.xaxis.get_ticklabels()):
            if index % step != 0: label.set_visible(False)
        plt.xticks(rotation=45 if not jedan_dan else 0, fontsize=9); plt.yticks(fontsize=10)
    plt.tight_layout()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)
    return imgdata

def kreiraj_grafikon_status(df_sub, naslov):
    wolt_o = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Otvoreno")])
    wolt_z = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Zatvoreno")])
    glovo_o = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Otvoreno")])
    glovo_z = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Zatvoreno")])
    fig, ax = plt.subplots(figsize=(5, 4), facecolor='#ffffff')
    labels, x, width = ['Ukupno', 'Otvoreno', 'Zatvoreno'], np.arange(3), 0.35
    ax.bar(x - width/2, [wolt_o+wolt_z, wolt_o, wolt_z], width, color='#00c2e8', label='Wolt')
    ax.bar(x + width/2, [glovo_o+glovo_z, glovo_o, glovo_z], width, color='#ffc244', label='Glovo')
    ax.set_xticks(x); ax.set_xticklabels(labels, fontweight='bold', fontsize=10)
    ax.set_title(naslov, fontsize=12, fontweight='bold', color='#2c3e50')
    ax.legend(frameon=False, fontsize=9)
    for i, v in enumerate([wolt_o+wolt_z, wolt_o, wolt_z]):
        if v > 0: ax.text(i - width/2, v + 0.5, str(v), ha='center', va='bottom', fontweight='bold', color='#2c3e50', fontsize=9)
    for i, v in enumerate([glovo_o+glovo_z, glovo_o, glovo_z]):
        if v > 0: ax.text(i + width/2, v + 0.5, str(v), ha='center', va='bottom', fontweight='bold', color='#2c3e50', fontsize=9)
    max_v = max([wolt_o+wolt_z, glovo_o+glovo_z]) if max([wolt_o+wolt_z, glovo_o+glovo_z]) > 0 else 10
    ax.set_ylim(0, max_v * 1.2)
    plt.tight_layout()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)
    return imgdata

def kreiraj_grafikon_vreme_dostave(df_sub, naslov):
    wolt_df = df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Vreme_Broj"].notna())]
    glovo_df = df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Vreme_Broj"].notna())]
    
    # OSIGURAČ ZA GRAPH: Ako nema podataka (NaN), stavi nulu da ne puca
    w_avg = wolt_df["Vreme_Broj"].dropna().mean() if not wolt_df["Vreme_Broj"].dropna().empty else 0
    w_avg = 0 if pd.isna(w_avg) else w_avg
    
    g_avg = glovo_df["Vreme_Broj"].dropna().mean() if not glovo_df["Vreme_Broj"].dropna().empty else 0
    g_avg = 0 if pd.isna(g_avg) else g_avg
    
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
        
    ax.set_ylabel('Prosečno vreme (min)', fontsize=11, fontweight='bold')
    ax.set_title(naslov, fontsize=12, fontweight='bold', color='#2c3e50')
    
    for i, v in zip(pos_list, bar_list):
        if v > 0: ax.text(i, v + 0.5, f"{v:.1f} min", ha='center', va='bottom', fontweight='bold', color='#2c3e50', fontsize=9)
        
    max_v = max(bar_list) if len(bar_list) > 0 and max(bar_list) > 0 else 10
    ax.set_ylim(0, max_v * 1.2)
    plt.tight_layout()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)
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
    cist = str(tekst) + " \n " + re.sub(r'<[^>]+>', ' \n ', str(html))
    akcije, seen, res = [], set(), []
    for line in [l.strip().lower() for l in cist.split('\n') if l.strip()]:
        if len(line) > 80: continue 
        if any(x in line for x in ["besplatna dostava", "free delivery", "dostava 0", "delivery 0"]): akcije.append("Besplatna dostava")
        elif "1+1" in line or "buy 1 get 1" in line: akcije.append("1+1 Gratis")
        elif "%" in line and re.search(r'\d', line):
            if plat == "Glovo" and re.fullmatch(r'\d{1,3}\s*%', line): continue
            akcije.append(line)
        elif any(x in line for x in ["rsd", "din"]) and any(x in line for x in ["-", "off", "discount", "popust", "save", "spend"]): akcije.append(line)
    
    sve_low = (str(tekst) + " " + str(html)).lower()
    if not akcije:
        akcije.extend([p.strip() for p in re.findall(r'(-\s*\d{1,2}\s*%|\b\d{1,2}\s*%\s*popust|\b\d{1,2}\s*%\s*off|\b\d{1,2}\s*%\s*discount)', sve_low)])
    if "wolt+" in sve_low: akcije.append("Wolt+")
    if "prime" in sve_low: akcije.append("Prime")
        
    for a in akcije:
        ac = re.sub(r'<[^>]+>', '', a).strip()
        if not ac: continue
        if "besplatna" in ac.lower() or "free" in ac.lower(): ac = "Besplatna dostava"
        elif ac not in ["Wolt+", "Prime"]: ac = ac[0].upper() + ac[1:].replace("rsd", "RSD").replace("din", "DIN")
        if ac not in seen: seen.add(ac); res.append(f"• {ac}")
    return "\n".join(res) if res else "-"

def normalizuj_ime(ime): return re.sub(r'[^\w]', '', str(ime).lower())

# ---------------- ORIGINALNO LJUDSKO SKROLOVANJE (Provereno radi na oba sajta) ----------------
async def pametno_skrolovanje_i_ekstrakcija(page, plat, address, log_ph, live_ph, live_state):
    results_dict = {}
    prethodni_broj = 0; pokusaji_na_dnu = 0
    max_pokusaja = 8 # Siguran broj pokušaja na dnu za velike gradove
    
    while True:
        if plat == "Wolt":
            podaci = await page.evaluate('''() => {
                let rez = []; document.querySelectorAll("a[data-test-id^='venueCard.']").forEach(c => {
                    rez.push({link: c.href, text: c.innerText, html: (c.closest("li") || c).innerHTML});
                }); return rez;
            }''')
        else:
            podaci = await page.evaluate('''() => {
                let rez = []; document.querySelectorAll("a:has(h3), a[data-testid='store-card'], .store-card a").forEach(c => {
                    if (!c.href.includes('/dostava') && !c.href.includes('/category')) { rez.push({link: c.href, text: c.innerText, html: c.innerHTML}); }
                }); return rez;
            }''')

        for item in podaci:
            link = item['link']
            if not link or link in results_dict: continue
            
            sve_z = item['text'] + " " + item.get('html', '')
            ime = ukloni_kvacice(izvuci_ime(item['text']))
            if len(ime) < 2: continue
            
            ocena = izvuci_ocenu(sve_z, plat)
            is_new = ("novo" in sve_z.lower() or "new" in sve_z.lower() or ocena == "Novo")
            vreme_str, vreme_num = izvuci_vreme_dostave(sve_z)

            results_dict[link] = {
                "Adresa": address, "Platforma": plat, "Naziv": ime, "Ocena": ocena,
                "Vreme dostave": vreme_str, "Akcija": izvuci_akciju(item['text'], item.get('html',''), plat), 
                "Status": analiziraj_status(sve_z), "Vreme_Broj": vreme_num, "Is_New": is_new, "Link": link
            }

        trenutni = len(results_dict)
        if trenutni > prethodni_broj:
            # AŽURIRAJ LIVE UI BROJAČE
            live_state[plat] = trenutni
            osvezi_live_ui(live_ph, live_state["Wolt"], live_state["Glovo"], address)
            
            prethodni_broj = trenutni
            pokusaji_na_dnu = 0
            
        await page.evaluate("window.scrollBy(0, 500);")
        await asyncio.sleep(0.5)
        
        h = await page.evaluate("document.body.scrollHeight")
        s = await page.evaluate("window.scrollY + window.innerHeight")
        
        if s >= h - 100:
            pokusaji_na_dnu += 1
            await asyncio.sleep(2)
            if pokusaji_na_dnu >= max_pokusaja: break 
            
    return list(results_dict.values())

# ---------------- SCRAPERS SA SCREENSHOT LOGIKOM ----------------
async def scrape_wolt(context_wolt, address, log_ph, live_ph, live_state, error_screenshots):
    page = None
    try:
        page = await context_wolt.new_page()
        page.set_default_timeout(10000)
        
        await page.goto("https://wolt.com/sr/srb")
        try: await page.locator("[data-test-id='allow-button']").click(timeout=3000)
        except: pass
        
        try:
            input_f = page.get_by_role("combobox").first
            await input_f.wait_for(state="visible", timeout=4000)
            await input_f.click()
            await input_f.fill(address)
            await asyncio.sleep(2)
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await asyncio.sleep(5)
            await page.goto("https://wolt.com/sr/discovery/restaurants")
        except PlaywrightTimeoutError:
            try:
                header_btn = page.locator("[data-test-id='header.address-select-button']")
                if not await header_btn.is_visible(): header_btn = page.locator("header [role='button']").first
                await header_btn.click(timeout=5000)
                await asyncio.sleep(1)
                search_modal = page.locator("[data-test-id='address-picker-input']")
                if not await search_modal.is_visible(): search_modal = page.get_by_role("combobox").last
                await search_modal.click()
                await search_modal.fill(address)
                await asyncio.sleep(2)
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")
                await asyncio.sleep(5)
                await page.goto("https://wolt.com/sr/discovery/restaurants")
            except Exception: pass
            
        rez = await pametno_skrolovanje_i_ekstrakcija(page, "Wolt", address, log_ph, live_ph, live_state)
        
        # 🚨 PROVERA GREŠKE: Slikaj ako je našao sumnjivo malo restorana
        if len(rez) < 5:
            err_path = str(ERRORS_DIR / f"Wolt_Upozorenje_{ukloni_kvacice(address).replace(' ', '_')}_{timestamp()}.png")
            try:
                await page.screenshot(path=err_path)
                error_screenshots.append(err_path)
            except: pass
            
        return rez
    except Exception as e: 
        if page:
            try:
                ep = str(ERRORS_DIR / f"Wolt_Error_{timestamp()}.png")
                await page.screenshot(path=ep); error_screenshots.append(ep)
            except: pass
        return []
    finally:
        if page: await page.close()

async def scrape_glovo(context_glovo, address, log_ph, live_ph, live_state, error_screenshots):
    page = None
    try:
        page = await context_glovo.new_page()
        page.set_default_timeout(10000)
        
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")
        if "Oh, no!" in await page.content():
            ep = str(ERRORS_DIR / f"Glovo_Ban_{timestamp()}.png")
            await page.screenshot(path=ep); error_screenshots.append(ep)
            return []
            
        try: await page.get_by_role("button", name=re.compile("Accept|Prihvati", re.I)).click(timeout=3000)
        except: pass
        
        try:
            h_input = page.locator("#hero-container-input")
            await h_input.wait_for(state="visible", timeout=4000)
            await h_input.click()
            await page.get_by_role("searchbox").fill(address)
            await page.locator("div[data-actionable='true'][role='button']").first.click(timeout=8000)
        except PlaywrightTimeoutError:
            try:
                await page.locator('header div[role="button"]').first.click(timeout=5000)
                await asyncio.sleep(1)
                await page.get_by_role("searchbox").last.fill(address)
                await asyncio.sleep(2)
                await page.locator("div[data-actionable='true'][role='button']").first.click(timeout=8000)
            except Exception: pass

        try: await page.locator("button:has-text('Drugo')").click(timeout=3000)
        except: pass
        try: await page.locator("button:has-text('Potvrdi adresu')").click(timeout=3000)
        except: pass
        await asyncio.sleep(4)
        
        try: await page.locator("text='Idi na početnu stranicu'").first.click(timeout=3000); await asyncio.sleep(4)
        except: pass
        
        try: await page.get_by_role("link", name=re.compile(r"Restorani|Hrana|Food|Restaurants", re.I)).first.click(timeout=5000)
        except: pass
        
        await asyncio.sleep(5)
        page.set_default_timeout(60000) 
        rez = await pametno_skrolovanje_i_ekstrakcija(page, "Glovo", address, log_ph, live_ph, live_state)
        
        # 🚨 PROVERA GREŠKE: Slikaj ako je našao sumnjivo malo restorana
        if len(rez) < 5:
            err_path = str(ERRORS_DIR / f"Glovo_Upozorenje_{ukloni_kvacice(address).replace(' ', '_')}_{timestamp()}.png")
            try:
                await page.screenshot(path=err_path)
                error_screenshots.append(err_path)
            except: pass

        return rez
    except Exception as e: 
        if page:
            try:
                ep = str(ERRORS_DIR / f"Glovo_Error_{timestamp()}.png")
                await page.screenshot(path=ep); error_screenshots.append(ep)
            except: pass
        return []
    finally:
        if page: await page.close()

# ---------------- SEKVENCIJALNI PROCES SKENIRANJA (BEZ MREŽNIH BLOKADA) ----------------
async def proces_skeniranja(adrese, log_ph, live_ph, live_state, generisi_pdf=False, email_primaoca=""):
    sve = []
    error_screenshots = [] 
    
    async with async_playwright() as p:
        # Standardno otvaranje Chromiuma bez ikakvih blokatora slika
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"]
        ) 
        
        wa = {"permissions": ['geolocation'], "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        if os.path.exists(WOLT_AUTH_FILE): wa["storage_state"] = WOLT_AUTH_FILE
            
        ga = {"permissions": ['geolocation'], "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9,sr;q=0.8"}}
        if os.path.exists(GLOVO_AUTH_FILE): ga["storage_state"] = GLOVO_AUTH_FILE
            
        for i, adr in enumerate(adrese):
            live_state["Wolt"], live_state["Glovo"] = 0, 0
            osvezi_live_ui(live_ph, 0, 0, adr)
            
            if i > 0: await asyncio.sleep(5)
            
            # 1. GLOVO
            context_glovo = await browser.new_context(**ga)
            sve.extend(await scrape_glovo(context_glovo, adr, log_ph, live_ph, live_state, error_screenshots))
            await context_glovo.close() 
            
            # 2. WOLT
            context_wolt = await browser.new_context(**wa)
            sve.extend(await scrape_wolt(context_wolt, adr, log_ph, live_ph, live_state, error_screenshots))
            await context_wolt.close() 
                
        await browser.close()
            
    if sve:
        df_s = pd.DataFrame(sve)
        df_h = sacuvaj_u_istoriju(df_s)
        
        pdf_fajlovi = []
        if generisi_pdf:
            zbirni = napravi_zbirni_pdf(df_s, df_h)
            if zbirni: pdf_fajlovi.append(zbirni)
            for adr in df_s["Adresa"].unique():
                p_fajl = napravi_pdf_za_adresu(df_s[df_s["Adresa"] == adr], adr, df_h)
                if p_fajl: pdf_fajlovi.append(p_fajl)
                
            if email_primaoca.strip() and pdf_fajlovi:
                posalji_email(pdf_fajlovi, email_primaoca, log_ph)
            
        return df_s, df_h, pdf_fajlovi, error_screenshots
    return pd.DataFrame(), pd.DataFrame(), [], error_screenshots

# ================= STREAMLIT UI KONTROLE =================
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
    st.header("⚙️ Podešavanja skeniranja")
    adresa_1 = st.text_input("📍 Adresa 1 (Obavezna):", value="", placeholder="Makenzijeva 57, Beograd")
    adresa_2 = st.text_input("📍 Adresa 2 (Opciona):", value="", placeholder="Somborska 5, Niš")
    
    auto_refresh = st.checkbox("🔄 Automatsko osvežavanje", value=False)
    sleep_interval = st.number_input("⏱️ Interval (min):", min_value=1, value=60, disabled=not auto_refresh)
    
    generisi_pdf = st.checkbox("📄 Generiši PDF izveštaje", value=False)
    email_unos = st.text_input("📧 Pošalji na email:", placeholder="tvoj@email.com") if generisi_pdf else ""
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ POKRENI", type="primary", use_container_width=True): 
            st.session_state.pokrenuto = True
            st.session_state.loaded_history = False
            st.session_state.last_run = 0
            st.rerun()
    with c2:
        if st.button("⏹️ ZAUSTAVI", use_container_width=True): 
            st.session_state.pokrenuto = False
            st.rerun()

    st.markdown("---")
    st.header("📂 Arhiva skeniranja")
    istorija_fajlovi = sorted(list(OUTPUT_DIR.glob("Detaljno_*.csv")), reverse=True)
    opcije = {"--- Izaberi stari izveštaj ---": None}
    for f in istorija_fajlovi:
        ime = f.stem.replace("Detaljno_", "")
        try: opcije[datetime.datetime.strptime(ime, "%Y%m%d_%H%M%S").strftime("%d.%m.%Y u %H:%M:%S")] = f
        except: opcije[ime] = f

    izabrani_fajl = st.selectbox("Prethodna skeniranja:", list(opcije.keys()), label_visibility="collapsed")
    col_ucitaj, col_obrisi = st.columns(2)
    with col_ucitaj:
        if st.button("📂 Učitaj", use_container_width=True) and opcije[izabrani_fajl]:
            st.session_state.df_sve = pd.read_csv(opcije[izabrani_fajl])
            st.session_state.pokrenuto = False
            st.session_state.loaded_history = True
            st.session_state.last_run = os.path.getmtime(opcije[izabrani_fajl])
            st.rerun()
    with col_obrisi:
        # DUGME ZA BRISANJE
        if st.button("🗑️ Obriši", type="secondary", use_container_width=True) and opcije[izabrani_fajl]:
            os.remove(opcije[izabrani_fajl])
            if st.session_state.loaded_history: 
                st.session_state.df_sve = pd.DataFrame()
                st.session_state.loaded_history = False
            st.rerun()

# ================= GLAVNI INTERFEJS (TABS & LOADING) =================
if st.session_state.pokrenuto or st.session_state.loaded_history:

    if st.session_state.pokrenuto:
        lista_adresa = [cirilica_u_latinicu(a.strip()) for a in [adresa_1, adresa_2] if a.strip()]
        if not lista_adresa: 
            st.warning("⚠️ Unesite bar prvu adresu da biste skenirali!"); st.session_state.pokrenuto = False; st.rerun()

        if time.time() - st.session_state.last_run >= sleep_interval * 60 or st.session_state.last_run == 0:
            
            # MODERAN LOADING SPINNER
            with st.spinner('🔄 Skripta vredno rudari podatke, molim te sačekaj...'):
                live_ui_ph = st.empty() 
                sl = st.empty() 
                live_state = {"Wolt": 0, "Glovo": 0}
                
                df, hi, pdf, err_imgs = asyncio.run(proces_skeniranja(lista_adresa, sl, live_ui_ph, live_state, generisi_pdf, email_unos))
                
                if not df.empty:
                    df.to_csv(OUTPUT_DIR / f"Detaljno_{timestamp()}.csv", index=False)

                live_ui_ph.empty()
                st.session_state.df_sve, st.session_state.df_history, st.session_state.pdf_fajlovi, st.session_state.error_screenshots, st.session_state.last_run = df, hi, pdf, err_imgs, time.time()
                sl.empty()
            st.rerun()

    df = st.session_state.df_sve
    if not df.empty:
        for col in ["Vreme_Broj", "Vreme dostave", "Ocena", "Is_New"]:
            if col not in df.columns: df[col] = False if col == "Is_New" else (np.nan if "Broj" in col else "-")

        if st.session_state.loaded_history: st.info("📂 **Pregledate arhivirani izveštaj.**")
        else: st.success(f"✅ Skeniranje završeno u: {datetime.datetime.fromtimestamp(st.session_state.last_run, LOCAL_TZ).strftime('%H:%M:%S')}")
        
        # MODERNI TABOVI
        tab_dash, tab_akcije, tab_uporedno, tab_lista, tab_istorija = st.tabs([
            "📊 Dashboard", "🎁 Akcije i Popusti", "⚖️ Uporedni Prikaz", "🔍 Lista Restorana", "📅 Istorijat"
        ])
        
        adrese_un = list(df["Adresa"].unique())
        
        with tab_dash:
            tc = st.columns(len(adrese_un))
            for i, adr in enumerate(adrese_un):
                with tc[i % len(tc)]:
                    st.markdown(f"#### 📍 {adr.upper()}")
                    sd = df[df["Adresa"] == adr]; sm = []
                    for p in ["Wolt", "Glovo"]:
                        pd_f = sd[sd["Platforma"] == p]
                        if not pd_f.empty: sm.append({"Platforma": p, "Ukupno": len(pd_f), "Otvoreno": len(pd_f[pd_f["Status"]=="Otvoreno"]), "Zatvoreno": len(pd_f[pd_f["Status"]=="Zatvoreno"])})
                    if sm: st.dataframe(pd.DataFrame(sm), hide_index=True, use_container_width=True)
            
            st.markdown("---")
            graf_adr = st.selectbox("Izaberi adresu za grafikone:", ["Sve adrese"] + adrese_un, index=1 if len(adrese_un)==1 else 0)
            c_df = df if graf_adr == "Sve adrese" else df[df["Adresa"] == graf_adr]
            ca, cb = st.columns(2)
            with ca: st.image(kreiraj_grafikon_status(c_df, "Uporedni Status"), use_container_width=True)
            with cb: st.image(kreiraj_grafikon_vreme_dostave(c_df, "Prosečno vreme dostave"), use_container_width=True)

        with tab_akcije:
            unikatne_akcije = set()
            for akcija_str in df['Akcija']:
                if pd.notna(akcija_str) and str(akcija_str) != "-":
                    for a in str(akcija_str).split('\n'):
                        cl = a.replace("• ", "").strip()
                        if cl: unikatne_akcije.add(cl)
            izabrani_popusti = st.multiselect("Filtriraj grafikon akcija:", sorted(list(unikatne_akcije)), default=list(unikatne_akcije)[:5] if unikatne_akcije else [])
            st.image(kreiraj_grafikon_popusta(df, izabrani_popusti, "Broj restorana sa izabranim akcijama"), use_container_width=False)

        with tab_uporedno:
            c_up1, c_up2 = st.columns(2)
            with c_up1: filter_wolt_up = st.multiselect("Prikaz za Wolt:", ["Otvoreno", "Zatvoreno"], default=["Otvoreno", "Zatvoreno"])
            with c_up2: filter_glovo_up = st.multiselect("Prikaz za Glovo:", ["Otvoreno", "Zatvoreno"], default=["Otvoreno", "Zatvoreno"])

            df['Naziv_Norm'] = df['Naziv'].apply(normalizuj_ime)
            uporedni_podaci = []
            for adr in adrese_un:
                df_adr = df[df['Adresa'] == adr]
                zajednicki = set(df_adr[df_adr['Platforma'] == 'Wolt']['Naziv_Norm']).intersection(set(df_adr[df_adr['Platforma'] == 'Glovo']['Naziv_Norm']))
                for norm_ime in zajednicki:
                    w_row = df_adr[(df_adr['Platforma'] == 'Wolt') & (df_adr['Naziv_Norm'] == norm_ime)].iloc[0]
                    g_row = df_adr[(df_adr['Platforma'] == 'Glovo') & (df_adr['Naziv_Norm'] == norm_ime)].iloc[0]
                    uporedni_podaci.append({ "Adresa": adr, "Naziv (Wolt)": w_row['Naziv'], "Status Wolt": w_row['Status'], "Vreme Wolt": w_row['Vreme dostave'], "Link Wolt": w_row['Link'], "Naziv (Glovo)": g_row['Naziv'], "Status Glovo": g_row['Status'], "Vreme Glovo": g_row['Vreme dostave'], "Link Glovo": g_row['Link'] })
            
            if uporedni_podaci:
                df_uporedni = pd.DataFrame(uporedni_podaci)
                df_uporedni = df_uporedni[(df_uporedni['Status Wolt'].isin(filter_wolt_up)) & (df_uporedni['Status Glovo'].isin(filter_glovo_up))]
                if not df_uporedni.empty: st.dataframe(df_uporedni.style.map(lambda val: f'color: {"#27ae60" if val=="Otvoreno" else "#e74c3c"}; font-weight: bold;', subset=['Status Wolt', 'Status Glovo']), use_container_width=True, hide_index=True, column_config={"Link Wolt": st.column_config.LinkColumn("Link Wolt"), "Link Glovo": st.column_config.LinkColumn("Link Glovo")})
                else: st.info("Nema restorana za date filtere.")
            else: st.info("Nema zajedničkih restorana na obe platforme.")

        with tab_lista:
            f1, f2, f3 = st.columns(3)
            with f1: fa = st.multiselect("Adresa", adrese_un, adrese_un)
            with f2: fp = st.multiselect("Platforma", df["Platforma"].unique(), df["Platforma"].unique())
            with f3: fs = st.multiselect("Status", ["Otvoreno", "Zatvoreno"], ["Otvoreno", "Zatvoreno"])
            c_filt1, c_filt2 = st.columns(2)
            with c_filt1: filt_new = st.checkbox("Samo NOVE restorane")
            with c_filt2: filt_promo = st.checkbox("Samo SA AKCIJAMA")
            
            f_df = df[(df["Adresa"].isin(fa)) & (df["Platforma"].isin(fp)) & (df["Status"].isin(fs))]
            if filt_new: f_df = f_df[f_df["Is_New"].isin([True, 'True', 'true', 1])]
            if filt_promo: f_df = f_df[f_df["Akcija"] != "-"]

            disp_df = f_df.drop(columns=['Naziv_Norm', 'Vreme_Broj', 'Is_New'], errors='ignore')
            def style_rows(row):
                s = [''] * len(row)
                s[row.index.get_loc('Status')] = 'color: #27ae60; font-weight: bold;' if row['Status'] == 'Otvoreno' else 'color: #e74c3c; font-weight: bold;'
                if row['Akcija'] != '-': s[row.index.get_loc('Akcija')] = 'color: #8e44ad; font-weight: bold;'
                return s
            st.dataframe(disp_df.style.apply(style_rows, axis=1), use_container_width=True, hide_index=True, column_config={"Link": st.column_config.LinkColumn("Link"), "Akcija": st.column_config.TextColumn("Akcija", width="large")})

        with tab_istorija:
            hist_df = st.session_state.df_history.copy()
            if not hist_df.empty and 'Datum' in hist_df.columns and 'Vreme' in hist_df.columns:
                hist_df['Datetime'] = pd.to_datetime(hist_df['Datum'] + ' ' + hist_df['Vreme'])
                c_dt1, c_dt2, c_dt3, c_dt4 = st.columns(4)
                with c_dt1: s_d = st.date_input("Od:", hist_df['Datetime'].min().date())
                with c_dt2: s_t = st.time_input("Vreme od:", datetime.time(0, 0))
                with c_dt3: e_d = st.date_input("Do:", hist_df['Datetime'].max().date())
                with c_dt4: e_t = st.time_input("Vreme do:", datetime.time(23, 59))
                
                mask = (hist_df['Datetime'] >= pd.to_datetime(datetime.datetime.combine(s_d, s_t))) & (hist_df['Datetime'] <= pd.to_datetime(datetime.datetime.combine(e_d, e_t)))
                st.image(kreiraj_timeline_grafikon(hist_df.loc[mask].copy(), None, "Istorijat"), use_container_width=True)
            else: st.info("Nema istorije.")

        # --- GREŠKE I PDF OSTAJU ISPOD TABOVA DA BUDU UVEK VIDLJIVI ---
        if st.session_state.get('error_screenshots'):
            st.markdown("---")
            st.error("⚠️ PAŽNJA: Skripta je zabeležila greške ili sumnjivo mali broj restorana. Pogledaj screenshotove sa lica mesta:")
            ec = st.columns(len(st.session_state.error_screenshots))
            for idx, img_path in enumerate(st.session_state.error_screenshots):
                with ec[idx % len(ec)]: st.image(img_path, caption=os.path.basename(img_path), use_container_width=True)

        if st.session_state.get('pdf_fajlovi'):
            st.markdown("---"); st.subheader("📥 PDF Izveštaji")
            pc = st.columns(4)
            for i, p in enumerate(st.session_state.pdf_fajlovi):
                with pc[i % 4]:
                    with open(p, "rb") as f: st.download_button(f"Preuzmi {os.path.basename(p)}", f.read(), os.path.basename(p), "application/pdf")

    if st.session_state.pokrenuto:
        if auto_refresh:
            rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
            while rem > 0:
                # Da bi se tajmer vrteo moramo osvežiti tekst, ali pošto je ui zakucan sa time.sleep, Streamlit koristi empty()
                st.sidebar.info(f"⏳ Odbrojavanje do sledećeg skeniranja: **{rem//60:02d}:{rem%60:02d}**")
                time.sleep(1); rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
            st.rerun()
        else:
            st.sidebar.success("✅ Skeniranje završeno. Kliknite 'Pokreni' za novo skeniranje.")
        
else: 
    st.info("Sistem je spreman. Unesite parametre i kliknite 'Pokreni'.")
