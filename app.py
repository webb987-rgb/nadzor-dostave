import asyncio
import datetime
import os
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import plotly.express as px
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
st.set_page_config(page_title="Nadzor Dostave PRO", page_icon="📊", layout="wide")

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

# ---------------- PLOTLY INTERAKTIVNI GRAFIKONI ----------------
def kreiraj_plotly_status(df_sub, naslov):
    wolt_o = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Otvoreno")])
    wolt_z = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Zatvoreno")])
    glovo_o = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Otvoreno")])
    glovo_z = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Zatvoreno")])
    
    fig = go.Figure()
    if "Wolt" in df_sub["Platforma"].values:
        fig.add_trace(go.Bar(name='Wolt', x=['Otvoreno', 'Zatvoreno'], y=[wolt_o, wolt_z], marker_color='#00c2e8'))
    if "Glovo" in df_sub["Platforma"].values:
        fig.add_trace(go.Bar(name='Glovo', x=['Otvoreno', 'Zatvoreno'], y=[glovo_o, glovo_z], marker_color='#ffc244'))
        
    fig.update_layout(title=naslov, barmode='group', height=350, margin=dict(l=20, r=20, t=40, b=20), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    return fig

def kreiraj_plotly_vreme_dostave(df_sub, naslov):
    wolt_df = df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Vreme_Broj"].notna())]
    glovo_df = df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Vreme_Broj"].notna())]
    
    w_avg = round(wolt_df["Vreme_Broj"].mean(), 1) if not wolt_df.empty else 0
    g_avg = round(glovo_df["Vreme_Broj"].mean(), 1) if not glovo_df.empty else 0
    
    fig = go.Figure()
    fig.add_trace(go.Bar(x=['Wolt', 'Glovo'], y=[w_avg, g_avg], marker_color=['#00c2e8', '#ffc244'], text=[f"{w_avg} min", f"{g_avg} min"], textposition='auto'))
    fig.update_layout(title=naslov, height=350, margin=dict(l=20, r=20, t=40, b=20), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    return fig

def kreiraj_plotly_timeline(df_hist, naslov):
    if df_hist.empty: return go.Figure()
    
    # Grupisanje podataka za Area Chart
    df_sub = df_hist.groupby(["Datum", "Vreme", "Platforma"]).sum(numeric_only=True).reset_index()
    df_sub["Vremenska_Tacka"] = df_sub["Datum"].str[-5:] + " " + df_sub["Vreme"]
    
    fig = go.Figure()
    for plat, color in [("Wolt", "#00c2e8"), ("Glovo", "#ffc244")]:
        d = df_sub[df_sub["Platforma"] == plat]
        if not d.empty:
            fig.add_trace(go.Scatter(x=d["Vremenska_Tacka"], y=d["Otvoreno"], name=plat, line_shape='spline', fill='tozeroy', line=dict(width=3, color=color)))
            
    fig.update_layout(title=naslov, height=400, margin=dict(l=20, r=20, t=40, b=20), hovermode="x unified", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    return fig

# ---------------- MATPLOTLIB ZA PDF (BACKEND) ----------------
def kreiraj_grafikon_status_pdf(df_sub, naslov):
    wolt_o = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Otvoreno")])
    wolt_z = len(df_sub[(df_sub["Platforma"] == "Wolt") & (df_sub["Status"] == "Zatvoreno")])
    glovo_o = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Otvoreno")])
    glovo_z = len(df_sub[(df_sub["Platforma"] == "Glovo") & (df_sub["Status"] == "Zatvoreno")])
    fig, ax = plt.subplots(figsize=(5, 4)); ax.bar([0.2, 0.8], [wolt_o, wolt_z], width=0.35, color='#00c2e8', label='Wolt'); ax.bar([1.2, 1.8], [glovo_o, glovo_z], width=0.35, color='#ffc244', label='Glovo'); ax.set_xticks([0.5, 1.5]); ax.set_xticklabels(['Wolt', 'Glovo']); ax.set_title(naslov); imgdata = BytesIO(); fig.savefig(imgdata, format='png'); plt.close(fig); return imgdata

def kreiraj_timeline_pdf(df_hist, adresa=None):
    df_sub = df_hist.tail(48).copy()
    fig, ax = plt.subplots(figsize=(10, 4)); ax.plot(df_sub["Vreme"], df_sub["Otvoreno"], marker='o'); plt.xticks(rotation=45); imgdata = BytesIO(); fig.savefig(imgdata, format='png'); plt.close(fig); return imgdata

# ---------------- EMAIL FUNKCIJA ----------------
def posalji_email(pdf_putanje, primaoci_str, log_ph=None):
    lista_primaoca = [e.strip() for e in primaoci_str.split(",") if e.strip()]
    if not lista_primaoca: return
    try:
        for primalac in lista_primaoca:
            msg = MIMEMultipart(); msg['From'] = EMAIL_POSILJAOCA; msg['To'] = primalac; msg['Subject'] = f"Izveštaj - {datetime.datetime.now().strftime('%H:%M')}"
            msg.attach(MIMEText("U prilogu su izveštaji.", 'plain'))
            for p in pdf_putanje:
                with open(p, "rb") as f:
                    part = MIMEBase('application', 'octet-stream'); part.set_payload(f.read()); encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f"attachment; filename= {os.path.basename(p)}"); msg.attach(part)
            s = smtplib.SMTP('smtp.gmail.com', 587); s.starttls(); s.login(EMAIL_POSILJAOCA, LOZINKA_POSILJAOCA); s.sendmail(EMAIL_POSILJAOCA, primalac, msg.as_string()); s.quit()
    except Exception as e: log_msg(f"Greška email: {e}", log_ph)

# ---------------- ANALIZA PODATAKA ----------------
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

async def scrape_wolt(browser, adr, log_ph):
    try:
        ctx = await browser.new_context(permissions=['geolocation']); page = await ctx.new_page(); await page.goto("https://wolt.com/sr/srb")
        try: await page.locator("[data-test-id='allow-button']").click(timeout=3000)
        except: pass
        f = page.get_by_role("combobox"); await f.click(); await f.fill(adr); await asyncio.sleep(2); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter")
        await asyncio.sleep(5); await page.goto("https://wolt.com/sr/discovery/restaurants")
        try: await page.wait_for_selector("a[data-test-id^='venueCard.']", timeout=10000)
        except: return []
        
        # Ekstrakcija
        rez = []
        podaci = await page.evaluate('''() => {
            let items = [];
            document.querySelectorAll("a[data-test-id^='venueCard.']").forEach(c => {
                let p = c.closest("li");
                items.push({link: c.href, text: c.innerText, html: p ? p.innerHTML : ""});
            });
            return items;
        }''')
        for item in podaci:
            ime = cirilica_u_latinicu(re.split(r'\n', item['text'])[0])
            sve_z = item['text'] + " " + item['html']
            v_str, v_num = izvuci_vreme(sve_z)
            status = "Zatvoreno" if any(x in sve_z.lower() for x in ["zatvoreno", "zakazi", "nedostupno"]) else "Otvoreno"
            rez.append({"Adresa": adr, "Platforma": "Wolt", "Naziv": ime, "Ocena": izvuci_ocenu(sve_z, "Wolt"), "Vreme dostave": v_str, "Status": status, "Vreme_Broj": v_num, "Link": item['link']})
        await ctx.close(); return rez
    except: return []

async def scrape_glovo(browser, adr, log_ph):
    try:
        ctx = await browser.new_context(permissions=['geolocation']); page = await ctx.new_page(); await page.goto("https://glovoapp.com/sr/rs")
        try: await page.get_by_role("button", name=re.compile("Accept|Prihvati", re.I)).click(timeout=3000)
        except: pass
        await page.locator("#hero-container-input").click(); s = page.get_by_role("searchbox"); await s.fill(adr)
        try: d = page.locator("div[data-actionable='true'][role='button']").first; await d.wait_for(state="visible", timeout=8000); await d.click()
        except: await page.keyboard.press("Enter")
        await asyncio.sleep(6)
        try: k = page.get_by_role("link", name=re.compile(r"Restorani|Hrana", re.I)).first; await k.wait_for(state="visible", timeout=7000); await k.click()
        except: pass
        await asyncio.sleep(5)
        
        rez = []
        podaci = await page.evaluate('''() => {
            let items = [];
            document.querySelectorAll("a:has(h3), a[data-testid='store-card']").forEach(c => {
                items.push({link: c.href, text: c.innerText, html: c.innerHTML});
            });
            return items;
        }''')
        for item in podaci:
            ime = cirilica_u_latinicu(item['text'].split('\n')[0])
            v_str, v_num = izvuci_vreme(item['text'])
            status = "Zatvoreno" if any(x in item['text'].lower() for x in ["zatvoreno", "zakazi", "nedostupno"]) else "Otvoreno"
            rez.append({"Adresa": adr, "Platforma": "Glovo", "Naziv": ime, "Ocena": izvuci_ocenu(item['text'], "Glovo"), "Vreme dostave": v_str, "Status": status, "Vreme_Broj": v_num, "Link": item['link']})
        await ctx.close(); return rez
    except: return []

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
        df = pd.DataFrame(sve); df_h = sacuvaj_u_istoriju(df)
        return df, df_h, []
    return pd.DataFrame(), pd.DataFrame(), []

def sacuvaj_u_istoriju(df):
    vreme, datum = format_time_short(), datetime.datetime.now().strftime("%Y-%m-%d")
    novi = []
    for adr in df["Adresa"].unique():
        for plat in ["Wolt", "Glovo"]:
            sub = df[(df["Adresa"] == adr) & (df["Platforma"] == plat)]
            novi.append({"Datum": datum, "Vreme": vreme, "Adresa": adr, "Platforma": plat, "Otvoreno": len(sub[sub["Status"] == "Otvoreno"]), "Zatvoreno": len(sub[sub["Status"] == "Zatvoreno"])})
    df_n = pd.DataFrame(novi)
    if os.path.exists(HISTORY_FILE): df_s = pd.read_csv(HISTORY_FILE); df_comb = pd.concat([df_s, df_n], ignore_index=True)
    else: df_comb = df_n
    df_comb.to_csv(HISTORY_FILE, index=False); return df_comb

# ================= STREAMLIT UI =================
if 'pokrenuto' not in st.session_state: st.session_state.pokrenuto = False
if 'last_run' not in st.session_state: st.session_state.last_run = 0
if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()
if 'df_history' not in st.session_state: 
    st.session_state.df_history = pd.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else pd.DataFrame()

st.title("🍔 Nadzor Dostave (Wolt & Glovo)")

with st.sidebar:
    st.header("⚙️ Podešavanja")
    adrese_input = st.text_area("📍 Adrese:", value="", placeholder="Primer:\nMakenzijeva 57, Beograd")
    sleep_interval = st.number_input("⏱️ Interval (min):", min_value=1, value=15)
    
    # STATICNI TAJMER
    timer_ph = st.empty()
    
    slanje_maila = st.checkbox("✉️ Email", value=False)
    email_adrese = st.text_input("Mejlovi:", value="")
    
    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ Pokreni", type="primary"): st.session_state.pokrenuto = True; st.session_state.last_run = 0; st.rerun()
    with c2:
        if st.button("⏹️ Zaustavi"): st.session_state.pokrenuto = False; st.rerun()
    
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
            df, hi, pdf = asyncio.run(proces_skeniranja(lista_adresa, sl))
            st.session_state.df_sve, st.session_state.df_history, st.session_state.last_run = df, hi, time.time()
            if slanje_maila and not df.empty: posalji_email([], email_adrese, sl)
        sl.empty(); st.rerun()

    df = st.session_state.df_sve
    if not df.empty:
        # 1. TABELA NA VRHU (KAKO SI TRAŽIO)
        st.subheader("📊 Rezime Skeniranja")
        rez_data = []
        for adr in df["Adresa"].unique():
            for p in ["Wolt", "Glovo"]:
                sub = df[(df["Adresa"] == adr) & (df["Platforma"] == p)]
                if not sub.empty:
                    rez_data.append({"Adresa": adr, "Platforma": p, "Ukupno": len(sub), "Otvoreno": len(sub[sub["Status"]=="Otvoreno"]), "Zatvoreno": len(sub[sub["Status"]=="Zatvoreno"])})
        st.table(pd.DataFrame(rez_data))
        
        # 2. INTERAKTIVNI GRAFIKONI
        st.subheader("📈 Vizuelna Analiza")
        adrese_un = list(df["Adresa"].unique())
        graf_adr = st.selectbox("📍 Filtriraj po Adresi:", ["Sve adrese"] + adrese_un, index=1 if len(adrese_un)==1 else 0)
        
        c_df = df if graf_adr == "Sve adrese" else df[df["Adresa"] == graf_adr]
        
        col_g1, col_g2 = st.columns(2)
        with col_g1: st.plotly_chart(kreiraj_plotly_status(c_df, f"Status - {graf_adr}"), use_container_width=True)
        with col_g2: st.plotly_chart(kreiraj_plotly_vreme_dostave(c_df, "Prosečno vreme (min)"), use_container_width=True)
        
        # 3. ISTORIJAT (AREA CHART)
        h_df = st.session_state.df_history
        if not h_df.empty:
            c_h = h_df if graf_adr == "Sve adrese" else h_df[h_df["Adresa"] == graf_adr]
            st.plotly_chart(kreiraj_plotly_timeline(c_h, "Istorijat aktivnosti (Area Spline)"), use_container_width=True)

        # 4. LISTE RESTORANA
        st.subheader("🔍 Detaljna Lista")
        st.dataframe(c_df.drop(columns=["Vreme_Broj"]).style.map(lambda v: f'color: {"#27ae60" if v=="Otvoreno" else "#e74c3c"}; font-weight: bold;', subset=['Status']), use_container_width=True, hide_index=True)

    # TAJMER MM:SS U SIDEBARU
    rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
    if rem > 0:
        mins, secs = divmod(rem, 60)
        timer_ph.metric("Sledeće skeniranje", f"{mins:02d}:{secs:02d}")
        time.sleep(1); st.rerun()
    else: st.rerun()
else:
    st.info("Sistem zaustavljen. Unesite adrese i kliknite Pokreni.")
