async def scrape_glovo(context_glovo, address, log_ph=None, live_ph=None, live_state=None, error_screenshots=None, debug_mode=False):
    page = None
    try:
        page = await context_glovo.new_page()
        
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.set_default_timeout(15000) 
        
        await page.goto("https://glovoapp.com/sr/rs", wait_until="domcontentloaded")
        
        try:
            accept_btn = page.locator("button", has_text=re.compile(r"Accept All|Prihvati sve", re.IGNORECASE)).first
            await accept_btn.wait_for(state="visible", timeout=4000)
            await accept_btn.click()
            await asyncio.sleep(1.5)
        except: pass
        
        stranica_tekst = await page.content()
        if "Oh, no!" in stranica_tekst or "It looks like there's a problem" in stranica_tekst:
            log_msg(f"[GLOVO BLOCKED] {address}.", log_ph)
            if error_screenshots is not None and debug_mode:
                try:
                    err_path = str(ERRORS_DIR / f"Glovo_SoftBan_{remove_accents(address).replace(' ', '_')}_{timestamp()}.png")
                    await page.screenshot(path=err_path)
                    error_screenshots.append(err_path)
                except: pass
            return []
        
        try:
            hero_input = page.locator("#hero-container-input")
            await hero_input.wait_for(state="visible", timeout=5000)
            await hero_input.click()
            search = page.get_by_role("searchbox")
            await search.fill(address)
            
            dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
            await dropdown_item.wait_for(state="visible", timeout=8000)
            await dropdown_item.click()
            
        except PlaywrightTimeoutError:
            log_msg(f"[GLOVO] Changing address in header to: {address}", log_ph)
            try:
                header_btn = page.locator('header div[role="button"]').first
                await header_btn.wait_for(state="visible", timeout=6000)
                await header_btn.click()
                
                await asyncio.sleep(1.5)
                search_modal = page.get_by_role("searchbox").last
                await search_modal.wait_for(state="visible", timeout=6000)
                await search_modal.click()
                await search_modal.fill(address)
                
                await asyncio.sleep(2.5)
                dropdown_item = page.locator("div[data-actionable='true'][role='button']").first
                await dropdown_item.wait_for(state="visible", timeout=8000)
                await dropdown_item.click()
            except PlaywrightTimeoutError:
                log_msg(f"[GLOVO ABORT] Cannot change address for {address}.", log_ph)
                if error_screenshots is not None and debug_mode:
                    try:
                        err_path = str(ERRORS_DIR / f"Glovo_Nav_Error_{remove_accents(address).replace(' ', '_')}_{timestamp()}.png")
                        await page.screenshot(path=err_path)
                        error_screenshots.append(err_path)
                    except: pass
                return []

        try:
            btn_drugo = page.locator("button:has-text('Drugo')")
            await btn_drugo.wait_for(state="visible", timeout=4000)
            await btn_drugo.click()
        except PlaywrightTimeoutError: pass
        
        # --- OVO JE JEDINI BLOK KOJI JE IZMENJEN ---
        try:
            btn_potvrdi = page.locator("button", has_text=re.compile(r"Potvrdi adresu", re.IGNORECASE)).first
            await btn_potvrdi.wait_for(state="visible", timeout=8000)
            await btn_potvrdi.click(force=True)
        except Exception: pass
        # ---------------------------------------------
        
        await asyncio.sleep(6)
        
        try:
            btn_pocetna = page.locator("text='Idi na početnu stranicu'")
            if await btn_pocetna.count() > 0 and await btn_pocetna.first.is_visible(timeout=4000):
                await btn_pocetna.first.click()
                await asyncio.sleep(6)
        except: pass
        
        try:
            kat_link = page.get_by_role("link", name=re.compile(r"Restorani|Hrana|Food|Restaurants", re.I)).first
            await kat_link.wait_for(state="visible", timeout=6000)
            await kat_link.click()
        except PlaywrightTimeoutError: pass
        
        try:
            await page.wait_for_selector("a[data-testid='store-card'], .store-card a", timeout=15000)
        except PlaywrightTimeoutError:
            log_msg(f"[GLOVO WARNING] Restorani nisu ucitani na vreme za {address}.", log_ph)
        
        page.set_default_timeout(60000) 
        rez = await smart_scroll_and_extract(page, "Glovo", address, log_ph, live_ph, live_state)
        
        if len(rez) < 5 and debug_mode:
            if error_screenshots is not None:
                err_path = str(ERRORS_DIR / f"Glovo_Warning_{remove_accents(address).replace(' ', '_')}_{timestamp()}.png")
                try:
                    await page.screenshot(path=err_path)
                    error_screenshots.append(err_path)
                except: pass

        return rez
    except Exception as e: 
        log_msg(f"[GLOVO ERROR] {e}", log_ph)
        if page and error_screenshots is not None and debug_mode:
            try:
                err_path = str(ERRORS_DIR / f"Glovo_Error_{remove_accents(address).replace(' ', '_')}_{timestamp()}.png")
                await page.screenshot(path=err_path)
                error_screenshots.append(err_path)
            except: pass
        return []
    finally:
        if page: await page.close()
