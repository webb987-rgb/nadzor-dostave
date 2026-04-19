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
    
    st.session_state.df_history = df_kombinovano
    return df_kombinovano

def kreiraj_timeline_grafikon(df_hist, adresa=None, custom_naslov=None, is_pdf=False):
    df_sub = df_hist.copy()
    if adresa:
        df_sub = df_sub[df_sub["Adresa"] == adresa]
        naslov = f'Istorijat aktivnosti - {adresa.upper()}'
    else:
        if not df_sub.empty:
            df_sub = df_sub.groupby(["Datum", "Vreme", "Platforma"]).sum(numeric_only=True).reset_index()
        naslov = 'Zbirni Istorijat aktivnosti (Sve Adrese)'
        
    if custom_naslov:
        naslov = custom_naslov
        
    fig, ax = plt.subplots(figsize=(10, 4), facecolor='#ffffff')
    ax.set_facecolor('#f8f9fa')
    
    if len(df_sub) == 0:
        ax.text(0.5, 0.5, "Nema istorijskih podataka za ovaj period", ha='center', va='center')
        ax.axis('off')
    else:
        jedan_dan = df_sub["Datum"].nunique() <= 1
        if jedan_dan:
            df_sub["X_Label"] = df_sub["Vreme"]
        else:
            df_sub["X_Label"] = df_sub["Datum"].str[-5:].str.replace('-', '.') + " \n" + df_sub["Vreme"]

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
    for i, v in zip(pos_list, bar_list):
        if v > 0: ax.text(i, v + 0.5, f"{v:.1f} min", ha='center', va='bottom', fontweight='bold', color='#2c3e50', fontsize=9)
    max_v = max(bar_list) if max(bar_list) > 0 else 10
    ax.set_ylim(0, max_v * 1.2)
    plt.tight_layout()
    imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)
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
    ind = [
        "samo preuzimanje", "samo za preuzimanje", "pickup only", 
        "dostava nije dostupna", "dostava trenutno nije", "samo licno preuzimanje",
        "zatvoreno", "zakažite", "zakaži", "zakazi", "nedostupno", "otvara se", "otvara", "closed", "schedule"
    ]
    if any(k in t for k in ind): return "Zatvoreno"
    return "Otvoreno"

def izvuci_ocenu(tekst, plat):
    try:
        if not tekst: return "-"
        cist_tekst = re.sub(r'<[^>]+>', ' ', tekst).lower()
        ocena = None
        if plat == "Glovo":
            procenti = re.findall(r'(\d{1,3})\s*%', cist_tekst)
            for p in procenti:
                if int(p) >= 60:
                    ocena = p + "%"
                    break
        elif plat == "Wolt":
            match = re.search(r'\b([5-9][.,][0-9]|10[.,]0)\b', cist_tekst)
            if match: 
                ocena = match.group(1).replace(',', '.')
        if ocena: return ocena
        return "-"
    except: return "-"

def izvuci_vreme_dostave(tekst):
    try:
        if not tekst: return "-", np.nan
        cist_tekst = re.sub(r'<[^>]+>', ' ', tekst).lower()
        match = re.search(r'(\d{1,3})\s*[-–]\s*(\d{1,3})\s*(?:min|m|\')', cist_tekst)
        if match:
            v1, v2 = int(match.group(1)), int(match.group(2))
            if v1 < 120 and v2 < 120:
                return f"{v1}-{v2} min", (v1 + v2) / 2.0
        match_single = re.search(r'\b(\d{1,3})\s*(?:min|m|\')', cist_tekst)
        if match_single:
            v = int(match_single.group(1))
            if v < 120:
                return f"{v} min", float(v)
        return "-", np.nan
    except: return "-", np.nan

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
            vreme_str, vreme_num = izvuci_vreme_dostave(sve_z)
            
            is_new = False
            t_low = text.strip().lower()
            if plat == "Wolt":
                is_new = bool(re.search(r'>\s*(novo|new)\s*<', item['html'].lower())) or (ocena == "Novo")
            else:
                is_new = t_low.endswith('new') or t_low.endswith('novo') or bool(re.search(r'•.*?new\b', t_low)) or (ocena == "Novo")

            results_dict[link] = {
                "Adresa": address, "Platforma": plat, "Naziv": ime, "Ocena": ocena,
                "Vreme dostave": vreme_str, "Status": analiziraj_status(sve_z),
                "Vreme_Broj": vreme_num, "Is_New": is_new, "Link": link
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

# ---------------- SCRAPERS (STEALTH + HUMAN NAVIGATION) ----------------
async def scrape_wolt(context_wolt, address, log_ph=None, error_screenshots=None):
    page = None
    try:
        page = await context_wolt.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.set_default_timeout(10000)
        
        await page.goto("https://wolt.com/sr/srb")
        try: await page.locator("[data-test-id='allow-button']").click(timeout=3000)
        except: pass
        
        try:
            input_f = page.get_by_role("combobox")
            await input_f.wait_for(state="visible", timeout=5000)
            await input_f.click(timeout=3000)
            await input_f.fill(address)
            await asyncio.sleep(2)
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await asyncio.sleep(5)
            await page.goto("https://wolt.com/sr/discovery/restaurants")
            try: await page.wait_for_selector("a[data-test-id^='venueCard.']", timeout=8000)
            except PlaywrightTimeoutError: pass
            
            rez = await pametno_skrolovanje_i_ekstrakcija(page, "Wolt", address, log_ph)
            return rez
        except PlaywrightTimeoutError:
            log_msg(f"[WOLT TIMEOUT] Odustajem od adrese {address} zbog sporog učitavanja.", log_ph)
            return []
    except Exception as e: 
        log_msg(f"[WOLT GREŠKA] {e}", log_ph)
        return []
    finally:
        if page: await page.close()

async def scrape_glovo(context_glovo, address, log_ph=None, error_screenshots=None):
    page = None
    try:
        # NE BRIŠEMO KOLAČIĆE! Čuvamo propusnicu od Cloudflare-a!
        page = await context_glovo.new_page()
        
        # STEALTH: Skrivamo da smo robot od Cloudflare-a i Glova
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.set_default_timeout(10000)
        
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")
        
        # FAST-FAIL: Detekcija "Oh, no!" ekrana
        stranica_tekst = await page.content()
        if "Oh, no!" in stranica_tekst or "It looks like there's a problem" in stranica_tekst:
            log_msg(f"[GLOVO BLOKADA] Glovo je detektovao bota na {address}. Slikam i odustajem.", log_ph)
            if error_screenshots is not None:
                try:
                    err_path = str(ERRORS_DIR / f"Glovo_SoftBan_{ukloni_kvacice(address).replace(' ', '_')}_{timestamp()}.png")
                    await page.screenshot(path=err_path)
                    error_screenshots.append(err_path)
                except: pass
            return []
        
        try: await page.get_by_role("button", name=re.compile("Accept|Prihvati", re.I)).click(timeout=3000)
        except: pass
        
        # LOGIKA PRAVOG ČOVEKA: Da li vidimo glavno polje (prva adresa) ili zaglavlje (druga adresa)?
        try:
            # Pokušaj 1: Potpuno nova, čista sesija (vidimo veliko polje na sredini)
            hero_input = page.locator("#hero-container-input")
            await hero_input.wait_for(state="visible", timeout=4000)
            await hero_input.click()
            search = page.get_by_role("searchbox")
            await search.fill(address)
            
            dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
            await dropdown_item.wait_for(state="visible", timeout=8000)
            await dropdown_item.click()
            
        except PlaywrightTimeoutError:
            # Pokušaj 2: Glovo nas se seća! Klikćemo na trenutnu adresu u zaglavlju kao pravi čovek.
            log_msg(f"[GLOVO] Menjam adresu kao pravi korisnik za: {address}", log_ph)
            try:
                # Nalazimo dugme u headeru gde piše trenutna adresa
                header_btn = page.locator('header div[role="button"]').first
                await header_btn.wait_for(state="visible", timeout=5000)
                await header_btn.click()
                
                await asyncio.sleep(1)
                search_modal = page.get_by_role("searchbox").last
                await search_modal.wait_for(state="visible", timeout=5000)
                await search_modal.click()
                await search_modal.fill(address)
                
                await asyncio.sleep(2)
                # Klik na prvi predlog u padajućem meniju novog prozora
                dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
                await dropdown_item.wait_for(state="visible", timeout=8000)
                await dropdown_item.click()
            except PlaywrightTimeoutError:
                log_msg(f"[GLOVO ODUSTAJEM] Ne mogu da promenim adresu za {address}. Slikam...", log_ph)
                if error_screenshots is not None:
                    try:
                        err_path = str(ERRORS_DIR / f"Glovo_Nav_Error_{ukloni_kvacice(address).replace(' ', '_')}_{timestamp()}.png")
                        await page.screenshot(path=err_path)
                        error_screenshots.append(err_path)
                    except: pass
                return []

        try:
            btn_drugo = page.locator("button:has-text('Drugo')")
            await btn_drugo.wait_for(state="visible", timeout=3000)
            await btn_drugo.click()
        except PlaywrightTimeoutError: pass
        
        try:
            btn_potvrdi = page.locator("button:has-text('Potvrdi adresu')")
            await btn_potvrdi.wait_for(state="visible", timeout=3000)
            await btn_potvrdi.click()
        except PlaywrightTimeoutError: pass
        
        await asyncio.sleep(5)
        try:
            btn_pocetna = page.locator("text='Idi na početnu stranicu'")
            if await btn_pocetna.count() > 0 and await btn_pocetna.first.is_visible(timeout=3000):
                await btn_pocetna.first.click()
                await asyncio.sleep(5)
        except: pass
        
        try:
            kat_link = page.get_by_role("link", name=re.compile(r"Restorani|Hrana", re.I)).first
            await kat_link.wait_for(state="visible", timeout=5000)
            await kat_link.click()
        except PlaywrightTimeoutError: pass
        
        await asyncio.sleep(5)
        page.set_default_timeout(60000) 
        rez = await pametno_skrolovanje_i_ekstrakcija(page, "Glovo", address, log_ph)
        return rez
    except Exception as e: 
        log_msg(f"[GLOVO GREŠKA] {e}", log_ph)
        return []
    finally:
        if page: await page.close()

# ---------------- PDF LOGIC ----------------
def format_pdf_stavka(tekst, status, stil):
    boja = "#27ae60" if status == "Otvoreno" else "#e74c3c"
    return Paragraph(f"<font color='{boja}' size=16>&bull;</font> {tekst}", stil)

def napravi_pdf_za_adresu(df_adr, adr, df_hist):
    p_path = str(OUTPUT_DIR / f"Izvestaj_{ukloni_kvacice(adr).replace(' ', '_')}_{timestamp()}.pdf")
    doc = SimpleDocTemplate(p_path, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    ns = ParagraphStyle('Naslov', parent=styles['Title'], textColor=colors.HexColor("#2c3e50"), fontSize=20, spaceAfter=10)
    ps = ParagraphStyle('Podnaslov', parent=styles['Heading2'], textColor=colors.HexColor("#2980b9"), fontSize=16, spaceBefore=20, spaceAfter=15)
    cs = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=10, leading=14)

    elements = [Paragraph(f"Izvestaj o Dostavi - {ukloni_kvacice(adr).upper()}", ns)]
    tab = [["Platforma", "Ukupno Nadjeno", "Otvoreno", "Zatvoreno"]]
    for plat in ["Wolt", "Glovo"]:
        sub = df_adr[df_adr["Platforma"] == plat]
        if not sub.empty: tab.append([plat, len(sub), len(sub[sub["Status"] == "Otvoreno"]), len(sub[sub["Status"] == "Zatvoreno"])])
    
    t_z = Table(tab, colWidths=[120, 100, 100, 100])
    t_z.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#34495e")),('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),('ALIGN', (0,0), (-1,-1), 'CENTER'),('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
    
    elements.extend([t_z, Spacer(1, 20), Table([[Image(kreiraj_grafikon_status(df_adr, f"Status - {adr}"), width=280, height=224)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), Spacer(1, 10), Table([[Image(kreiraj_timeline_grafikon(df_hist, adr, is_pdf=True), width=500, height=200)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), PageBreak()])

    wn = {}
    for _, r in df_adr[df_adr["Platforma"] == "Wolt"].iterrows():
        wn[normalizuj_ime(r["Naziv"])] = r
        
    gn = {}
    for _, r in df_adr[df_adr["Platforma"] == "Glovo"].iterrows():
        gn[normalizuj_ime(r["Naziv"])] = r
        
    sva = set(wn.keys()).union(set(gn.keys()))
    zaj = sorted([i for i in sva if i in wn and i in gn])
    sw = sorted([i for i in sva if i in wn and i not in gn])
    sg = sorted([i for i in sva if i in gn and i not in wn])

    if zaj:
        elements.append(Paragraph("Zajednicki Restorani", ps))
        pz = [["Naziv", "Status Wolt", "Status Glovo"]]
        for n in zaj: 
            pz.append([Paragraph(wn[n]["Naziv"], cs), format_pdf_stavka(wn[n]["Status"], wn[n]["Status"], cs), format_pdf_stavka(gn[n]["Status"], gn[n]["Status"], cs)])
        t_c = Table(pz, colWidths=[200, 130, 130])
        t_c.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2c3e50")), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
        elements.extend([t_c, PageBreak()])
        
    if sw:
        elements.append(Paragraph("Ekskluzivno na Woltu", ps))
        pod = [["Naziv Restorana"]]
        for n in sw: 
            pod.append([format_pdf_stavka(wn[n]["Naziv"], wn[n]["Status"], cs)])
        t_w = Table(pod, colWidths=[460])
        t_w.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#3498db")), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
        elements.extend([t_w, PageBreak()])
        
    if sg:
        elements.append(Paragraph("Ekskluzivno na Glovu", ps))
        pod = [["Naziv Restorana"]]
        for n in sg: 
            pod.append([format_pdf_stavka(gn[n]["Naziv"], gn[n]["Status"], cs)])
        t_g = Table(pod, colWidths=[460])
        t_g.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f39c12")), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#bdc3c7"))]))
        elements.append(t_g)
        
    doc.build(elements)
    return p_path

def napravi_zbirni_pdf(df, df_hist):
    p_path = str(OUTPUT_DIR / f"Zbirni_Izvestaj_{timestamp()}.pdf")
    doc = SimpleDocTemplate(p_path, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    ns = ParagraphStyle('Naslov', parent=styles['Title'], textColor=colors.HexColor("#2c3e50"), fontSize=20, spaceAfter=20)
    ps = ParagraphStyle('Podnaslov', parent=styles['Heading2'], textColor=colors.HexColor("#2980b9"), fontSize=16, spaceBefore=20, spaceAfter=15)
    
    elements = [Paragraph("Zbirni Izvestaj - Sve Adrese", ns), Table([[Image(kreiraj_grafikon_status(df, "Ukupni Status"), width=280, height=224)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), Spacer(1, 10), Table([[Image(kreiraj_timeline_grafikon(df_hist, None, is_pdf=True), width=500, height=200)]], colWidths=[515], style=[('ALIGN', (0,0), (-1,-1), 'CENTER')]), PageBreak()]
    
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

# ---------------- PROCES SKENIRANJA ----------------
async def proces_skeniranja(adrese, log_ph):
    sve = []
    error_screenshots = [] 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        ) 
        
        context_wolt = await browser.new_context(
            permissions=['geolocation'],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context_glovo = await browser.new_context(
            permissions=['geolocation'],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9,sr;q=0.8"}
        )
        
        for i, adr in enumerate(adrese):
            if i > 0:
                log_msg("⏳ Pauza 5 sekundi izmedju adresa...", log_ph)
                await asyncio.sleep(5)
                
            log_msg(f"\n[SISTEM] Pokrecem skeniranje za: {adr}", log_ph)
            r = await asyncio.gather(
                scrape_wolt(context_wolt, adr, log_ph, error_screenshots), 
                scrape_glovo(context_glovo, adr, log_ph, error_screenshots)
            )
            sve.extend(r[0] + r[1])
                
        await context_wolt.close()
        await context_glovo.close()
        await browser.close()
            
    if sve:
        df_s = pd.DataFrame(sve)
        df_h = sacuvaj_u_istoriju(df_s)
        log_msg("Generisem PDF izvestaje...", log_ph)
        
        pdf_fajlovi = []
        zbirni = napravi_zbirni_pdf(df_s, df_h)
        if zbirni: pdf_fajlovi.append(zbirni)
        
        for adr in df_s["Adresa"].unique():
            df_sub = df_s[df_s["Adresa"] == adr]
            p_fajl = napravi_pdf_za_adresu(df_sub, adr, df_h)
            if p_fajl: pdf_fajlovi.append(p_fajl)
            
        return df_s, df_h, pdf_fajlovi, error_screenshots
    return pd.DataFrame(), pd.DataFrame(), [], error_screenshots

# ================= STREAMLIT UI =================
if 'pokrenuto' not in st.session_state: st.session_state.pokrenuto = False
if 'last_run' not in st.session_state: st.session_state.last_run = 0
if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()
if 'pdf_fajlovi' not in st.session_state: st.session_state.pdf_fajlovi = []
if 'error_screenshots' not in st.session_state: st.session_state.error_screenshots = []

if 'df_history' not in st.session_state: 
    if os.path.exists(HISTORY_FILE):
        st.session_state.df_history = pd.read_csv(HISTORY_FILE)
    else:
        st.session_state.df_history = pd.DataFrame()

st.title("🍔 Nadzor Dostave (Wolt & Glovo)")
with st.sidebar:
    st.header("⚙️ Podešavanja")
    adrese_input = st.text_area("📍 Adrese:", value="", placeholder="Makenzijeva 57, Beograd")
    sleep_interval = st.number_input("⏱️ Spavanje (min):", min_value=1, value=15)
    timer_ph = st.empty()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ Pokreni", type="primary"): st.session_state.pokrenuto = True; st.session_state.last_run = 0; st.rerun()
    with c2:
        if st.button("⏹️ Zaustavi"): st.session_state.pokrenuto = False; st.rerun()
    if st.button("🗑️ Obriši istoriju", use_container_width=True):
        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
        st.session_state.df_history = pd.DataFrame(); st.rerun()

if st.session_state.pokrenuto:
    lista_adresa = [cirilica_u_latinicu(a.strip()) for a in adrese_input.split('\n') if a.strip()]
    if not lista_adresa: st.warning("Unesite bar jednu adresu!"); st.session_state.pokrenuto = False; st.rerun()

    now = time.time()
    if now - st.session_state.last_run >= sleep_interval * 60 or st.session_state.last_run == 0:
        timer_ph.warning("⏳ Skeniranje...")
        sl = st.empty()
        df, hi, pdf, err_imgs = asyncio.run(proces_skeniranja(lista_adresa, sl))
        st.session_state.df_sve, st.session_state.df_history, st.session_state.pdf_fajlovi, st.session_state.error_screenshots, st.session_state.last_run = df, hi, pdf, err_imgs, time.time()
        sl.empty(); st.rerun()

    df = st.session_state.df_sve
    if not df.empty:
        for col in ["Vreme_Broj", "Vreme dostave", "Ocena", "Is_New"]:
            if col not in df.columns: df[col] = False if col == "Is_New" else (np.nan if "Broj" in col else "-")

        st.success(f"✅ Osveženo u: {datetime.datetime.fromtimestamp(st.session_state.last_run).strftime('%H:%M:%S')}")
        
        st.subheader("📊 Zbirni po Adresama")
        tc = st.columns(len(df["Adresa"].unique()))
        for i, adr in enumerate(df["Adresa"].unique()):
            with tc[i % len(tc)]:
                st.markdown(f"**📍 {adr.upper()}**")
                sd = df[df["Adresa"] == adr]; sm = []
                for p in ["Wolt", "Glovo"]:
                    pd_f = sd[sd["Platforma"] == p]
                    if not pd_f.empty: sm.append({"Platforma": p, "Ukupno": len(pd_f), "Otvoreno": len(pd_f[pd_f["Status"]=="Otvoreno"]), "Zatvoreno": len(pd_f[pd_f["Status"]=="Zatvoreno"])})
                if sm: st.dataframe(pd.DataFrame(sm), hide_index=True, use_container_width=True)
        st.markdown("---")

        st.subheader("📊 Interaktivni Grafikoni i Istorijat")
        adrese_un = list(df["Adresa"].unique())
        graf_adr = st.selectbox("📍 Filtriraj Grafikone:", ["Sve adrese"] + adrese_un, index=1 if len(adrese_un) == 1 else 0)
        c_df = df if graf_adr == "Sve adrese" else df[df["Adresa"] == graf_adr]
        
        ca, cb = st.columns(2)
        with ca: st.image(kreiraj_grafikon_status(c_df, "Uporedni Status"), use_container_width=True)
        with cb: st.image(kreiraj_grafikon_vreme_dostave(c_df, "Prosečno vreme dostave"), use_container_width=True)
        
        hist_df = st.session_state.df_history.copy()
        if not hist_df.empty:
            c_h = hist_df if graf_adr == "Sve adrese" else hist_df[hist_df["Adresa"] == graf_adr]
            st.image(kreiraj_timeline_grafikon(c_h, None, "Istorijat aktivnosti"), use_container_width=True)
        st.markdown("---")

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
                    "Adresa": adr, "Naziv (Wolt)": w_row['Naziv'], "Status Wolt": w_row['Status'], "Vreme Wolt": w_row['Vreme dostave'], "Ocena Wolt": w_row['Ocena'], "Link Wolt": w_row['Link'],
                    "Naziv (Glovo)": g_row['Naziv'], "Status Glovo": g_row['Status'], "Vreme Glovo": g_row['Vreme dostave'], "Ocena Glovo": g_row['Ocena'], "Link Glovo": g_row['Link']
                })
        if uporedni_podaci:
            df_uporedni = pd.DataFrame(uporedni_podaci)
            st.dataframe(df_uporedni.style.map(lambda val: f'color: {"#27ae60" if val=="Otvoreno" else "#e74c3c"}; font-weight: bold;', subset=['Status Wolt', 'Status Glovo']), use_container_width=True, hide_index=True, column_config={"Link Wolt": st.column_config.LinkColumn("Link Wolt", display_text="Otvori Wolt"), "Link Glovo": st.column_config.LinkColumn("Link Glovo", display_text="Otvori Glovo")})
        else:
            st.info("Nema restorana koji se nalaze na obe platforme za odabrane adrese.")
        st.markdown("---")

        st.subheader("🔍 Detaljna Lista Restorana")
        f1, f2, f3 = st.columns(3)
        with f1: fa = st.multiselect("📍 Adresa", df["Adresa"].unique(), df["Adresa"].unique())
        with f2: fp = st.multiselect("📱 Platforma", df["Platforma"].unique(), df["Platforma"].unique())
        with f3: fs = st.multiselect("🚦 Status", ["Otvoreno", "Zatvoreno"], ["Otvoreno", "Zatvoreno"])
        filt_new = st.checkbox("✨ Prikazi samo NOVE restorane")
        f_df = df[(df["Adresa"].isin(fa)) & (df["Platforma"].isin(fp)) & (df["Status"].isin(fs))]
        if filt_new: f_df = f_df[f_df["Is_New"] == True]

        disp_df = f_df.copy()
        disp_df["Oznaka"] = disp_df["Is_New"].apply(lambda x: "✨ NOVO" if x else "")
        disp_df = disp_df.drop(columns=['Naziv_Norm', 'Vreme_Broj', 'Is_New'], errors='ignore')
        cols = ["Adresa", "Platforma", "Naziv", "Status", "Ocena", "Vreme dostave", "Oznaka", "Link"]
        disp_df = disp_df[cols]

        st.dataframe(
            disp_df.style.map(lambda v: f'color: {"#27ae60" if v=="Otvoreno" else "#e74c3c"}; font-weight: bold;', subset=['Status']), 
            use_container_width=True, hide_index=True,
            column_config={"Link": st.column_config.LinkColumn("Link", display_text="Otvori na sajtu")}
        )

        if st.session_state.pdf_fajlovi:
            st.markdown("---"); st.subheader("📥 PDF Izveštaji")
            pc = st.columns(4)
            for i, p in enumerate(st.session_state.pdf_fajlovi):
                with pc[i % 4]:
                    with open(p, "rb") as f: st.download_button(f"Preuzmi {os.path.basename(p)}", f.read(), os.path.basename(p), "application/pdf", key=f"p_{i}")
                    
        if st.session_state.error_screenshots:
            st.markdown("---")
            st.subheader("📸 Zabeležene greške (Screenshots)")
            st.warning("Prilikom poslednjeg skeniranja sistem je naišao na blokade. Pogledajte slike ekrana ispod:")
            ec = st.columns(len(st.session_state.error_screenshots))
            for idx, img_path in enumerate(st.session_state.error_screenshots):
                with ec[idx % len(ec)]:
                    st.image(img_path, caption=os.path.basename(img_path), use_container_width=True)

    rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
    while rem > 0:
        mins, secs = divmod(rem, 60)
        timer_ph.info(f"⏳ Sledeće skeniranje za: **{mins:02d}:{secs:02d}**")
        time.sleep(1); rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))
    st.rerun()
else: 
    st.info("Sistem zaustavljen. Unesite parametre i kliknite 'Pokreni'.")
