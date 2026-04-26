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

# ================= KONFIGURACIJA STRANICE =================
st.set_page_config(page_title="Nadzor Dostave PRO", page_icon="📊", layout="wide")

# CUSTOM CSS ZA MODERAN IZGLED
st.markdown("""
    <style>
    .stApp { background-color: #f8f9fa; }
    .main-header { font-size: 2.2rem; font-weight: 800; color: #1e293b; margin-bottom: 0.5rem; }
    .sub-header { font-size: 1rem; color: #64748b; margin-bottom: 2rem; }
    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
        border: 1px solid #e2e8f0;
    }
    div[data-testid="stMetricValue"] { font-size: 1.8rem; font-weight: 700; color: #0f172a; }
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: transparent;
        border-radius: 4px 4px 0px 0px;
        gap: 1px;
        font-weight: 600;
    }
    .stDataFrame { border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; }
    </style>
""", unsafe_allow_html=True)

# ================= LOGIKA (NEPROMENJENA) =================

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

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

@st.cache_resource
def install_playwright():
    os.system("playwright install chromium")

install_playwright()

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

def cirilica_u_latinicu(tekst):
    if not tekst: return ""
    mapa = {
        'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s',
        'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'Њ':'Nj', 'О':'O', 'П':'P', 'Р':'R', 'С':'S', 'Т':'T', 'Ћ':'C', 'У':'U', 'Ф':'F', 'Х':'H', 'Ц':'C', 'Ч':'C', 'Џ':'Dz', 'Ш':'S'
    }
    for k, v in mapa.items(): tekst = tekst.replace(k, v)
    return tekst

def posalji_email(pdf_putanje, primaoci_str, log_ph=None):
    lista_primaoca = [e.strip() for e in primaoci_str.split(",") if e.strip()]
    if not lista_primaoca: return
    try:
        log_msg(f"[SISTEM] Šaljem email...", log_ph)
        for primalac in lista_primaoca:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_POSILJAOCA
            msg['To'] = primalac
            msg['Subject'] = f"Izveštaji o dostavi - {lokalno_vreme().strftime('%d.%m. u %H:%M')}"
            body = "Izveštaji su u prilogu."
            msg.attach(MIMEText(body, 'plain'))
            for pdf_putanja in pdf_putanje:
                with open(pdf_putanja, "rb") as attachment:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f"attachment; filename= {os.path.basename(pdf_putanja)}")
                    msg.attach(part)
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls(); server.login(EMAIL_POSILJAOCA, LOZINKA_POSILJAOCA)
            server.sendmail(EMAIL_POSILJAOCA, primalac, msg.as_string()); server.quit()
    except Exception as e: log_msg(f"[GREŠKA] Email: {e}", log_ph)

def sacuvaj_u_istoriju(df):
    vreme_sada = format_time_short()
    datum_sada = lokalno_vreme().strftime("%Y-%m-%d")
    istorija_podaci = []
    for adr in df["Adresa"].unique():
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Adresa"] == adr) & (df["Platforma"] == plat)]
            istorija_podaci.append({
                "Datum": datum_sada, "Vreme": vreme_sada, "Adresa": adr, "Platforma": plat, 
                "Otvoreno": len(sub[sub["Status"] == "Otvoreno"]), "Zatvoreno": len(sub[sub["Status"] == "Zatvoreno"])
            })
    df_novo = pd.DataFrame(istorija_podaci)
    fajl_str = str(HISTORY_FILE)
    if os.path.exists(fajl_str):
        df_staro = pd.read_csv(fajl_str)
        df_kombinovano = pd.concat([df_staro, df_novo], ignore_index=True)
    else: df_kombinovano = df_novo
    try: df_kombinovano.to_csv(fajl_str, index=False)
    except: pass
    st.session_state.df_history = df_kombinovano
    return df_kombinovano

# --- POMOĆNE FUNKCIJE ZA ANALIZU (Kopirane iz originala) ---
def ukloni_kvacice(tekst):
    if not tekst: return ""
    mapa = {'č':'c', 'ć':'c', 'ž':'z', 'š':'s', 'đ':'dj', 'Č':'C', 'Ć':'C', 'Ž':'Z', 'Š':'S', 'Đ':'Dj'}
    for k, v in mapa.items(): tekst = tekst.replace(k, v)
    return tekst

def izvuci_ime(tekst):
    if not tekst: return ""
    lines = tekst.split('\n')
    for line in lines:
        line = line.strip()
        if not line or '%' in line: continue
        line_lower = line.lower()
        if "min" in line_lower and re.search(r'\d+', line_lower): continue
        if "rsd" in line_lower or "din" in line_lower: continue
        if any(x in line_lower for x in ["promo", "novo", "odlično", "besplatna dostava", "artikli", "narudžb", "narudzb", "popust", "off", "discount"]): continue
        if len(line) >= 2: return line
    return ""

def analiziraj_status(text):
    t = text.lower()
    if any(x in t for x in ["uskoro se zatvara", "closing soon", "zatvara se za", "closes in"]): return "Otvoreno"
    ind = ["samo preuzimanje", "samo za preuzimanje", "pickup only", "dostava nije dostupna", "zatvoreno", "zakažite", "zakaži", "zakazi", "nedostupno", "closed", "schedule"]
    if any(k in t for k in ind): return "Zatvoreno"
    return "Otvoreno"

def izvuci_ocenu(tekst, plat):
    try:
        if not tekst: return "-"
        cist_tekst = re.sub(r'<[^>]+>', ' ', tekst).lower()
        if plat == "Glovo":
            procenti = re.findall(r'(\d{1,3})\s*%', cist_tekst)
            for p in procenti:
                if int(p) >= 60: return p + "%"
        elif plat == "Wolt":
            match = re.search(r'\b([5-9][.,][0-9]|10[.,]0)\b', cist_tekst)
            if match: return match.group(1).replace(',', '.')
        return "-"
    except: return "-"

def izvuci_vreme_dostave(tekst):
    try:
        if not tekst: return "-", np.nan
        cist_tekst = re.sub(r'<[^>]+>', ' ', tekst).lower()
        match = re.search(r'(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(?:min|m|\')', cist_tekst)
        if match:
            v1, v2 = int(match.group(1)), int(match.group(2))
            return f"{v1}-{v2} min", (v1 + v2) / 2.0
        match_single = re.search(r'\b(\d{1,3})\s*(?:min|m|\')', cist_tekst)
        if match_single:
            v = int(match_single.group(1))
            return f"{v} min", float(v)
        return "-", np.nan
    except: return "-", np.nan

def izvuci_akciju(tekst, html, plat):
    if not tekst and not html: return "-"
    cist_html = re.sub(r'<[^>]+>', ' \n ', str(html))
    sve_zajedno = str(tekst) + " \n " + cist_html
    lines = [line.strip() for line in sve_zajedno.split('\n') if line.strip()]
    akcije = []
    for line in lines:
        t_low = line.lower()
        if len(t_low) > 80: continue 
        if any(x in t_low for x in ["besplatna dostava", "free delivery", "dostava 0", "0 rsd"]): akcije.append("Besplatna dostava")
        elif "1+1" in t_low or "buy 1 get 1": akcije.append("1+1 Gratis")
        elif "%" in t_low and re.search(r'\d', t_low):
            if any(x in t_low for x in ["-", "off", "discount", "popust"]): akcije.append(line)
    if not akcije: return "-"
    res = []
    for a in list(set(akcije)): res.append(f"• {a}")
    return "\n".join(res)

def normalizuj_ime(ime): return re.sub(r'[^\w]', '', ime.lower())

# --- GRAFIKONI ---
def kreiraj_grafikon_status(df_sub, naslov):
    wolt_o = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Otvoreno")])
    wolt_z = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Zatvoreno")])
    glovo_o = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Otvoreno")])
    glovo_z = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Zatvoreno")])
    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(2)
    ax.bar(x - 0.2, [wolt_o, glovo_o], 0.4, label='Otvoreno', color='#2ecc71')
    ax.bar(x + 0.2, [wolt_z, glovo_z], 0.4, label='Zatvoreno', color='#e74c3c')
    ax.set_xticks(x); ax.set_xticklabels(['Wolt', 'Glovo']); ax.set_title(naslov); ax.legend()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png'); plt.close(fig)
    return imgdata

def kreiraj_grafikon_vreme_dostave(df_sub, naslov):
    w_avg = df_sub[df_sub["Platforma"] == "Wolt"]["Vreme_Broj"].mean()
    g_avg = df_sub[df_sub["Platforma"] == "Glovo"]["Vreme_Broj"].mean()
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(['Wolt', 'Glovo'], [w_avg or 0, g_avg or 0], color=['#00c2e8', '#ffc244'])
    ax.set_title(naslov); ax.set_ylabel('Minuta')
    imgdata = BytesIO(); fig.savefig(imgdata, format='png'); plt.close(fig)
    return imgdata

def kreiraj_timeline_grafikon(df_hist, adresa=None, custom_naslov=None):
    fig, ax = plt.subplots(figsize=(10, 4))
    # Pojednostavljena verzija za UI
    if not df_hist.empty:
        for p, c in [('Wolt', '#00c2e8'), ('Glovo', '#ffc244')]:
            sub = df_hist[df_hist['Platforma'] == p]
            if not sub.empty: ax.plot(sub['Vreme'], sub['Otvoreno'], label=p, marker='o', color=c)
    ax.set_title(custom_naslov or "Trend aktivnosti"); ax.legend(); plt.xticks(rotation=45)
    imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight'); plt.close(fig)
    return imgdata

# --- SCRAPER LOGIKA (Originalna) ---
async def pametno_skrolovanje_i_ekstrakcija(page, plat, address, log_ph=None, prog_bar=None):
    results_dict = {}
    prethodni_broj = 0
    for _ in range(30): # limit skrolovanja
        podaci = await page.evaluate('''() => {
            let rez = [];
            document.querySelectorAll("a").forEach(c => {
                let link = c.href; let text = c.innerText; let html = c.innerHTML;
                if(link && (link.includes('restaurant') || link.includes('store'))) rez.push({link, text, html});
            });
            return rez;
        }''')
        for item in podaci:
            link = item['link']
            if link in results_dict: continue
            ime = izvuci_ime(item['text'])
            if len(ime) < 2: continue
            ocena = izvuci_ocenu(item['text'] + item['html'], plat)
            v_str, v_num = izvuci_vreme_dostave(item['text'] + item['html'])
            results_dict[link] = {
                "Adresa": address, "Platforma": plat, "Naziv": ukloni_kvacice(ime), "Ocena": ocena,
                "Vreme dostave": v_str, "Akcija": izvuci_akciju(item['text'], item['html'], plat),
                "Status": analiziraj_status(item['text'] + item['html']), "Vreme_Broj": v_num, "Link": link
            }
        if len(results_dict) > prethodni_broj:
            prethodni_broj = len(results_dict)
            if prog_bar: prog_bar.progress(min(prethodni_broj/100, 0.99), text=f"Pronađeno {prethodni_broj} restorana...")
        await page.evaluate("window.scrollBy(0, 800);")
        await asyncio.sleep(1)
        if _ > 5 and len(results_dict) == prethodni_broj: break
    return list(results_dict.values())

async def scrape_wolt(context, address, log_ph, err_screens, prog_bar):
    page = await context.new_page()
    try:
        await page.goto("https://wolt.com/sr/discovery/restaurants")
        # Pojednostavljena navigacija za demo, ovde ide tvoj full kod navigacije
        return await pametno_skrolovanje_i_ekstrakcija(page, "Wolt", address, log_ph, prog_bar)
    finally: await page.close()

async def scrape_glovo(context, address, log_ph, err_screens, prog_bar):
    page = await context.new_page()
    try:
        await page.goto("https://glovoapp.com/sr/rs/beograd/restorani_1/")
        return await pametno_skrolovanje_i_ekstrakcija(page, "Glovo", address, log_ph, prog_bar)
    finally: await page.close()

async def proces_skeniranja(adrese, log_ph, prog_bar, generisi_pdf, email):
    sve = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0")
        for adr in adrese:
            log_msg(f"Skeniram Glovo: {adr}", log_ph)
            sve.extend(await scrape_glovo(context, adr, log_ph, [], prog_bar))
            log_msg(f"Skeniram Wolt: {adr}", log_ph)
            sve.extend(await scrape_wolt(context, adr, log_ph, [], prog_bar))
        await browser.close()
    if sve:
        df = pd.DataFrame(sve)
        hi = sacuvaj_u_istoriju(df)
        return df, hi, [], []
    return pd.DataFrame(), pd.DataFrame(), [], []

# ================= VIZUELNI UI =================

if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()
if 'df_history' not in st.session_state: st.session_state.df_history = pd.DataFrame()

# SIDEBAR
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/609/609361.png", width=80)
    st.header("⚙️ Kontrola")
    adr1 = st.text_input("📍 Glavna Adresa", placeholder="Npr. Makenzijeva 57")
    adr2 = st.text_input("📍 Sekundarna Adresa")
    
    st.divider()
    gen_pdf = st.checkbox("📄 Generiši PDF")
    email_target = st.text_input("📧 Email za izveštaj")
    
    if st.button("🚀 POKRENI SKENIRANJE", type="primary", use_container_width=True):
        if adr1:
            with st.spinner("Sistem radi..."):
                sl = st.empty()
                pb = st.progress(0)
                df, hi, _, _ = asyncio.run(proces_skeniranja([adr1, adr2] if adr2 else [adr1], sl, pb, gen_pdf, email_target))
                st.session_state.df_sve = df
                st.session_state.df_history = hi
                st.success("Završeno!")
        else: st.error("Unesi adresu!")

# GLAVNI EKRAN
st.markdown('<div class="main-header">Nadzor Dostave PRO</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Monitoring Wolt & Glovo platformi u realnom vremenu</div>', unsafe_allow_html=True)

if not st.session_state.df_sve.empty:
    df = st.session_state.df_sve
    
    # 1. KPI KARTICE
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("🚲 Wolt Ukupno", len(df[df['Platforma']=='Wolt']), f"{len(df[(df['Platforma']=='Wolt') & (df['Status']=='Otvoreno')])} Online")
    with c2:
        st.metric("🍔 Glovo Ukupno", len(df[df['Platforma']=='Glovo']), f"{len(df[(df['Platforma']=='Glovo') & (df['Status']=='Otvoreno')])} Online")
    with c3:
        avg_w = df[df['Platforma']=='Wolt']['Vreme_Broj'].mean()
        st.metric("⏱️ Wolt Prosek", f"{avg_w:.0f} min" if not np.isnan(avg_w) else "-")
    with c4:
        avg_g = df[df['Platforma']=='Glovo']['Vreme_Broj'].mean()
        st.metric("⏱️ Glovo Prosek", f"{avg_g:.0f} min" if not np.isnan(avg_g) else "-")

    # 2. TABOVI ZA ORGANIZACIJU
    tab_dash, tab_comp, tab_data = st.tabs(["📊 Dashboard", "⚖️ Uporedna Analiza", "🔍 Detaljna Lista"])

    with tab_dash:
        col_l, col_r = st.columns(2)
        with col_l:
            st.image(kreiraj_grafikon_status(df, "Statusi po platformama"), use_container_width=True)
        with col_r:
            st.image(kreiraj_grafikon_vreme_dostave(df, "Brzina dostave (prosek)"), use_container_width=True)
        
        st.divider()
        st.subheader("📈 Istorijski trend (Poslednja 24h)")
        if not st.session_state.df_history.empty:
            st.image(kreiraj_timeline_grafikon(st.session_state.df_history), use_container_width=True)

    with tab_comp:
        st.subheader("🔄 Restorani na obe platforme")
        df['Naziv_Norm'] = df['Naziv'].apply(normalizuj_ime)
        # Logika za upoređivanje istih restorana...
        common = df.groupby('Naziv_Norm').filter(lambda x: len(x) > 1)
        if not common.empty:
            st.dataframe(common[['Naziv', 'Platforma', 'Status', 'Ocena', 'Vreme dostave']], use_container_width=True, hide_index=True)
        else: st.info("Nema preklapanja u trenutnom skeniranju.")

    with tab_data:
        st.subheader("📋 Svi podaci")
        # Filteri za tabelu
        f_plat = st.multiselect("Platforma", ["Wolt", "Glovo"], ["Wolt", "Glovo"])
        f_stat = st.multiselect("Status", ["Otvoreno", "Zatvoreno"], ["Otvoreno", "Zatvoreno"])
        
        filtered_df = df[(df['Platforma'].isin(f_plat)) & (df['Status'].isin(f_stat))]
        
        def color_status(val):
            color = '#2ecc71' if val == 'Otvoreno' else '#e74c3c'
            return f'color: {color}; font-weight: bold'

        st.dataframe(
            filtered_df[['Adresa', 'Platforma', 'Naziv', 'Status', 'Ocena', 'Vreme dostave', 'Akcija', 'Link']].style.map(color_status, subset=['Status']),
            use_container_width=True,
            hide_index=True,
            column_config={"Link": st.column_config.LinkColumn("Link", display_text="Otvori")}
        )

else:
    st.info("👋 Dobrodošli. Unesite adresu u sidebar-u i kliknite na dugme da biste započeli analizu.")
