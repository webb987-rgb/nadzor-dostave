import asyncio
import datetime
import os
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from io import BytesIO
from pathlib import Path
from playwright.async_api import async_playwright
import time
import streamlit as st
import sys

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

# ================= FIX ZA WINDOWS I PLAYWRIGHT =================
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ================= INSTALACIJA BROWSERA NA CLOUD-u =================
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

# ---------------- POMOĆNE FUNKCIJE ----------------
def timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def format_time_short():
    return datetime.datetime.now().strftime("%H:%M")

def log_msg(msg, placeholder=None):
    if placeholder:
        placeholder.text(msg)

def cirilica_u_latinicu(tekst):
    if not tekst: return ""
    mapa = {
        'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s',
        'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'Њ':'Nj', 'О':'O', 'П':'P', 'Р':'R', 'С':'S', 'Т':'T', 'Ћ':'C', 'У':'U', 'Ф':'F', 'Х':'H', 'Ц':'C', 'Ч':'C', 'Џ':'Dz', 'Ш':'S'
    }
    for k, v in mapa.items():
        tekst = tekst.replace(k, v)
    return tekst

# ---------------- ANALIZA STATUSA (PICKUP LOGIKA) ----------------
def analiziraj_status(tekst):
    if not tekst: return "Otvoreno"
    t = tekst.lower()
    
    zatvoreno_indikatori = [
        "samo preuzimanje", "samo za preuzimanje", "pickup only", 
        "dostava nije dostupna", "dostava trenutno nije", "samo licno preuzimanje",
        "zatvoreno", "zakažite", "zakaži", "zakazi", "nedostupno", "otvara se", "otvara", "closed", "schedule"
    ]
    
    if any(k in t for k in zatvoreno_indikatori):
        return "Zatvoreno"
    return "Otvoreno"

# ---------------- POMOĆNE ZA EKSTRAKCIJU ----------------
def izvuci_ocenu(tekst, plat):
    try:
        if not tekst: return "-"
        cist = re.sub(r'<[^>]+>', ' ', tekst).lower()
        if plat == "Glovo":
            p = re.findall(r'(\d{1,3})\s*%', cist)
            for x in p:
                if int(x) >= 60: return x + "%"
        elif plat == "Wolt":
            m = re.search(r'\b([5-9][.,][0-9]|10[.,]0)\b', cist)
            if m: return m.group(1).replace(',', '.')
        if re.search(r'\b(novo|new)\b', cist): return "Novo"
        return "-"
    except: return "-"

def izvuci_vreme(tekst):
    try:
        if not tekst: return "-", np.nan
        cist = re.sub(r'<[^>]+>', ' ', tekst).lower()
        m = re.search(r'(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(?:min|m|\')', cist)
        if m: return f"{m.group(1)}-{m.group(2)} min", (int(m.group(1)) + int(m.group(2))) / 2.0
        m2 = re.search(r'\b(\d{1,3})\s*(?:min|m|\')', cist)
        if m2: return f"{m2.group(1)} min", float(m2.group(1))
        return "-", np.nan
    except: return "-", np.nan

def normalizuj_ime(ime): return re.sub(r'[^\w]', '', ime.lower())

def izvuci_ime(tekst):
    if not tekst: return ""
    lines = tekst.split('\n')
    for line in lines:
        line = line.strip()
        if not line or '%' in line: continue
        line_lower = line.lower()
        if "min" in line_lower and re.search(r'\d+', line_lower): continue
        if "rsd" in line_lower or "din" in line_lower: continue
        if any(x in line_lower for x in ["promo", "novo", "odlično", "besplatna dostava", "artikli", "narudžb", "narudzb", "popust"]): continue
        if len(line) >= 2: return line
    return ""

# ---------------- MATPLOTLIB GRAFIKONI (STABILNA VERZIJA) ----------------
def kreiraj_grafikon_status(df_sub, naslov):
    wolt_o = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Otvoreno")])
    wolt_z = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Zatvoreno")])
    glovo_o = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Otvoreno")])
    glovo_z = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Zatvoreno")])
    
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.bar([0.2, 0.8], [wolt_o, wolt_z], width=0.35, color='#00c2e8', label='Wolt')
    ax.bar([1.2, 1.8], [glovo_o, glovo_z], width=0.35, color='#ffc244', label='Glovo')
    ax.set_xticks([0.5, 1.5])
    ax.set_xticklabels(['Wolt\n(Otv/Zat)', 'Glovo\n(Otv/Zat)'])
    ax.set_title(naslov, fontweight='bold')
    ax.legend(frameon=False)
    plt.tight_layout()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png'); plt.close(fig); return imgdata

def kreiraj_grafikon_vreme_dostave(df_sub, naslov):
    wolt_df = df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Vreme_Broj"].notna())]
    glovo_df = df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Vreme_Broj"].notna())]
    w_avg = wolt_df["Vreme_Broj"].mean() if not wolt_df.empty else 0
    g_avg = glovo_df["Vreme_Broj"].mean() if not glovo_df.empty else 0
    
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.bar(['Wolt', 'Glovo'], [w_avg, g_avg], color=['#00c2e8', '#ffc244'], width=0.5)
    ax.set_ylabel('Minuti')
    ax.set_title(naslov, fontweight='bold')
    for i, v in enumerate([w_avg, g_avg]):
        if v > 0: ax.text(i, v + 0.5, f"{v:.1f}", ha='center', fontweight='bold')
    plt.tight_layout()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png'); plt.close(fig); return imgdata

def kreiraj_timeline_grafikon(df_hist, adresa=None, custom_naslov=None, is_pdf=False):
    df_sub = df_hist.copy()
    if adresa:
        df_sub = df_sub[df_sub["Adresa"] == adresa]
        naslov = f'Istorijat - {adresa.upper()}'
    else:
        if not df_sub.empty:
            df_sub = df_sub.groupby(["Datum", "Vreme", "Platforma"]).sum(numeric_only=True).reset_index()
        naslov = 'Zbirni Istorijat (Sve Adrese)'
    if custom_naslov: naslov = custom_naslov

    fig, ax = plt.subplots(figsize=(10, 4))
    if df_sub.empty:
        ax.text(0.5, 0.5, "Nema podataka", ha='center')
    else:
        for plat, color in [("Wolt", "#00c2e8"), ("Glovo", "#ffc244")]:
            d = df_sub[df_sub["Platforma"] == plat]
            if is_pdf: d = d.tail(48)
            if not d.empty: ax.plot(d["Vreme"], d["Otvoreno"], marker='o', label=plat, color=color, linewidth=2)
        ax.set_title(naslov, fontweight='bold')
        ax.legend()
        plt.xticks(rotation=45)
    plt.tight_layout()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png'); plt.close(fig); return imgdata

# ---------------- PAMETNO SKROLOVANJE ----------------
async def pametno_skrolovanje_i_ekstrakcija(page, plat, address, log_ph=None):
    results_dict = {}
    prethodni_broj = 0; pokusaji = 0
    while True:
        if plat == "Wolt":
            podaci = await page.evaluate('''() => {
                let rez = [];
                document.querySelectorAll("a[data-test-id^='venueCard.']").forEach(c => {
                    let link = c.href; let text = c.innerText; let p = c.closest("li");
                    let html = p ? p.innerHTML : c.innerHTML; rez.push({link, text, html});
                });
                return rez;
            }''')
        else:
            podaci = await page.evaluate('''() => {
                let rez = [];
                document.querySelectorAll("a:has(h3), a[data-testid='store-card'], .store-card a").forEach(c => {
                    let link = c.href;
                    if (!link.includes('/dostava') && !link.includes('/category')) { rez.push({link: link, text: c.innerText, html: ""}); }
                });
                return rez;
            }''')

        for item in podaci:
            link = item['link']
            if not link or link in results_dict: continue
            text = item['text']
            sve_z = text + " " + item['html'] if plat == "Wolt" else text
            ime = ukloni_kvacice(izvuci_ime(text))
            if len(ime) < 2: continue
            
            ocena = izvuci_ocenu(sve_z, plat)
            vreme_str, vreme_num = izvuci_vreme(sve_z)
            
            results_dict[link] = {
                "Adresa": address, 
                "Platforma": plat, 
                "Naziv": ime, 
                "Ocena": ocena,
                "Vreme dostave": vreme_str,
                "Status": analiziraj_status(sve_z),
                "Vreme_Broj": vreme_num, 
                "Link": link
            }

        trenutni = len(results_dict)
        if trenutni > prethodni_broj:
            log_msg(f"[{plat.upper()} - {address}] Učitano {trenutni} restorana...", log_ph)
            prethodni_broj = trenutni; pokusaji = 0
        
        await page.evaluate("window.scrollBy(0, window.innerHeight);")
        await asyncio.sleep(0.8)
        
        h = await page.evaluate("document.body.scrollHeight")
        s = await page.evaluate("window.scrollY + window.innerHeight")
        if s >= h - 100:
            pokusaji += 1
            await asyncio.sleep(1.5)
            if pokusaji >= 5: break
        else: pokusaji = 0
    return list(results_dict.values())

# ---------------- SCRAPERS (VRAĆENA ORIGINALNA LOGIKA) ----------------
async def scrape_wolt(browser, address, log_ph=None):
    try:
        context = await browser.new_context(permissions=['geolocation'])
        page = await context.new_page()
        await page.goto("https://wolt.com/sr/srb")
        try: await page.locator("[data-test-id='allow-button']").click(timeout=3000)
        except: pass
        input_f = page.get_by_role("combobox")
        await input_f.click()
        await input_f.fill(address)
        await asyncio.sleep(2)
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        await asyncio.sleep(5)
        await page.goto("https://wolt.com/sr/discovery/restaurants")
        try: await page.wait_for_selector("a[data-test-id^='venueCard.']", timeout=10000)
        except: return []

        rezultati = await pametno_skrolovanje_i_ekstrakcija(page, "Wolt", address, log_ph)
        await context.close()
        return rezultati
    except Exception as e: 
        log_msg(f"[WOLT GREŠKA] {e}", log_ph)
        return []

async def scrape_glovo(browser, address, log_ph=None):
    try:
        context = await browser.new_context(permissions=['geolocation'])
        page = await context.new_page()
        await page.goto("https://glovoapp.com/sr/rs")
        try: await page.get_by_role("button", name=re.compile("Accept|Prihvati", re.I)).click(timeout=3000)
        except: pass
        await page.locator("#hero-container-input").click()
        search = page.get_by_role("searchbox")
        await search.fill(address)
        try:
            dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
            await dropdown_item.wait_for(state="visible", timeout=8000)
            await dropdown_item.click()
        except: await page.keyboard.press("Enter")
        try:
            btn_drugo = page.locator("button:has-text('Drugo')")
            await btn_drugo.wait_for(state="visible", timeout=4000)
            await btn_drugo.click()
        except: pass
        try:
            btn_potvrdi = page.locator("button:has-text('Potvrdi adresu')")
            await btn_potvrdi.wait_for(state="visible", timeout=4000)
            await btn_potvrdi.click()
        except: pass
        await asyncio.sleep(5)
        try:
            btn_pocetna = page.locator("text='Idi na početnu stranicu'")
            if await btn_pocetna.count() > 0 and await btn_pocetna.first.is_visible(timeout=3000):
                await btn_pocetna.first.click()
                await asyncio.sleep(5) 
        except: pass
        try:
            kat_link = page.get_by_role("link", name=re.compile(r"Restorani|Hrana", re.I)).first
            await kat_link.wait_for(state="visible", timeout=7000)
            await kat_link.click()
        except: pass
        await asyncio.sleep(5)

        rezultati = await pametno_skrolovanje_i_ekstrakcija(page, "Glovo", address, log_ph)
        await context.close()
        return rezultati
    except Exception as e: 
        log_msg(f"[GLOVO GREŠKA] {e}", log_ph)
        return []

# ---------------- PROCES SKENIRANJA ----------------
async def proces_skeniranja(adrese, log_ph):
    sve = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for adr in adrese:
            log_msg(f"📍 Skeniram: {adr}", log_ph)
            r = await asyncio.gather(scrape_wolt(browser, adr, log_ph), scrape_glovo(browser, adr, log_ph))
            sve.extend(r[0] + r[1])
        await browser.close()
    if sve:
        df = pd.DataFrame(sve)
        vreme, datum = format_time_short(), datetime.datetime.now().strftime("%Y-%m-%d")
        novi = []
        for adr in df["Adresa"].unique():
            for plat in ["Wolt", "Glovo"]:
                sub = df[(df["Adresa"] == adr) & (df["Platforma"] == plat)]
                novi.append({"Datum": datum, "Vreme": vreme, "Adresa": adr, "Platforma": plat, "Otvoreno": len(sub[sub["Status"] == "Otvoreno"]), "Zatvoreno": len(sub[sub["Status"] == "Zatvoreno"])})
        df_n = pd.DataFrame(novi)
        if os.path.exists(HISTORY_FILE): df_s = pd.read_csv(HISTORY_FILE); df_h = pd.concat([df_s, df_n], ignore_index=True)
        else: df_h = df_n
        df_h.to_csv(HISTORY_FILE, index=False); return df, df_h
    return pd.DataFrame(), pd.DataFrame()

# ================= STREAMLIT UI =================
if 'pokrenuto' not in st.session_state: st.session_state.pokrenuto = False
if 'last_run' not in st.session_state: st.session_state.last_run = 0
if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()
if 'df_history' not in st.session_state: st.session_state.df_history = pd.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else pd.DataFrame()

st.title("🍔 Nadzor Dostave (Wolt & Glovo)")

with st.sidebar:
    st.header("⚙️ Podešavanja")
    adrese_input = st.text_area("📍 Adrese:", value="", placeholder="Primer:\nMakenzijeva 57, Beograd")
    sleep_interval = st.number_input("⏱️ Interval (min):", min_value=1, value=15)
    
    # STATICNI TAJMER
    timer_ph = st.empty()
    
    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ Pokreni", type="primary"): st.session_state.pokrenuto = True; st.session_state.last_run = 0; st.rerun()
    with c2:
        if st.button("⏹️ Zaustavi"): st.session_state.pokrenuto = False; st.rerun()
    
    st.markdown("---")
    if st.button("🗑️ Obriši istoriju"):
        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
        st.session_state.df_history = pd.DataFrame(); st.rerun()

if st.session_state.pokrenuto:
    lista_adresa = [cirilica_u_latinicu(a.strip()) for a in adrese_input.split('\n') if a.strip()]
    if not lista_adresa: st.warning("Unesite adresu!"); st.session_state.pokrenuto = False; st.stop()

    now = time.time()
    if now - st.session_state.last_run >= sleep_interval * 60:
        sl = st.empty()
        with st.spinner("Skeniranje..."):
            df, hi = asyncio.run(proces_skeniranja(lista_adresa, sl))
            st.session_state.df_sve, st.session_state.df_history, st.session_state.last_run = df, hi, time.time()
        sl.empty(); st.rerun()

    df = st.session_state.df_sve
    if not df.empty:
        # OSIGURAČ ZA STARE PODATKE
        for col in ["Vreme_Broj", "Vreme dostave", "Ocena", "Link"]:
            if col not in df.columns: df[col] = np.nan if "Broj" in col else "-"

        st.success(f"✅ Poslednje osvežavanje: {datetime.datetime.fromtimestamp(st.session_state.last_run).strftime('%H:%M:%S')}")

        # --- 1. TABELA NA VRHU ---
        st.subheader("📊 Rezime Skeniranja")
        rez_data = []
        for adr in df["Adresa"].unique():
            for p in ["Wolt", "Glovo"]:
                sub = df[(df["Adresa"] == adr) & (df["Platforma"] == p)]
                if not sub.empty:
                    rez_data.append({"Adresa": adr, "Platforma": p, "Ukupno": len(sub), "Otvoreno": len(sub[sub["Status"]=="Otvoreno"]), "Zatvoreno": len(sub[sub["Status"]=="Zatvoreno"])})
        st.table(pd.DataFrame(rez_data))
        
        # --- 2. GRAFIKONI ---
        st.markdown("---")
        adrese_un = list(df["Adresa"].unique())
        graf_adr = st.selectbox("📍 Filtriraj Grafikone po Adresi:", ["Sve adrese"] + adrese_un, index=1 if len(adrese_un)==1 else 0)
        c_df = df if graf_adr == "Sve adrese" else df[df["Adresa"] == graf_adr]
        
        col1, col2 = st.columns(2)
        with col1: st.image(kreiraj_grafikon_status(c_df, f"Status - {graf_adr}"), use_container_width=True)
        with col2: st.image(kreiraj_grafikon_vreme_dostave(c_df, "Prosečno vreme dostave"), use_container_width=True)
        
        h_df = st.session_state.df_history
        if not h_df.empty:
            c_h = h_df if graf_adr == "Sve adrese" else h_df[h_df["Adresa"] == graf_adr]
            st.image(kreiraj_timeline_grafikon(c_h, None, "Istorijat aktivnosti"), use_container_width=True)

        # --- 3. UPOREDNI PRIKAZ ---
        st.markdown("---")
        st.subheader("⚖️ Uporedni Prikaz (Restorani na obe platforme)")
        df['Naziv_Norm'] = df['Naziv'].apply(normalizuj_ime)
        uporedni = []
        for adr in df['Adresa'].unique():
            df_a = df[df['Adresa'] == adr]
            wolt_df = df_a[df_a['Platforma'] == 'Wolt']; glovo_df = df_a[df_a['Platforma'] == 'Glovo']
            zajednicki = set(wolt_df['Naziv_Norm']).intersection(set(glovo_df['Naziv_Norm']))
            for n in zajednicki:
                w = wolt_df[wolt_df['Naziv_Norm'] == n].iloc[0]; g = glovo_df[glovo_df['Naziv_Norm'] == n].iloc[0]
                uporedni.append({"Adresa": adr, "Restoran": w['Naziv'], "Status Wolt": w['Status'], "Vreme Wolt": w['Vreme dostave'], "Status Glovo": g['Status'], "Vreme Glovo": g['Vreme dostave']})
        if uporedni: st.dataframe(pd.DataFrame(uporedni), use_container_width=True, hide_index=True)

        # --- 4. DETALJNA LISTA ---
        st.markdown("---")
        st.subheader("🔍 Detaljna Lista")
        f1, f2, f3 = st.columns(3)
        with f1: fa = st.multiselect("📍 Adresa", df["Adresa"].unique(), df["Adresa"].unique())
        with f2: fp = st.multiselect("📱 Platforma", df["Platforma"].unique(), df["Platforma"].unique())
        with f3: fs = st.multiselect("🚦 Status", ["Otvoreno", "Zatvoreno"], ["Otvoreno", "Zatvoreno"])
        f_df = df[(df["Adresa"].isin(fa)) & (df["Platforma"].isin(fp)) & (df["Status"].isin(fs))]
        st.dataframe(f_df.drop(columns=['Naziv_Norm', 'Vreme_Broj'], errors='ignore').style.map(lambda v: f'color: {"#27ae60" if v=="Otvoreno" else "#e74c3c"}; font-weight: bold;', subset=['Status']), use_container_width=True, hide_index=True, column_config={"Link": st.column_config.LinkColumn("Link", display_text="Otvori na sajtu")})

    # TAJMER
    rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
    if rem > 0:
        mins, secs = divmod(rem, 60)
        timer_ph.metric("Sledeće skeniranje", f"{mins:02d}:{secs:02d}")
        time.sleep(1); st.rerun()
