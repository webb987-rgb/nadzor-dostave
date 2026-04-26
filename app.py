Evo skripte"import asyncio

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

        

    if custom_naslov:

        naslov = custom_naslov

        

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

    for i, v in zip(pos_list, bar_list):

        if v > 0: ax.text(i, v + 0.5, f"{v:.1f} min", ha='center', va='bottom', fontweight='bold', color='#2c3e50', fontsize=9)

    max_v = max(bar_list) if max(bar_list) > 0 else 10

    ax.set_ylim(0, max_v * 1.2)

    plt.tight_layout()

    imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)

    return imgdata



def kreiraj_grafikon_popusta(df_sub, izabrane_akcije, naslov):

    if not izabrane_akcije:

        fig, ax = plt.subplots(figsize=(8, 4), facecolor='#ffffff')

        ax.text(0.5, 0.5, "Nijedan popust nije izabran", ha='center', va='center', color='#7f8c8d')

        ax.axis('off')

        imgdata = BytesIO(); fig.savefig(imgdata, format='png', bbox_inches='tight', dpi=150); imgdata.seek(0); plt.close(fig)

        return imgdata



    wolt_count = 0

    glovo_count = 0



    for _, row in df_sub.iterrows():

        akcije_restorana = []

        if pd.notna(row['Akcija']) and row['Akcija'] != "-":

            akcije_restorana = [a.replace("• ", "").strip() for a in str(row['Akcija']).split('\n') if a.strip()]

            

        if any(akcija in izabrane_akcije for akcija in akcije_restorana):

            if row['Platforma'] == 'Wolt':

                wolt_count += 1

            elif row['Platforma'] == 'Glovo':

                glovo_count += 1



    fig, ax = plt.subplots(figsize=(6, 4), facecolor='#ffffff')

    bars = ax.bar(['Wolt', 'Glovo'], [wolt_count, glovo_count], color=['#00c2e8', '#ffc244'], width=0.4)

    ax.set_ylabel('Broj restorana sa popustom', fontsize=11, fontweight='bold')

    ax.set_title(naslov, fontsize=12, fontweight='bold', color='#2c3e50')

    

    for bar in bars:

        yval = bar.get_height()

        if yval > 0:

            ax.text(bar.get_x() + bar.get_width()/2, yval + 0.1, int(yval), ha='center', va='bottom', fontweight='bold', color='#2c3e50', fontsize=10)

            

    max_v = max([wolt_count, glovo_count]) if max([wolt_count, glovo_count]) > 0 else 5

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

        if any(x in line_lower for x in ["promo", "novo", "odlično", "besplatna dostava", "artikli", "narudžb", "narudzb", "popust", "off", "discount"]): continue

        if len(line) >= 2: return line

    return ""



def analiziraj_status(text):

    t = text.lower()

    

    if any(x in t for x in ["uskoro se zatvara", "closing soon", "zatvara se za", "closes in"]):

        return "Otvoreno"

        

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



def izvuci_akciju(tekst, html, plat):

    if not tekst and not html: return "-"

    

    cist_html = re.sub(r'<[^>]+>', ' \n ', str(html))

    sve_zajedno = str(tekst) + " \n " + cist_html

    lines = [line.strip() for line in sve_zajedno.split('\n') if line.strip()]

    

    akcije = []

    for line in lines:

        t_low = line.lower()

        if len(t_low) > 80: continue 

        

        if any(x in t_low for x in ["besplatna dostava", "free delivery", "dostava 0", "delivery 0", "0 rsd delivery"]):

            akcije.append("Besplatna dostava")

        elif "1+1" in t_low or "1 + 1" in t_low or "buy 1 get 1" in t_low:

            akcije.append("1+1 Gratis")

        elif "%" in t_low and re.search(r'\d', t_low):

            if plat == "Glovo" and re.fullmatch(r'\d{1,3}\s*%', t_low):

                continue

            if any(x in t_low for x in ["-", "off", "discount", "popust", "ušted", "usted"]) or t_low.startswith("-"):

                akcije.append(line)

        elif any(x in t_low for x in ["rsd", "din"]):

            if any(x in t_low for x in ["-", "off", "discount", "popust", "save", "spend", "potroš", "preko"]):

                akcije.append(line)



    if not akcije:

        t_low_sve = re.sub(r'<[^>]+>', ' ', str(tekst) + " " + str(html)).lower()

        procenti = re.findall(r'(-\s*\d{1,2}\s*%|\b\d{1,2}\s*%\s*popust|\b\d{1,2}\s*%\s*off|\b\d{1,2}\s*%\s*discount)', t_low_sve)

        iznosi = re.findall(r'(-\d{3,4}\s*(?:rsd|din)|\b\d{3,4}\s*(?:rsd|din)\s*off|spend\s*\d{3,4}\s*(?:rsd|din))', t_low_sve)

        akcije.extend([p.strip() for p in procenti])

        akcije.extend([i.strip() for i in iznosi])



    sve_low = (str(tekst) + " " + str(html)).lower()

    if "wolt+" in sve_low and not any("wolt+" in a.lower() for a in akcije):

        akcije.append("Wolt+")

    if "prime" in sve_low and not any("prime" in a.lower() for a in akcije):

        akcije.append("Prime")

        

    if akcije:

        seen = set()

        res = []

        for a in akcije:

            a_clean = re.sub(r'<[^>]+>', '', a).strip()

            if not a_clean: continue

            

            if "besplatna" in a_clean.lower() or "free" in a_clean.lower():

                a_clean = "Besplatna dostava"

            elif a_clean not in ["Wolt+", "Prime"]:

                a_clean = a_clean[0].upper() + a_clean[1:]

                a_clean = a_clean.replace("rsd", "RSD").replace("din", "DIN").replace("off", "Off").replace("discount", "Discount")



            if a_clean not in seen:

                seen.add(a_clean)

                res.append(f"• {a_clean}")

                

        if res:

            return "\n".join(res)

            

    return "-"



def normalizuj_ime(ime): return re.sub(r'[^\w]', '', ime.lower())



# ---------------- LJUDSKO SKROLOVANJE SA RADAROM (NETWORK INTERCEPTION) ----------------

async def pametno_skrolovanje_i_ekstrakcija(page, plat, address, log_ph=None, prog_bar=None):

    results_dict = {}

    prethodni_broj = 0

    pokusaji_na_dnu = 0

    

    # === RADAR SISTEM (Prati šta Glovo skida u pozadini) ===

    radar = {"aktivno": False, "poslednji_ulov": time.time()}



    async def na_zahtev(request):

        # Ako Glovo zatraži novih 50 restorana, radar se pali

        if "offset=" in request.url and "limit=" in request.url:

            radar["aktivno"] = True



    async def na_odgovor(response):

        # Kad podaci stignu (200 OK), radar beleži vreme

        if "offset=" in response.url and "limit=" in response.url and response.status == 200:

            radar["aktivno"] = False

            radar["poslednji_ulov"] = time.time()



    if plat == "Glovo":

        page.on("request", na_zahtev)

        page.on("response", na_odgovor)

    # ========================================================

    

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

            

            is_new = False

            t_low = text.strip().lower()

            if plat == "Wolt":

                is_new = bool(re.search(r'>\s*(novo|new)\s*<', html_content.lower())) or (ocena == "Novo")

            else:

                is_new = t_low.endswith('new') or t_low.endswith('novo') or bool(re.search(r'•.*?new\b', t_low)) or (ocena == "Novo")



            results_dict[link] = {

                "Adresa": address, "Platforma": plat, "Naziv": ime, "Ocena": ocena,

                "Vreme dostave": vreme_str, "Akcija": akcija_str, "Status": analiziraj_status(sve_z),

                "Vreme_Broj": vreme_num, "Is_New": is_new, "Link": link

            }



        trenutni = len(results_dict)

        if trenutni > prethodni_broj:

            log_msg(f"[{plat.upper()} - {address}] Učitano {trenutni} restorana...", log_ph)

            

            if prog_bar:

                prog_val = min(trenutni / 1500.0, 1.0)

                if prog_val == 1.0: prog_val = 0.99 

                prog_bar.progress(prog_val, text=f"[{plat.upper()}] Skeniram '{address}'... Pronađeno: {trenutni} restorana")

                

            prethodni_broj = trenutni

            pokusaji_na_dnu = 0

            

        await page.evaluate("window.scrollBy(0, window.innerHeight);")

        await asyncio.sleep(0.8)

        

        h = await page.evaluate("document.body.scrollHeight")

        s = await page.evaluate("window.scrollY + window.innerHeight")

        

        if s >= h - 100:

            pokusaji_na_dnu += 1

            

            if plat == "Glovo":

                # AKO JE RADAR AKTIVAN (Glovo skida podatke), resetujemo tajmer i čekamo!

                if radar["aktivno"]:

                    pokusaji_na_dnu = 0

                    await asyncio.sleep(2)

                    continue

                # Ako je upravo skinuo (pre manje od 3 sekunde), damo mu vremena da nacrta

                elif time.time() - radar["poslednji_ulov"] < 3.0:

                    pokusaji_na_dnu = 0

                    await asyncio.sleep(1.5)

                    continue



            await asyncio.sleep(1.5)

            

            # Kraj samo ako smo na dnu, i radar se potpuno ućutao

            if pokusaji_na_dnu >= 5: 

                break 

        

    return list(results_dict.values())



# ---------------- SCRAPERS (STEALTH + FAST FAIL) ----------------

async def scrape_wolt(context_wolt, address, log_ph=None, error_screenshots=None, prog_bar=None):

    page = None

    try:

        page = await context_wolt.new_page()

        

        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page.set_default_timeout(10000)

        

        if prog_bar: prog_bar.progress(0.05, text=f"[WOLT] Otvaram sajt za adresu: {address}...")

        await page.goto("https://wolt.com/sr/srb")

        

        try: await page.locator("[data-test-id='allow-button']").click(timeout=3000)

        except: pass

        

        try:

            input_f = page.get_by_role("combobox").first

            await input_f.wait_for(state="visible", timeout=4000)

            await input_f.click(timeout=3000)

            await input_f.fill(address)

            await asyncio.sleep(2)

            await page.keyboard.press("ArrowDown")

            await page.keyboard.press("Enter")

            await asyncio.sleep(5)

            await page.goto("https://wolt.com/sr/discovery/restaurants")

            try: await page.wait_for_selector("a[data-test-id^='venueCard.']", timeout=8000)

            except PlaywrightTimeoutError: pass

            

        except PlaywrightTimeoutError:

            log_msg(f"[WOLT] VIP mod (Ulogovan). Menjam adresu u header-u za: {address}", log_ph)

            try:

                header_btn = page.locator("[data-test-id='header.address-select-button']")

                if not await header_btn.is_visible():

                    header_btn = page.locator("header [role='button']").first

                    

                await header_btn.wait_for(state="visible", timeout=5000)

                await header_btn.click()

                await asyncio.sleep(1)



                search_modal = page.locator("[data-test-id='address-picker-input']")

                if not await search_modal.is_visible():

                    search_modal = page.get_by_role("combobox").last

                    

                await search_modal.wait_for(state="visible", timeout=5000)

                await search_modal.click()

                await search_modal.fill(address)



                await asyncio.sleep(2)

                await page.keyboard.press("ArrowDown")

                await page.keyboard.press("Enter")

                await asyncio.sleep(5)

                

                await page.goto("https://wolt.com/sr/discovery/restaurants")

                try: await page.wait_for_selector("a[data-test-id^='venueCard.']", timeout=8000)

                except PlaywrightTimeoutError: pass

                

            except PlaywrightTimeoutError:

                log_msg(f"[WOLT ODUSTAJEM] Ne mogu da nadjem polje za promenu adrese.", log_ph)

                if page and error_screenshots is not None:

                    try:

                        err_path = str(ERRORS_DIR / f"Wolt_Timeout_{ukloni_kvacice(address).replace(' ', '_')}_{timestamp()}.png")

                        await page.screenshot(path=err_path)

                        error_screenshots.append(err_path)

                    except: pass

                return []

                

        rez = await pametno_skrolovanje_i_ekstrakcija(page, "Wolt", address, log_ph, prog_bar)

        return rez



    except Exception as e: 

        log_msg(f"[WOLT GREŠKA] {e}", log_ph)

        if page and error_screenshots is not None:

            try:

                err_path = str(ERRORS_DIR / f"Wolt_Error_{ukloni_kvacice(address).replace(' ', '_')}_{timestamp()}.png")

                await page.screenshot(path=err_path)

                error_screenshots.append(err_path)

            except: pass

        return []

    finally:

        if page: await page.close()



async def scrape_glovo(context_glovo, address, log_ph=None, error_screenshots=None, prog_bar=None):

    page = None

    try:

        page = await context_glovo.new_page()

        

        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page.set_default_timeout(10000)

        

        if prog_bar: prog_bar.progress(0.05, text=f"[GLOVO] Otvaram sajt za adresu: {address}...")

        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")

        

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

        

        try:

            hero_input = page.locator("#hero-container-input")

            await hero_input.wait_for(state="visible", timeout=4000)

            await hero_input.click()

            search = page.get_by_role("searchbox")

            await search.fill(address)

            

            dropdown_item = page.locator("div[data-actionable='true'][role='button']").first

            await dropdown_item.wait_for(state="visible", timeout=8000)

            await dropdown_item.click()

            

        except PlaywrightTimeoutError:

            log_msg(f"[GLOVO] Ulogovan sam, menjam adresu u header-u za: {address}", log_ph)

            try:

                header_btn = page.locator('header div[role="button"]').first

                await header_btn.wait_for(state="visible", timeout=5000)

                await header_btn.click()

                

                await asyncio.sleep(1)

                search_modal = page.get_by_role("searchbox").last

                await search_modal.wait_for(state="visible", timeout=5000)

                await search_modal.click()

                await search_modal.fill(address)

                

                await asyncio.sleep(2)

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

            kat_link = page.get_by_role("link", name=re.compile(r"Restorani|Hrana|Food|Restaurants", re.I)).first

            await kat_link.wait_for(state="visible", timeout=5000)

            await kat_link.click()

        except PlaywrightTimeoutError: pass

        

        await asyncio.sleep(5)

        page.set_default_timeout(60000) 

        rez = await pametno_skrolovanje_i_ekstrakcija(page, "Glovo", address, log_ph, prog_bar)

        return rez

    except Exception as e: 

        log_msg(f"[GLOVO GREŠKA] {e}", log_ph)

        if page and error_screenshots is not None:

            try:

                err_path = str(ERRORS_DIR / f"Glovo_Error_{ukloni_kvacice(address).replace(' ', '_')}_{timestamp()}.png")

                await page.screenshot(path=err_path)

                error_screenshots.append(err_path)

            except: pass

        return []

    finally:

        if page: await page.close()



# ---------------- SEKVENCIJALNI PROCES SKENIRANJA (SPAS ZA RAM) ----------------

async def proces_skeniranja(adrese, log_ph, prog_bar, generisi_pdf=False, email_primaoca=""):

    sve = []

    error_screenshots = [] 

    

    async with async_playwright() as p:

        browser = await p.chromium.launch(

            headless=True,

            args=[

                "--disable-blink-features=AutomationControlled",

                "--disable-dev-shm-usage", 

                "--no-sandbox"             

            ]

        ) 

        

        wolt_args = {

            "permissions": ['geolocation'],

            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        }

        if os.path.exists(WOLT_AUTH_FILE):

            log_msg("🔐 WOLT: Učitana VIP propusnica.", log_ph)

            wolt_args["storage_state"] = WOLT_AUTH_FILE

            

        glovo_args = {

            "permissions": ['geolocation'],

            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",

            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9,sr;q=0.8"}

        }

        if os.path.exists(GLOVO_AUTH_FILE):

            log_msg("🔐 GLOVO: Učitana VIP propusnica.", log_ph)

            glovo_args["storage_state"] = GLOVO_AUTH_FILE

            

        for i, adr in enumerate(adrese):

            if i > 0:

                log_msg("⏳ Pauza 5 sekundi izmedju adresa...", log_ph)

                await asyncio.sleep(5)

                

            log_msg(f"\n[SISTEM] Pokrećem skeniranje za: {adr}", log_ph)

            

            # SEKVENCIJALNO: Prvo ide GLOVO, pa tek onda WOLT

            log_msg("📱 Skrolujem GLOVO...", log_ph)

            context_glovo = await browser.new_context(**glovo_args)

            r_glovo = await scrape_glovo(context_glovo, adr, log_ph, error_screenshots, prog_bar)

            sve.extend(r_glovo)

            await context_glovo.close() 

            

            log_msg("🚲 Skrolujem WOLT...", log_ph)

            context_wolt = await browser.new_context(**wolt_args)

            r_wolt = await scrape_wolt(context_wolt, adr, log_ph, error_screenshots, prog_bar)

            sve.extend(r_wolt)

            await context_wolt.close() 

                

        await browser.close()

            

    if sve:

        df_s = pd.DataFrame(sve)

        df_h = sacuvaj_u_istoriju(df_s)

        

        pdf_fajlovi = []

        if generisi_pdf:

            log_msg("Generišem PDF izveštaje...", log_ph)

            if prog_bar: prog_bar.progress(1.0, text="Generišem PDF izvještaje...")

            zbirni = napravi_zbirni_pdf(df_s, df_h)

            if zbirni: pdf_fajlovi.append(zbirni)

            

            for adr in df_s["Adresa"].unique():

                df_sub = df_s[df_s["Adresa"] == adr]

                p_fajl = napravi_pdf_za_adresu(df_sub, adr, df_h)

                if p_fajl: pdf_fajlovi.append(p_fajl)

                

            if email_primaoca.strip() and pdf_fajlovi:

                log_msg(f"Šaljem izveštaje na email: {email_primaoca}", log_ph)

                posalji_email(pdf_fajlovi, email_primaoca, log_ph)

        else:

            log_msg("Skeniranje završeno. Opcija za PDF je isključena.", log_ph)

            

        return df_s, df_h, pdf_fajlovi, error_screenshots

    return pd.DataFrame(), pd.DataFrame(), [], error_screenshots



# ================= STREAMLIT UI =================

if 'pokrenuto' not in st.session_state: st.session_state.pokrenuto = False

if 'last_run' not in st.session_state: st.session_state.last_run = 0

if 'df_sve' not in st.session_state: st.session_state.df_sve = pd.DataFrame()

if 'pdf_fajlovi' not in st.session_state: st.session_state.pdf_fajlovi = []

if 'error_screenshots' not in st.session_state: st.session_state.error_screenshots = []

if 'loaded_history' not in st.session_state: st.session_state.loaded_history = False



if 'df_history' not in st.session_state: 

    if os.path.exists(HISTORY_FILE):

        st.session_state.df_history = pd.read_csv(HISTORY_FILE)

    else:

        st.session_state.df_history = pd.DataFrame()



st.title("🍔 Nadzor Dostave (Wolt & Glovo)")

with st.sidebar:

    st.header("⚙️ Podešavanja")

    

    adresa_1 = st.text_input("📍 Adresa 1 (Obavezna):", value="", placeholder="Makenzijeva 57, Beograd")

    adresa_2 = st.text_input("📍 Adresa 2 (Opciona):", value="", placeholder="Somborska 5, Niš")

    

    auto_refresh = st.checkbox("🔄 Automatsko osvežavanje", value=False)

    sleep_interval = st.number_input("⏱️ Interval (min):", min_value=1, value=60, disabled=not auto_refresh)

    

    generisi_pdf = st.checkbox("📄 Generiši PDF izveštaje", value=False)

    email_unos = ""

    if generisi_pdf:

        email_unos = st.text_input("📧 Pošalji izveštaj na email (opciono):", placeholder="tvoj@email.com")

    

    timer_ph = st.empty()

    c1, c2 = st.columns(2)

    with c1:

        if st.button("▶️ Pokreni", type="primary"): 

            st.session_state.pokrenuto = True

            st.session_state.loaded_history = False

            st.session_state.last_run = 0

            st.rerun()

    with c2:

        if st.button("⏹️ Zaustavi"): 

            st.session_state.pokrenuto = False

            st.rerun()

            

    if st.button("🗑️ Obriši istoriju", use_container_width=True):

        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)

        st.session_state.df_history = pd.DataFrame(); st.rerun()



    st.markdown("---")

    st.header("📂 Stari izveštaji")

    istorija_fajlovi = sorted(list(OUTPUT_DIR.glob("Detaljno_*.csv")), reverse=True)

    

    opcije = {"--- Izaberi ---": None}

    for f in istorija_fajlovi:

        ime = f.stem.replace("Detaljno_", "")

        try:

            dt_obj = datetime.datetime.strptime(ime, "%Y%m%d_%H%M%S")

            prikaz = dt_obj.strftime("%d.%m.%Y u %H:%M:%S")

        except:

            prikaz = ime

        opcije[prikaz] = f



    izabrani_fajl = st.selectbox("Učitaj prethodno skeniranje:", list(opcije.keys()))

    if st.button("📂 Učitaj izveštaj"):

        fajl = opcije[izabrani_fajl]

        if fajl:

            st.session_state.df_sve = pd.read_csv(fajl)

            st.session_state.pokrenuto = False

            st.session_state.loaded_history = True

            st.session_state.last_run = os.path.getmtime(fajl)

            st.rerun()



if st.session_state.pokrenuto or st.session_state.loaded_history:



    if st.session_state.pokrenuto:

        lista_adresa = []

        if adresa_1.strip():

            lista_adresa.append(cirilica_u_latinicu(adresa_1.strip()))

        if adresa_2.strip():

            lista_adresa.append(cirilica_u_latinicu(adresa_2.strip()))



        if not adresa_1.strip(): 

            st.warning("⚠️ Unesite bar prvu adresu (Adresa 1) da biste pokrenuli skeniranje!")

            st.session_state.pokrenuto = False

            st.rerun()



        now = time.time()

        if now - st.session_state.last_run >= sleep_interval * 60 or st.session_state.last_run == 0:

            timer_ph.warning("⏳ Skeniranje u toku...")

            sl = st.empty()

            

            prog_bar = st.progress(0.0, text="Priprema sistema za skeniranje...")

            

            df, hi, pdf, err_imgs = asyncio.run(proces_skeniranja(lista_adresa, sl, prog_bar, generisi_pdf, email_unos))

            

            if not df.empty:

                ts = timestamp()

                df.to_csv(OUTPUT_DIR / f"Detaljno_{ts}.csv", index=False)



            prog_bar.empty()

            st.session_state.df_sve, st.session_state.df_history, st.session_state.pdf_fajlovi, st.session_state.error_screenshots, st.session_state.last_run = df, hi, pdf, err_imgs, time.time()

            sl.empty(); st.rerun()



    df = st.session_state.df_sve

    if not df.empty:

        for col in ["Vreme_Broj", "Vreme dostave", "Ocena", "Is_New"]:

            if col not in df.columns: df[col] = False if col == "Is_New" else (np.nan if "Broj" in col else "-")



        if st.session_state.loaded_history:

            st.info("📂 **Prikazuje se učitani istorijski izveštaj.** Da biste skenirali ponovo, unesite adrese u meniju i kliknite 'Pokreni'.")

        else:

            st.success(f"✅ Osveženo u: {datetime.datetime.fromtimestamp(st.session_state.last_run, LOCAL_TZ).strftime('%H:%M:%S')}")

        

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

        

        st.markdown("---")

        st.markdown("##### 🎁 Analiza Popusta i Akcija")

        

        unikatne_akcije = set()

        for akcija_str in c_df['Akcija']:

            if pd.notna(akcija_str) and str(akcija_str) != "-":

                for a in str(akcija_str).split('\n'):

                    cl = a.replace("• ", "").strip()

                    if cl: unikatne_akcije.add(cl)

        unikatne_akcije = sorted(list(unikatne_akcije))

        

        izabrani_popusti = st.multiselect("Odaberi akcije za prikaz na grafikonu:", unikatne_akcije, default=unikatne_akcije)

        st.image(kreiraj_grafikon_popusta(c_df, izabrani_popusti, "Broj restorana sa izabranim akcijama"), use_container_width=False)

        

        st.markdown("---")

        st.markdown("##### 📅 Istorijat Aktivnosti (Filter Vremena)")

        

        hist_df = st.session_state.df_history.copy()

        if not hist_df.empty:

            c_h = hist_df if graf_adr == "Sve adrese" else hist_df[hist_df["Adresa"] == graf_adr]

            

            if not c_h.empty and 'Datum' in c_h.columns and 'Vreme' in c_h.columns:

                c_h['Datetime'] = pd.to_datetime(c_h['Datum'] + ' ' + c_h['Vreme'])

                min_d = c_h['Datetime'].min().date()

                max_d = c_h['Datetime'].max().date()

                

                c_dt1, c_dt2, c_dt3, c_dt4 = st.columns(4)

                with c_dt1: start_date = st.date_input("Od datuma:", min_d, min_value=min_d, max_value=max_d)

                with c_dt2: start_time = st.time_input("Od vremena:", datetime.time(0, 0))

                with c_dt3: end_date = st.date_input("Do datuma:", max_d, min_value=min_d, max_value=max_d)

                with c_dt4: end_time = st.time_input("Do vremena:", datetime.time(23, 59))

                

                start_dt = pd.to_datetime(datetime.datetime.combine(start_date, start_time))

                end_dt = pd.to_datetime(datetime.datetime.combine(end_date, end_time))

                

                mask = (c_h['Datetime'] >= start_dt) & (c_h['Datetime'] <= end_dt)

                chart_hist = c_h.loc[mask].copy()

                st.image(kreiraj_timeline_grafikon(chart_hist, None, "Istorijat aktivnosti"), use_container_width=True)

            else:

                st.info("Nema istorije za odabranu adresu.")

        else:

            st.info("Nema istorijskih podataka.")



        st.markdown("---")

        st.subheader("⚖️ Uporedni Prikaz (Restorani na obe platforme)")

        

        c_up1, c_up2 = st.columns(2)

        with c_up1: filter_wolt_up = st.multiselect("🚦 Prikaz za Wolt:", ["Otvoreno", "Zatvoreno"], default=["Otvoreno", "Zatvoreno"], key="fw")

        with c_up2: filter_glovo_up = st.multiselect("🚦 Prikaz za Glovo:", ["Otvoreno", "Zatvoreno"], default=["Otvoreno", "Zatvoreno"], key="fg")



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

            df_uporedni = df_uporedni[(df_uporedni['Status Wolt'].isin(filter_wolt_up)) & (df_uporedni['Status Glovo'].isin(filter_glovo_up))]

            

            if not df_uporedni.empty:

                st.dataframe(df_uporedni.style.map(lambda val: f'color: {"#27ae60" if val=="Otvoreno" else "#e74c3c"}; font-weight: bold;', subset=['Status Wolt', 'Status Glovo']), use_container_width=True, hide_index=True, column_config={"Link Wolt": st.column_config.LinkColumn("Link Wolt", display_text="Otvori Wolt"), "Link Glovo": st.column_config.LinkColumn("Link Glovo", display_text="Otvori Glovo")})

            else:

                st.info("Nema restorana koji odgovaraju odabranim filterima statusa.")

        else:

            st.info("Nema restorana koji se nalaze na obe platforme za odabrane adrese.")

        st.markdown("---")



        st.subheader("🔍 Detaljna Lista Restorana")

        f1, f2, f3 = st.columns(3)

        with f1: fa = st.multiselect("📍 Adresa", df["Adresa"].unique(), df["Adresa"].unique())

        with f2: fp = st.multiselect("📱 Platforma", df["Platforma"].unique(), df["Platforma"].unique())

        with f3: fs = st.multiselect("🚦 Status", ["Otvoreno", "Zatvoreno"], ["Otvoreno", "Zatvoreno"])

        

        c_filt1, c_filt2 = st.columns(2)

        with c_filt1: filt_new = st.checkbox("✨ Prikaži samo NOVE restorane")

        with c_filt2: filt_promo = st.checkbox("🔥 Prikaži samo restorane SA AKCIJAMA")

        

        f_df = df[(df["Adresa"].isin(fa)) & (df["Platforma"].isin(fp)) & (df["Status"].isin(fs))]

        if filt_new: 

            f_df = f_df[f_df["Is_New"].isin([True, 'True', 'true', 1])]

        if filt_promo: 

            f_df = f_df[f_df["Akcija"] != "-"]



        disp_df = f_df.copy()

        disp_df["Oznaka"] = disp_df["Is_New"].apply(lambda x: "✨ NOVO" if x in [True, 'True', 'true', 1] else "")

        disp_df = disp_df.drop(columns=['Naziv_Norm', 'Vreme_Broj', 'Is_New'], errors='ignore')

        

        cols = ["Adresa", "Platforma", "Naziv", "Status", "Ocena", "Vreme dostave", "Akcija", "Oznaka", "Link"]

        disp_df = disp_df[cols]



        def style_rows(row):

            styles = [''] * len(row)

            status_idx = row.index.get_loc('Status')

            akcija_idx = row.index.get_loc('Akcija')

            

            if row['Status'] == 'Otvoreno': styles[status_idx] = 'color: #27ae60; font-weight: bold;'

            else: styles[status_idx] = 'color: #e74c3c; font-weight: bold;'

                

            if row['Akcija'] != '-': styles[akcija_idx] = 'color: #8e44ad; font-weight: bold;'

            

            return styles



        st.dataframe(

            disp_df.style.apply(style_rows, axis=1), 

            use_container_width=True, hide_index=True,

            column_config={

                "Link": st.column_config.LinkColumn("Link", display_text="Otvori na sajtu"),

                "Akcija": st.column_config.TextColumn("Akcija", width="large")

            }

        )



        if st.session_state.get('pdf_fajlovi'):

            st.markdown("---"); st.subheader("📥 PDF Izveštaji")

            pc = st.columns(4)

            for i, p in enumerate(st.session_state.pdf_fajlovi):

                with pc[i % 4]:

                    with open(p, "rb") as f: st.download_button(f"Preuzmi {os.path.basename(p)}", f.read(), os.path.basename(p), "application/pdf", key=f"p_{i}")

                    

        if st.session_state.get('error_screenshots'):

            st.markdown("---")

            st.subheader("📸 Zabeležene greške (Screenshots)")

            st.warning("Prilikom posljednjeg skeniranja sistem je naišao na blokade. Pogledajte slike ekrana ispod:")

            ec = st.columns(len(st.session_state.error_screenshots))

            for idx, img_path in enumerate(st.session_state.error_screenshots):

                with ec[idx % len(ec)]:

                    st.image(img_path, caption=os.path.basename(img_path), use_container_width=True)



    if st.session_state.pokrenuto:

        if auto_refresh:

            rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))

            while rem > 0:

                mins, secs = divmod(rem, 60)

                timer_ph.info(f"⏳ Sljedeće automatsko skeniranje za: **{mins:02d}:{secs:02d}**")

                time.sleep(1)

                rem = int((sleep_interval * 60) - (time.time() - st.session_state.last_run))

            st.rerun()

        else:

            timer_ph.success("✅ Skeniranje završeno. Kliknite 'Pokreni' za novo skeniranje.")

        

else: 

    st.info("Sistem je spreman. Unesite parametre i kliknite 'Pokreni'.")

"
