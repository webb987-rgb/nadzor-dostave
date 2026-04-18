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

# ---------------- PODRŠKA ZA ĆIRILICU ----------------
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

# Dodat custom_naslov parametar da bismo kontrolisali tekst odozgo
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
        # Dinamičko iscrtavanje (samo ako ta platforma postoji u podacima)
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
    
    # Dinamičko prilagođavanje barova zavisno od filtera
    prikazi_wolt = "Wolt" in df_sub["Platforma"].values or df_sub.empty
    prikazi_glovo = "Glovo" in df_sub["Platforma"].values or df_sub.empty
    
    if prikazi_wolt and prikazi_glovo:
        ax.bar([0, 1], [wolt_o, wolt_z], 0.35, label='Wolt', color='#00c2e8')
        ax.bar([0.35, 1.35], [glovo_o, glovo_z], 0.35, label='Glovo', color='#ffc244')
        ax.set_xticks([0.175, 1.175])
    elif prikazi_wolt:
        ax.bar([0, 1], [wolt_o, wolt_z], 0.35, label='Wolt', color='#00c2e8')
        ax.set_xticks([0, 1])
    elif prikazi_glovo:
        ax.bar([0, 1], [glovo_o, glovo_z], 0.35, label='Glovo', color='#ffc244')
        ax.set_xticks([0, 1])

    ax.set_xticklabels(['Otvoreno', 'Zatvoreno'])
    ax.legend(frameon=False)
    ax.set_title(naslov, fontsize=12, fontweight='bold', color='#2c3e50')
    imgdata = BytesIO()
    fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150)
    imgdata.seek(0)
    plt.close(fig)
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
        if not line: continue
        line_lower = line.lower()
        if '%' in line: continue
        if "min" in line_lower and re.search(r'\d+', line_lower): continue
        if "rsd" in line_lower or "din" in line_lower: continue
        if any(x in line_lower for x in ["promo", "novo", "odlično", "besplatna dostava", "artikli", "narudžb", "narudzb", "popust"]): continue
        if len(line) >= 2: return line
    return ""

def analiziraj_status(text):
    t = text.lower()
    indikatori_zatvorenog = ["zatvoreno", "zakažite", "zakaži", "zakazi", "otvara se", "otvara", "closed", "schedule", "nedostupno"]
    if any(k in t for k in indikatori_zatvorenog): return "Zatvoreno"
    return "Otvoreno"

def normalizuj_ime(ime): return re.sub(r'[^\w]', '', ime.lower())

# ---------------- PAMETNO SKROLOVANJE (OPTIMIZACIJA) ----------------
async def pametno_skrolovanje_i_ekstrakcija(page, platforma, address, log_ph=None):
    results_dict = {}
    prethodna_visina = 0
    pokusaji_bez_promene = 0
    max_pokusaja = 5 
    prethodni_broj_restorana = 0

    log_msg(f"[{platforma.upper()} - {address}] Počinje pametno skeniranje i skrolovanje...", log_ph)

    while True:
        if platforma == "Wolt":
            podaci_ekrana = await page.evaluate('''() => {
                let rez = [];
                document.querySelectorAll("a[data-test-id^='venueCard.']").forEach(c => {
                    let link = c.href; let text = c.innerText; let p = c.closest("li");
                    let html_kod = p ? p.innerHTML : c.innerHTML; rez.push({link, text, html_kod});
                });
                return rez;
            }''')
        else:
            podaci_ekrana = await page.evaluate('''() => {
                let rez = [];
                document.querySelectorAll("a:has(h3), a[data-testid='store-card'], .store-card a").forEach(c => {
                    let link = c.href;
                    if (!link.includes('/dostava') && !link.includes('/category')) {
                        rez.push({link: link, text: c.innerText, html_kod: ""});
                    }
                });
                return rez;
            }''')

        for item in podaci_ekrana:
            link = item['link']
            if not link or link in results_dict: continue
            text = item['text']
            sve_zajedno = text + " " + item['html_kod'] if platforma == "Wolt" else text
            ime = ukloni_kvacice(izvuci_ime(text))
            if len(ime) < 2: continue
            results_dict[link] = {
                "Adresa": address, "Platforma": platforma, "Naziv": ime, "Status": analiziraj_status(sve_zajedno)
            }

        trenutni_broj = len(results_dict)
        if trenutni_broj > prethodni_broj_restorana:
            log_msg(f"[{platforma.upper()} - {address}] Učitano {trenutni_broj} restorana...", log_ph)
            prethodni_broj_restorana = trenutni_broj

        await page.evaluate("window.scrollBy(0, window.innerHeight);")
        await asyncio.sleep(0.8)
        trenutna_visina = await page.evaluate("document.body.scrollHeight")
        trenutni_skrol = await page.evaluate("window.scrollY + window.innerHeight")

        if trenutni_skrol >= trenutna_visina - 100: 
            pokusaji_bez_promene += 1
            await asyncio.sleep(1.5) 
            if pokusaji_bez_promene >= max_pokusaja: break 
        else:
            pokusaji_bez_promene = 0 
            prethodna_visina = trenutna_visina

    log_msg(f"[{platforma.upper()} - {address}] Završeno! Ukupno nađeno: {len(results_dict)} restorana.", log_ph)
    return list(results_dict.values())

# ---------------- WOLT SCRAPER ----------------
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

# ---------------- GLOVO SCRAPER ----------------
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
                await asyncio.sleep(4) 
        except: pass
        try:
            kat_link = page.get_by_role("link", name=re.compile(r"Restorani|Hrana", re.I)).first
            await kat_link.wait_for(state="visible", timeout=7000)
            await kat_link.click()
        except: pass
        await asyncio.sleep(4)

        rezultati = await pametno_skrolovanje_i_ekstrakcija(page, "Glovo", address, log_ph)
        await context.close()
        return rezultati
    except Exception as e: 
        log_msg(f"[GLOVO GREŠKA] {e}", log_ph)
        return []

# ---------------- PDF GENERATORI ----------------
def kreiraj_tabelu_stil(podaci, boje_zaglavlja):
    t = Table(podaci, colWidths=[230] * (len(podaci[0])))
    t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor(boje_zaglavlja)),('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),('ALIGN', (0,0), (-1,-1), 'LEFT'),('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),('BOTTOMPADDING', (0,0), (-1,-1), 8),('TOPPADDING', (0,0), (-1,-1), 8),('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7")),('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
    return t

def napravi_pdf_za_adresu(df_adr, adr, df_history):
    pdf_path = str(OUTPUT_DIR / f"Izvestaj_{ukloni_kvacice(adr).replace(' ', '_')}_{timestamp()}.pdf")
    doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    naslov_stil = ParagraphStyle('Naslov', parent=styles['Title'], textColor=colors.HexColor("#2c3e50"), fontSize=20, spaceAfter=10)
    podnaslov_stil = ParagraphStyle('Podnaslov', parent=styles['Heading2'], textColor=colors.HexColor("#2980b9"), fontSize=16, spaceBefore=20, spaceAfter=15)
    cell_stil = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=10, leading=14)
    def format_lampica_status(status):
        boja = "#27ae60" if status == "Otvoreno" else "#e74c3c"
        return Paragraph(f"<font color='{boja}' size=16>&bull;</font> {status}", cell_stil)
    def format_lampica(naziv, status):
        boja = "#27ae60" if status == "Otvoreno" else "#e74c3c"
        return Paragraph(f"<font color='{boja}' size=16>&bull;</font> {naziv}", cell_stil)

    elements = [Paragraph(f"Izveštaj o Dostavi - {ukloni_kvacice(adr).upper()}", naslov_stil)]
    tabela_podaci = [["Platforma", "Ukupno Nadjeno", "Otvoreno", "Zatvoreno"]]
    for plat in ["Wolt", "Glovo"]:
        sub = df_adr[df_adr["Platforma"] == plat]
        if not sub.empty: tabela_podaci.append([plat, len(sub), len(sub[sub["Status"] == "Otvoreno"]), len(sub[sub["Status"] == "Zatvoreno"])])
    
    t_zbirno = Table(tabela_podaci, colWidths=[120, 100, 100, 100])
    t_zbirno.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#34495e")),('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),('ALIGN', (0,0), (-1,-1), 'CENTER'),('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),('BOTTOMPADDING', (0,0), (-1,0), 8),('TOPPADDING', (0,0), (-1,0), 8),('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#f8f9fa")),('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7")),('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
    elements.append(t_zbirno)
    elements.append(Spacer(1, 20))
    elements.append(Table([[Image(kreiraj_grafikon_status(df_adr, f"Status - {adr}"), width=280, height=224)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]))
    elements.append(Spacer(1, 10))
    elements.append(Table([[Image(kreiraj_timeline_grafikon(df_history, adr), width=500, height=200)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]))
    elements.append(PageBreak())

    wolt_rečnik = {normalizuj_ime(row["Naziv"]): row for _, row in df_adr[df_adr["Platforma"] == "Wolt"].iterrows()}
    glovo_rečnik = {normalizuj_ime(row["Naziv"]): row for _, row in df_adr[df_adr["Platforma"] == "Glovo"].iterrows()}
    sva_norm_imena = set(wolt_rečnik.keys()).union(set(glovo_rečnik.keys()))
    zajednicki = sorted([ime for ime in sva_norm_imena if ime in wolt_rečnik and ime in glovo_rečnik])
    samo_wolt = sorted([ime for ime in sva_norm_imena if ime in wolt_rečnik and ime not in glovo_rečnik])
    samo_glovo = sorted([ime for ime in sva_norm_imena if ime in glovo_rečnik and ime not in wolt_rečnik])

    if zajednicki:
        elements.append(Paragraph("Zajednički Restorani (Na obe platforme)", podnaslov_stil))
        podaci_z = [["Naziv Restorana", "Status Wolt", "Status Glovo"]]
        for n in zajednicki: podaci_z.append([Paragraph(wolt_rečnik[n]["Naziv"], cell_stil), format_lampica_status(wolt_rečnik[n]["Status"]), format_lampica_status(glovo_rečnik[n]["Status"])])
        t_z = Table(podaci_z, colWidths=[200, 130, 130])
        t_z.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2c3e50")), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7")),('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),('BOTTOMPADDING', (0,0), (-1,-1), 8),('TOPPADDING', (0,0), (-1,-1), 8),('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
        elements.append(t_z)
        elements.append(PageBreak())

    if samo_wolt:
        elements.append(Paragraph("Ekskluzivno na Woltu", podnaslov_stil))
        elements.append(kreiraj_tabelu_stil([["Naziv Restorana"]] + [[format_lampica(wolt_rečnik[n]["Naziv"], wolt_rečnik[n]["Status"])] for n in samo_wolt], "#3498db"))
        elements.append(PageBreak())
    if samo_glovo:
        elements.append(Paragraph("Ekskluzivno na Glovu", podnaslov_stil))
        elements.append(kreiraj_tabelu_stil([["Naziv Restorana"]] + [[format_lampica(glovo_rečnik[n]["Naziv"], glovo_rečnik[n]["Status"])] for n in samo_glovo], "#f39c12"))

    doc.build(elements)
    return pdf_path

def napravi_zbirni_pdf(df, df_history):
    pdf_path = str(OUTPUT_DIR / f"Zbirni_Izvestaj_{timestamp()}.pdf")
    doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    naslov_stil = ParagraphStyle('Naslov', parent=styles['Title'], textColor=colors.HexColor("#2c3e50"), fontSize=20, spaceAfter=20)
    podnaslov_stil = ParagraphStyle('Podnaslov', parent=styles['Heading2'], textColor=colors.HexColor("#2980b9"), fontSize=16, spaceBefore=20, spaceAfter=15)

    elements = [Paragraph("Zbirni Izveštaj - Sve Adrese", naslov_stil)]
    elements.append(Table([[Image(kreiraj_grafikon_status(df, "Ukupni Status"), width=280, height=224)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]))
    elements.append(Spacer(1, 10))
    elements.append(Table([[Image(kreiraj_timeline_grafikon(df_history, None), width=500, height=200)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]))
    elements.append(PageBreak())

    for adr in df["Adresa"].unique():
        df_adr = df[df["Adresa"] == adr]
        elements.append(Paragraph(f"Statistika za lokaciju: {ukloni_kvacice(adr).upper()}", podnaslov_stil))
        tabela_podaci = [["Platforma", "Ukupno Nadjeno", "Otvoreno", "Zatvoreno"]]
        for plat in ["Wolt", "Glovo"]:
            sub = df_adr[df_adr["Platforma"] == plat]
            if not sub.empty: tabela_podaci.append([plat, len(sub), len(sub[sub["Status"] == "Otvoreno"]), len(sub[sub["Status"] == "Zatvoreno"])])
        
        t_adr = Table(tabela_podaci, colWidths=[120, 100, 100, 100])
        t_adr.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#34495e")),('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),('ALIGN', (0,0), (-1,-1), 'CENTER'),('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),('BOTTOMPADDING', (0,0), (-1,0), 8),('TOPPADDING', (0,0), (-1,0), 8),('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#f8f9fa")),('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7")),('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
        elements.append(t_adr)
        elements.append(Spacer(1, 15))
        elements.append(Table([[Image(kreiraj_grafikon_status(df_adr, f"Trenutni Status - {adr}"), width=280, height=224)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]))
        elements.append(Spacer(1, 20))

    doc.build(elements)
    return pdf_path

# ---------------- GLAVNA PETLJA ----------------
async def run_platform_scraper(platform_name, p, address, headless, log_ph):
    browser = await p.chromium.launch(headless=headless) 
    try:
        if platform_name == "Wolt": return await scrape_wolt(browser, address, log_ph)
        elif platform_name == "Glovo": return await scrape_glovo(browser, address, log_ph)
        return []
    finally: await browser.close()

async def proces_skeniranja(adrese, headless_mode, log_ph):
    sve_prikupljeno = []
    async with async_playwright() as p:
        for adr in adrese:
            log_msg(f"\n[SISTEM] Pokrećem skeniranje za: {adr} u {format_time_short()}", log_ph)
            rezultati = await asyncio.gather(
                run_platform_scraper("Wolt", p, adr, headless_mode, log_ph),
                run_platform_scraper("Glovo", p, adr, headless_mode, log_ph)
            )
            sve_prikupljeno.extend(rezultati[0] + rezultati[1])

    if sve_prikupljeno:
        df_sve = pd.DataFrame(sve_prikupljeno)
        df_history = sacuvaj_u_istoriju(df_sve)
        generisani_pdfovi = []
        
        log_msg("Generišem PDF izveštaje...", log_ph)
        zbirni_pdf = napravi_zbirni_pdf(df_sve, df_history)
        generisani_pdfovi.append(zbirni_pdf)
        for adr in df_sve["Adresa"].unique():
            df_adr = df_sve[df_sve["Adresa"] == adr]
            putanja = napravi_pdf_za_adresu(df_adr, adr, df_history)
            generisani_pdfovi.append(putanja)

        return df_sve, df_history, generisani_pdfovi
    return pd.DataFrame(), pd.DataFrame(), []

# ================= STREAMLIT INTERFEJS =================

if 'pokrenuto' not in st.session_state:
    st.session_state.pokrenuto = False
if 'last_run' not in st.session_state:
    st.session_state.last_run = 0
if 'df_sve' not in st.session_state:
    st.session_state.df_sve = pd.DataFrame()
if 'df_history' not in st.session_state:
    st.session_state.df_history = pd.DataFrame()
if 'pdf_fajlovi' not in st.session_state:
    st.session_state.pdf_fajlovi = []

st.title("🍔 Pametni Nadzor Dostave (Wolt & Glovo)")
st.markdown("Dobrodošli na kontrolnu tablu. Podesite parametre i pratite status u realnom vremenu.")

with st.sidebar:
    st.header("⚙️ Podešavanja")
    adrese_input = st.text_area("📍 Unesi adrese (svaku u novi red):", value="Presernova 8, Nis\nBulevar Nemanjica 20, Nis")
    sleep_interval = st.number_input("⏱️ Period spavanja (minuti):", min_value=1, value=15)
    headless_mode = st.checkbox("🕶️ Sakrij browser (Headless mode)", value=True)
    slanje_maila = st.checkbox("✉️ Pošalji izveštaj na Email", value=False)
    
    email_adrese = ""
    if slanje_maila:
        email_adrese = st.text_input("Unesi emailove (odvojene zarezom):", value="z.webb@live.com")
        
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Pokreni", type="primary"):
            st.session_state.pokrenuto = True
            st.session_state.last_run = 0 
            st.rerun()
    with col2:
        if st.button("⏹️ Zaustavi"):
            st.session_state.pokrenuto = False
            st.rerun()

if st.session_state.pokrenuto:
    lista_adresa = [cirilica_u_latinicu(a.strip()) for a in adrese_input.split('\n') if a.strip()]
    
    if not lista_adresa:
        st.warning("⚠️ Molimo unesite bar jednu adresu!")
        st.session_state.pokrenuto = False
        st.rerun()

    now = time.time()
    time_since_last = now - st.session_state.last_run
    
    if time_since_last >= sleep_interval * 60 or st.session_state.last_run == 0:
        status_log = st.empty()
        with st.spinner("Skeniranje je u toku..."):
            df_sve, df_history, pdf_fajlovi = asyncio.run(proces_skeniranja(lista_adresa, headless_mode, status_log))
            st.session_state.df_sve = df_sve
            st.session_state.df_history = df_history
            st.session_state.pdf_fajlovi = pdf_fajlovi
            st.session_state.last_run = time.time()
            
            if slanje_maila and pdf_fajlovi:
                posalji_email(pdf_fajlovi, email_adrese, status_log)
        status_log.empty()
        st.rerun()

    df = st.session_state.df_sve
    if not df.empty:
        st.success(f"✅ Podaci poslednji put osveženi u: {datetime.datetime.fromtimestamp(st.session_state.last_run).strftime('%H:%M:%S')}")
        
        # ---------------- FILTERI ZA GRAFIKONE ----------------
        st.subheader("📊 Interaktivni Grafikoni")
        
        g_col1, g_col2 = st.columns(2)
        with g_col1:
            graf_adresa = st.selectbox("📍 Filtriraj grafikone po adresi:", ["Sve adrese"] + list(df["Adresa"].unique()))
        with g_col2:
            graf_plat = st.selectbox("📱 Filtriraj grafikone po platformi:", ["Sve platforme", "Wolt", "Glovo"])

        chart_df = df.copy()
        chart_hist = st.session_state.df_history.copy()

        naslov_status_grafika = "Ukupni Status"
        naslov_timeline_grafika = "Istorijat aktivnosti"

        if graf_adresa != "Sve adrese":
            chart_df = chart_df[chart_df["Adresa"] == graf_adresa]
            chart_hist = chart_hist[chart_hist["Adresa"] == graf_adresa]
            naslov_status_grafika += f" - {graf_adresa.upper()}"
            naslov_timeline_grafika += f" - {graf_adresa.upper()}"
        else:
            naslov_status_grafika += " (Sve adrese)"
            naslov_timeline_grafika += " (Sve adrese)"

        if graf_plat != "Sve platforme":
            chart_df = chart_df[chart_df["Platforma"] == graf_plat]
            chart_hist = chart_hist[chart_hist["Platforma"] == graf_plat]
            naslov_status_grafika += f" | {graf_plat}"
            naslov_timeline_grafika += f" | {graf_plat}"

        colA, colB = st.columns(2)
        with colA:
            st.image(kreiraj_grafikon_status(chart_df, naslov_status_grafika), use_container_width=True)
        with colB:
            st.image(kreiraj_timeline_grafikon(chart_hist, None, naslov_timeline_grafika), use_container_width=True)
            
        st.markdown("---")
        
        # ---------------- ZBIRNE TABELE PO ADRESAMA ----------------
        st.subheader("📊 Zbirni Izveštaj po Adresama")
        tab_colovi = st.columns(len(df["Adresa"].unique()))
        
        for index, adr in enumerate(df["Adresa"].unique()):
            with tab_colovi[index % len(tab_colovi)]:
                st.markdown(f"**📍 {adr.upper()}**")
                sub_df = df[df["Adresa"] == adr]
                sum_data = []
                for plat in ["Wolt", "Glovo"]:
                    plat_df = sub_df[sub_df["Platforma"] == plat]
                    if not plat_df.empty:
                        sum_data.append({
                            "Platforma": plat, 
                            "Ukupno Nadjeno": len(plat_df), 
                            "Otvoreno": len(plat_df[plat_df["Status"] == "Otvoreno"]), 
                            "Zatvoreno": len(plat_df[plat_df["Status"] == "Zatvoreno"])
                        })
                if sum_data:
                    st.dataframe(pd.DataFrame(sum_data), hide_index=True, use_container_width=True)
                    
        st.markdown("---")

        # ---------------- FILTERI ZA GLAVNU TABELU RESTORANA ----------------
        st.subheader("🔍 Detaljna Lista Restorana")
        
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            f_adrese = st.multiselect("📍 Adresa", options=df["Adresa"].unique(), default=df["Adresa"].unique())
        with f_col2:
            f_plat = st.multiselect("📱 Platforma", options=df["Platforma"].unique(), default=df["Platforma"].unique())
        with f_col3:
            f_status = st.multiselect("🚦 Status", options=["Otvoreno", "Zatvoreno"], default=["Otvoreno", "Zatvoreno"])

        filtered_df = df[
            (df["Adresa"].isin(f_adrese)) & 
            (df["Platforma"].isin(f_plat)) & 
            (df["Status"].isin(f_status))
        ]

        def color_status(val):
            color = '#27ae60' if val == 'Otvoreno' else '#e74c3c' if val == 'Zatvoreno' else ''
            return f'color: {color}; font-weight: bold;'
            
        styled_df = filtered_df.style.map(color_status, subset=['Status'])
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
        
        st.markdown("---")

        # ---------------- PREUZIMANJE PDF-ova ----------------
        if st.session_state.pdf_fajlovi:
            st.markdown("### 📥 Preuzmi PDF izveštaje:")
            pdf_cols = st.columns(min(len(st.session_state.pdf_fajlovi), 4))
            for i, pdf in enumerate(st.session_state.pdf_fajlovi):
                with pdf_cols[i % 4]:
                    with open(pdf, "rb") as f:
                        pdf_bytes = f.read()
                    st.download_button(
                        label=f"Preuzmi {os.path.basename(pdf)}", 
                        data=pdf_bytes, 
                        file_name=os.path.basename(pdf), 
                        mime="application/pdf",
                        key=f"btn_pdf_{i}"
                    )

    preostalo_vreme = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
    if preostalo_vreme > 0:
        st.info(f"⏳ Sistem je u fazi spavanja. Sledeće skeniranje počinje za **{preostalo_vreme}** sekundi...")
        time.sleep(1)
        st.rerun() 

else:
    st.info("Sistem je zaustavljen. Podesite parametre levo i kliknite 'Pokreni'.")