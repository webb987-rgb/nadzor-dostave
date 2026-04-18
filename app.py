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
# ========================================================

def timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def format_time_short():
    return datetime.datetime.now().strftime("%H:%M")

def log_msg(msg, placeholder=None):
    print(msg)
    if placeholder:
        placeholder.text(msg)

# ---------------- PODRŠKA ZA ĆIRILICU I SPECIJALNA SLOVA ----------------
def cirilica_u_latinicu(tekst):
    if not tekst: return ""
    mapa = {
        'а':'a', 'б':'b', 'в':'v', 'г':'g', 'д':'d', 'ђ':'dj', 'е':'e', 'ж':'z', 'з':'z', 'и':'i', 'ј':'j', 'к':'k', 'л':'l', 'љ':'lj', 'м':'m', 'н':'n', 'њ':'nj', 'о':'o', 'п':'p', 'р':'r', 'с':'s', 'т':'t', 'ћ':'c', 'у':'u', 'ф':'f', 'х':'h', 'ц':'c', 'ч':'c', 'џ':'dz', 'ш':'s',
        'А':'A', 'Б':'B', 'В':'V', 'Г':'G', 'Д':'D', 'Ђ':'Dj', 'Е':'E', 'Ж':'Z', 'З':'Z', 'И':'I', 'Ј':'J', 'К':'K', 'Л':'L', 'Љ':'Lj', 'М':'M', 'Н':'N', 'Њ':'Nj', 'О':'O', 'П':'P', 'Р':'R', 'С':'S', 'Т':'T', 'Ћ':'C', 'У':'U', 'Ф':'F', 'Х':'H', 'Ц':'C', 'Ч':'C', 'Џ':'Dz', 'Ш':'S'
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
            msg['Subject'] = f"Izveštaji o dostavi - {datetime.datetime.now().strftime('%d.%m. u %H:%M')}"
            body = "Pozdrav šefe,\n\nU prilogu se nalaze zbirni i pojedinačni izveštaji o statusu restorana na platformama Wolt i Glovo.\n\nSistem je uspešno završio ciklus."
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
        log_msg("[USPEH] Svi emailovi su uspešno poslati!", log_ph)
    except Exception as e:
        log_msg(f"[GREŠKA] Slanje emaila nije uspelo: {e}", log_ph)

# ---------------- ISTORIJA I GRAFICI ----------------
def sacuvaj_u_istoriju(df):
    vreme_sada = format_time_short()
    datum_sada = datetime.datetime.now().strftime("%Y-%m-%d")
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
    return df_kombinovano

def kreiraj_timeline_grafikon(df_hist, adresa=None, custom_naslov=None):
    if adresa:
        df_sub = df_hist[df_hist["Adresa"] == adresa]
        naslov = f'Istorijat aktivnosti - {adresa.upper()}'
    else:
        df_sub = df_hist.groupby(["Datum", "Vreme", "Platforma"]).sum(numeric_only=True).reset_index()
        naslov = 'Zbirni Istorijat aktivnosti (Sve Adrese)'
        
    if custom_naslov:
        naslov = custom_naslov
        
    fig, ax = plt.subplots(figsize=(10, 4), facecolor='#ffffff')
    ax.set_facecolor('#f8f9fa')
    
    if len(df_sub) == 0:
        ax.text(0.5, 0.5, "Nema istorijskih podataka", ha='center', va='center')
        ax.axis('off')
    else:
        prikazi_wolt = "Wolt" in df_sub["Platforma"].values
        prikazi_glovo = "Glovo" in df_sub["Platforma"].values
        if prikazi_wolt:
            wolt_data = df_sub[df_sub["Platforma"] == "Wolt"].tail(48) 
            ax.plot(wolt_data["Vreme"], wolt_data["Otvoreno"], marker='o', markersize=4, linestyle='-', color='#00c2e8', linewidth=2.5, label='Wolt Otvoreni')
        if prikazi_glovo:
            glovo_data = df_sub[df_sub["Platforma"] == "Glovo"].tail(48)
            ax.plot(glovo_data["Vreme"], glovo_data["Otvoreno"], marker='s', markersize=4, linestyle='-', color='#ffc244', linewidth=2.5, label='Glovo Otvoreni')
        ax.set_ylabel('Broj otvorenih restorana', fontsize=11, fontweight='bold')
        ax.set_title(naslov, fontsize=14, fontweight='bold', color='#2c3e50', pad=15)
        ax.legend(frameon=True, fontsize=10, loc='lower center', bbox_to_anchor=(0.5, -0.3), ncol=2) 
        ax.grid(True, linestyle='--', alpha=0.6)
        for index, label in enumerate(ax.xaxis.get_ticklabels()):
            if index % 2 != 0: label.set_visible(False)
        plt.xticks(rotation=45, fontsize=9)
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
    fig, ax = plt.subplots(figsize=(5, 4), facecolor='#ffffff')
    prikazi_wolt = "Wolt" in df_sub["Platforma"].values or df_sub.empty
    prikazi_glovo = "Glovo" in df_sub["Platforma"].values or df_sub.empty
    if prikazi_wolt and prikazi_glovo:
        ax.bar([0, 1], [wolt_o, wolt_z], 0.35, label='Wolt', color='#00c2e8')
        ax.bar([0.35, 1.35], [glovo_o, glovo_z], 0.35, label='Glovo', color='#ffc244')
        ax.set_xticks([0.175, 1.175])
    elif prikazi_wolt:
        ax.bar([0, 1], [wolt_o, wolt_z], 0.35, label='Wolt', color='#00c2e8'); ax.set_xticks([0, 1])
    elif prikazi_glovo:
        ax.bar([0, 1], [glovo_o, glovo_z], 0.35, label='Glovo', color='#ffc244'); ax.set_xticks([0, 1])
    ax.set_xticklabels(['Otvoreno', 'Zatvoreno'])
    ax.legend(frameon=False); ax.set_title(naslov, fontsize=12, fontweight='bold', color='#2c3e50')
    imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150)
    imgdata.seek(0); plt.close(fig)
    return imgdata

# ---------------- ANALIZA I FORMATIRANJE ----------------
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
        if any(x in line_lower for x in ["promo", "novo", "odlično", "besplatna dostava", "artikli", "narudžb", "narudzb", "popust"]): continue
        if len(line) >= 2: return line
    return ""

def analiziraj_status(text):
    t = text.lower()
    ind = ["zatvoreno", "zakažite", "zakaži", "zakazi", "otvara se", "otvara", "closed", "schedule", "nedostupno"]
    if any(k in t for k in ind): return "Zatvoreno"
    return "Otvoreno"

def izvuci_ocenu(tekst, plat):
    """Funkcija koja pomoću Regularnih Ekspresija izvlači ocenu iz teksta"""
    if not tekst: return "-"
    tekst_lower = tekst.lower()
    if plat == "Glovo":
        match = re.search(r'(\d{1,3})\s*%', tekst_lower)
        if match: return match.group(1) + "%"
    elif plat == "Wolt":
        match = re.search(r'\b([5-9][.,][0-9]|10[.,]0)\b', tekst_lower)
        if match: return match.group(1).replace(',', '.')
    return "-"

def normalizuj_ime(ime): return re.sub(r'[^\w]', '', ime.lower())

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
            
            results_dict[link] = {
                "Adresa": address, 
                "Platforma": plat, 
                "Naziv": ime, 
                "Ocena": ocena,
                "Status": analiziraj_status(sve_z),
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

# ---------------- SCRAPERS ----------------
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

# ---------------- PDF LOGIC ----------------
def napravi_pdf_za_adresu(df_adr, adr, df_hist):
    p_path = str(OUTPUT_DIR / f"Izvestaj_{ukloni_kvacice(adr).replace(' ', '_')}_{timestamp()}.pdf")
    doc = SimpleDocTemplate(p_path, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    ns = ParagraphStyle('Naslov', parent=styles['Title'], textColor=colors.HexColor("#2c3e50"), fontSize=20, spaceAfter=10)
    ps = ParagraphStyle('Podnaslov', parent=styles['Heading2'], textColor=colors.HexColor("#2980b9"), fontSize=16, spaceBefore=20, spaceAfter=15)
    cs = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=10, leading=14)
    def fls(status):
        b = "#27ae60" if status == "Otvoreno" else "#e74c3c"
        return Paragraph(f"<font color='{b}' size=16>&bull;</font> {status}", cs)
    def fl(naziv, status):
        b = "#27ae60" if status == "Otvoreno" else "#e74c3c"
        return Paragraph(f"<font color='{b}' size=16>&bull;</font> {naziv}", cs)

    elements = [Paragraph(f"Izvestaj o Dostavi - {ukloni_kvacice(adr).upper()}", ns)]
    tab = [["Platforma", "Ukupno Nadjeno", "Otvoreno", "Zatvoreno"]]
    for plat in ["Wolt", "Glovo"]:
        sub = df_adr[df_adr["Platforma"] == plat]
        if not sub.empty: tab.append([plat, len(sub), len(sub[sub["Status"] == "Otvoreno"]), len(sub[sub["Status"] == "Zatvoreno"])])
    
    t_z = Table(tab, colWidths=[120, 100, 100, 100])
    t_z.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#34495e")),('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),('ALIGN', (0,0), (-1,-1), 'CENTER'),('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
    elements.extend([t_z, Spacer(1, 20), Table([[Image(kreiraj_grafikon_status(df_adr, f"Status - {adr}"), width=280, height=224)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), Spacer(1, 10), Table([[Image(kreiraj_timeline_grafikon(df_hist, adr), width=500, height=200)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), PageBreak()])

    wn = {normalizuj_ime(r["Naziv"]): r for _, r in df_adr[df_adr["Platforma"] == "Wolt"].iterrows()}
    gn = {normalizuj_ime(r["Naziv"]): r for _, r in df_adr[df_adr["Platforma"] == "Glovo"].iterrows()}
    sva = set(wn.keys()).union(set(gn.keys()))
    zaj = sorted([i for i in sva if i in wn and i in gn])
    sw = sorted([i for i in sva if i in wn and i not in gn]); sg = sorted([i for i in sva if i in gn and i not in wn])

    if zaj:
        elements.append(Paragraph("Zajednicki Restorani", ps))
        pz = [["Naziv", "Status Wolt", "Status Glovo"]]
        for n in zaj: pz.append([Paragraph(wn[n]["Naziv"], cs), fls(wn[n]["Status"]), fls(gn[n]["Status"])])
        t_c = Table(pz, colWidths=[200, 130, 130])
        t_c.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2c3e50")), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
        elements.extend([t_c, PageBreak()])
    if sw:
        elements.append(Paragraph("Ekskluzivno na Woltu", ps))
        pod = [["Naziv Restorana"]] + [[fl(wn[n]["Naziv"], wn[n]["Status"])] for n in sw]
        t_w = Table(pod, colWidths=[460]); t_w.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#3498db")), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
        elements.extend([t_w, PageBreak()])
    if sg:
        elements.append(Paragraph("Ekskluzivno na Glovu", ps))
        pod = [["Naziv Restorana"]] + [[fl(gn[n]["Naziv"], gn[n]["Status"])] for n in sg]
        t_g = Table(pod, colWidths=[460]); t_g.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f39c12")), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
        elements.append(t_g)
    doc.build(elements); return p_path

def napravi_zbirni_pdf(df, df_hist):
    p_path = str(OUTPUT_DIR / f"Zbirni_Izvestaj_{timestamp()}.pdf")
    doc = SimpleDocTemplate(p_path, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    ns = ParagraphStyle('Naslov', parent=styles['Title'], textColor=colors.HexColor("#2c3e50"), fontSize=20, spaceAfter=20)
    ps = ParagraphStyle('Podnaslov', parent=styles['Heading2'], textColor=colors.HexColor("#2980b9"), fontSize=16, spaceBefore=20, spaceAfter=15)
    elements = [Paragraph("Zbirni Izvestaj - Sve Adrese", ns), Table([[Image(kreiraj_grafikon_status(df, "Ukupni Status"), width=280, height=224)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), Spacer(1, 10), Table([[Image(kreiraj_timeline_grafikon(df_hist, None), width=500, height=200)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), PageBreak()]
    for adr in df["Adresa"].unique():
        df_a = df[df["Adresa"] == adr]
        elements.append(Paragraph(f"Statistika za lokaciju: {ukloni_kvacice(adr).upper()}", ps))
        tab = [["Platforma", "Ukupno Nadjeno", "Otvoreno", "Zatvoreno"]]
        for plat in ["Wolt", "Glovo"]:
            sub = df_a[df_a["Platforma"] == plat]
            if not sub.empty: tab.append([plat, len(sub), len(sub[sub["Status"] == "Otvoreno"]), len(sub[sub["Status"] == "Zatvoreno"])])
        t_a = Table(tab, colWidths=[120, 100, 100, 100])
        t_a.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#34495e")),('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),('ALIGN', (0,0), (-1,-1), 'CENTER'),('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
        elements.extend([t_a, Spacer(1, 15), Table([[Image(kreiraj_grafikon_status(df_a, f"Trenutni Status - {adr}"), width=280, height=224)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), Spacer(1, 20)])
    doc.build(elements); return p_path

# ---------------- CORE LOOP ----------------
async def run_platform_scraper(p_name, p, adr, log_ph):
    # Ostavljen fiksno headless=True kako server zahteva!
    browser = await p.chromium.launch(headless=True) 
    try:
        if p_name == "Wolt": return await scrape_wolt(browser, adr, log_ph)
        return await scrape_glovo(browser, adr, log_ph)
    finally: await browser.close()

async def proces_skeniranja(adrese, log_ph):
    sve = []
    async with async_playwright() as p:
        for adr in adrese:
            log_msg(f"\n[SISTEM] Pokrecem skeniranje za: {adr}", log_ph)
            r = await asyncio.gather(
                run_platform_scraper("Wolt", p, adr, log_ph), 
                run_platform_scraper("Glovo", p, adr, log_ph)
            )
            sve.extend(r[0] + r[1])
    if sve:
        df_s = pd.DataFrame(sve)
        df_h = sacuvaj_u_istoriju(df_s)
        log_msg("Generisem PDF izvestaje...", log_ph)
        pdf = [napravi_zbirni_pdf(df_s, df_h)] + [napravi_pdf_za_adresu(df_s[df_s["Adresa"] == adr], adr, df_h) for adr in df_s["Adresa"].unique()]
        return df_s, df_h, pdf
    return pd.DataFrame(), pd.DataFrame(), []

# ================= STREAMLIT UI =================
if 'pokrenuto' not in st.session_state: st.session_state.pokrenuto = False
if 'last_run' not in st.session_state: st.session_state.last_run = 0
if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()
if 'df_history' not in st.session_state: st.session_state.df_history = pd.DataFrame()
if 'pdf_fajlovi' not in st.session_state: st.session_state.pdf_fajlovi = []

st.title("🍔 Nadzor Dostave (Wolt & Glovo)")
with st.sidebar:
    st.header("⚙️ Podešavanja")
    # Uklonjen default value i dodat placeholder sa primerom
    adrese_input = st.text_area(
        "📍 Adrese (svaku u novi red):", 
        value="", 
        placeholder="Primer:\nMakenzijeva 57, Beograd\nBulevar Nemanjica 20, Nis"
    )
    sleep_interval = st.number_input("⏱️ Spavanje (min):", min_value=1, value=15)
    
    slanje_maila = st.checkbox("✉️ Email", value=False)
    email_adrese = st.text_input(
        "Mejlovi (odvojeni zarezom):", 
        value="", 
        placeholder="pera@gmail.com, mika@yahoo.com"
    ) if slanje_maila else ""
    
    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ Pokreni", type="primary"): 
            st.session_state.pokrenuto = True
            st.session_state.last_run = 0
            st.rerun()
    with c2:
        if st.button("⏹️ Zaustavi"): 
            st.session_state.pokrenuto = False
            st.rerun()

if st.session_state.pokrenuto:
    lista_adresa = [cirilica_u_latinicu(a.strip()) for a in adrese_input.split('\n') if a.strip()]
    
    if not lista_adresa:
        st.warning("⚠️ Molimo unesite bar jednu adresu za skeniranje!")
        st.session_state.pokrenuto = False
        st.rerun()

    now = time.time()
    time_since = now - st.session_state.last_run
    
    if time_since >= sleep_interval * 60 or st.session_state.last_run == 0:
        sl = st.empty()
        with st.spinner("Skeniranje u toku, molimo sačekajte..."):
            df, hi, pdf = asyncio.run(proces_skeniranja(lista_adresa, sl))
            st.session_state.df_sve, st.session_state.df_history, st.session_state.pdf_fajlovi, st.session_state.last_run = df, hi, pdf, time.time()
            if slanje_maila and pdf: posalji_email(pdf, email_adrese, sl)
        sl.empty()
        st.rerun()

    df = st.session_state.df_sve
    if not df.empty:
        st.success(f"✅ Osveženo u: {datetime.datetime.fromtimestamp(st.session_state.last_run).strftime('%H:%M:%S')}")
        
        # --- ZBIRNI GRAFIKONI ---
        st.subheader("📊 Interaktivni Grafikoni")
        g1, g2 = st.columns(2)
        with g1: graf_adr = st.selectbox("📍 Adresa:", ["Sve adrese"] + list(df["Adresa"].unique()))
        with g2: graf_pla = st.selectbox("📱 Platforma:", ["Sve platforme", "Wolt", "Glovo"])
        
        c_df, c_hi, n1, n2 = df.copy(), st.session_state.df_history.copy(), "Ukupni Status", "Istorijat"
        if graf_adr != "Sve adrese": c_df, c_hi, n1, n2 = c_df[c_df["Adresa"] == graf_adr], c_hi[c_hi["Adresa"] == graf_adr], n1 + f" - {graf_adr.upper()}", n2 + f" - {graf_adr.upper()}"
        if graf_pla != "Sve platforme": c_df, c_hi, n1, n2 = c_df[c_df["Platforma"] == graf_pla], c_hi[c_hi["Platforma"] == graf_pla], n1 + f" | {graf_pla}", n2 + f" | {graf_pla}"
        
        ca, cb = st.columns(2)
        with ca: st.image(kreiraj_grafikon_status(c_df, n1), use_container_width=True)
        with cb: st.image(kreiraj_timeline_grafikon(c_hi, None, n2), use_container_width=True)
        st.markdown("---")

        # --- ZBIRNO PO ADRESAMA ---
        st.subheader("📊 Zbirni po Adresama")
        tc = st.columns(len(df["Adresa"].unique()))
        for i, adr in enumerate(df["Adresa"].unique()):
            with tc[i % len(tc)]:
                st.markdown(f"**📍 {adr.upper()}**")
                sd = df[df["Adresa"] == adr]
                sm = []
                for p in ["Wolt", "Glovo"]:
                    pd_f = sd[sd["Platforma"] == p]
                    if not pd_f.empty: sm.append({"Platforma": p, "Nadjeno": len(pd_f), "Otvoreno": len(pd_f[pd_f["Status"]=="Otvoreno"]), "Zatvoreno": len(pd_f[pd_f["Status"]=="Zatvoreno"])})
                if sm: st.dataframe(pd.DataFrame(sm), hide_index=True, use_container_width=True)
        st.markdown("---")

        # --- UPOREDNI PRIKAZ ZAJEDNIČKIH RESTORANA ---
        st.subheader("⚖️ Uporedni Prikaz (Restorani na obe platforme)")
        df['Naziv_Norm'] = df['Naziv'].apply(normalizuj_ime)
        uporedni_podaci = []
        for adr in df['Adresa'].unique():
            df_adr = df[df['Adresa'] == adr]
            wolt_df = df_adr[df_adr['Platforma'] == 'Wolt']
            glovo_df = df_adr[df_adr['Platforma'] == 'Glovo']
            
            zajednicki = set(wolt_df['Naziv_Norm']).intersection(set(glovo_df['Naziv_Norm']))
            for norm_ime in zajednicki:
                w_row = wolt_df[wolt_df['Naziv_Norm'] == norm_ime].iloc[0]
                g_row = glovo_df[glovo_df['Naziv_Norm'] == norm_ime].iloc[0]
                
                uporedni_podaci.append({
                    "Adresa": adr,
                    "Naziv (Wolt)": w_row['Naziv'],
                    "Status Wolt": w_row['Status'],
                    "Ocena Wolt": w_row['Ocena'],
                    "Link Wolt": w_row['Link'],
                    "Naziv (Glovo)": g_row['Naziv'],
                    "Status Glovo": g_row['Status'],
                    "Ocena Glovo": g_row['Ocena'],
                    "Link Glovo": g_row['Link']
                })
        
        if uporedni_podaci:
            df_uporedni = pd.DataFrame(uporedni_podaci)
            
            def color_wolt_glovo(val):
                color = '#27ae60' if val == 'Otvoreno' else '#e74c3c' if val == 'Zatvoreno' else ''
                return f'color: {color}; font-weight: bold;'
            
            st.dataframe(
                df_uporedni.style.map(color_wolt_glovo, subset=['Status Wolt', 'Status Glovo']),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Link Wolt": st.column_config.LinkColumn("Link Wolt", display_text="Otvori Wolt"),
                    "Link Glovo": st.column_config.LinkColumn("Link Glovo", display_text="Otvori Glovo")
                }
            )
        else:
            st.info("Nema restorana koji se nalaze na obe platforme za odabrane adrese.")

        st.markdown("---")

        # --- LISTA SVIH RESTORANA (FILTERI) ---
        st.subheader("🔍 Detaljna Lista Restorana")
        f1, f2, f3 = st.columns(3)
        with f1: fa = st.multiselect("📍 Adresa", df["Adresa"].unique(), df["Adresa"].unique())
        with f2: fp = st.multiselect("📱 Platforma", df["Platforma"].unique(), df["Platforma"].unique())
        with f3: fs = st.multiselect("🚦 Status", ["Otvoreno", "Zatvoreno"], ["Otvoreno", "Zatvoreno"])
        
        f_df = df[(df["Adresa"].isin(fa)) & (df["Platforma"].isin(fp)) & (df["Status"].isin(fs))]
        
        disp_df = f_df.drop(columns=['Naziv_Norm']) if 'Naziv_Norm' in f_df.columns else f_df

        st.dataframe(
            disp_df.style.map(lambda v: f'color: {"#27ae60" if v=="Otvoreno" else "#e74c3c"}; font-weight: bold;', subset=['Status']), 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="Otvori na sajtu")
            }
        )

        # --- PREUZIMANJE PDF-a ---
        if st.session_state.pdf_fajlovi:
            st.markdown("---"); st.subheader("📥 PDF Izveštaji")
            pc = st.columns(4)
            for i, p in enumerate(st.session_state.pdf_fajlovi):
                with pc[i % 4]:
                    with open(p, "rb") as f: 
                        st.download_button(f"Preuzmi {os.path.basename(p)}", f.read(), os.path.basename(p), "application/pdf", key=f"p_{i}")

    rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
    if rem > 0:
        st.info(f"⏳ Sledeće skeniranje za **{rem}** s..."); time.sleep(1); st.rerun()
else: 
    st.info("Sistem zaustavljen. Unesite parametre u meniju sa leve strane i kliknite 'Pokreni'.")
