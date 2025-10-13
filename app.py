# app.py — Render-ready Instagram scraper (Selenium + Chromium)
# - Robust login: cookie-banner killer, selector fallbacks, retries
# - Optional cookie-based login via IG_SESSIONID to bypass login form
# - Unique Chrome profile per run to avoid "user data directory in use"
# - Saves CSV to /data (attach a Render Disk for persistence)

import os
import time
import random
import tempfile
import uuid
from datetime import datetime
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# ================== CONFIG (env-driven) ==================
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
PROFILE_URL       = os.getenv("PROFILE_URL", "https://www.instagram.com/srija_sweetiee/")
OUTPUT_FILE       = os.getenv("OUTPUT_FILE", "/data/Srija_posts.csv")
START_DATE        = os.getenv("START_DATE", "2025-09-29")  # yyyy-mm-dd
END_DATE          = os.getenv("END_DATE",   "2025-10-10")  # yyyy-mm-dd
IG_SESSIONID      = os.getenv("IG_SESSIONID", "")          # optional: cookie login

# Fail fast on bad dates
start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").date()
end_dt   = datetime.strptime(END_DATE,   "%Y-%m-%d").date()

# ================== CHROME (Render-safe) ==================
chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--window-size=1280,1696")
chrome_options.add_argument("--disable-features=VizDisplayCompositor")
chrome_options.add_argument("--lang=en-US")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option("useAutomationExtension", False)
chrome_options.add_argument("--remote-debugging-port=9222")
chrome_options.add_argument(
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

# Unique user-data-dir to avoid profile lock
user_data_dir = os.path.join(tempfile.gettempdir(), f"chrome-user-data-{uuid.uuid4().hex}")
chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

# CHROME_BIN / CHROMEDRIVER are set in Dockerfile; Selenium Manager is also fine
service = Service()
driver = webdriver.Chrome(service=service, options=chrome_options)
wait = WebDriverWait(driver, 20)


# ================== UTILITIES ==================
def _save_debug(prefix="debug"):
    """Dump current page HTML + PNG to /data for debugging."""
    try:
        os.makedirs("/data", exist_ok=True)
        html_path = f"/data/{prefix}.html"
        png_path  = f"/data/{prefix}.png"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        driver.save_screenshot(png_path)
        print(f"🧪 Saved debug artifacts: {html_path}, {png_path}")
    except Exception as e:
        print(f"⚠️ Could not save debug artifacts: {e}")


def _dismiss_cookie_banners():
    texts = [
        "Allow all cookies", "Only allow essential cookies", "Accept All",
        "Accept", "Allow All", "Agree", "Got it", "OK"
    ]
    xpaths = [
        '//button[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"), "{}")]'.format(t.lower())
        for t in texts
    ]
    for xp in xpaths:
        try:
            b = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xp)))
            driver.execute_script("arguments[0].click();", b)
            time.sleep(1.0)
            break
        except Exception:
            pass


def _find_login_inputs(timeout=8):
    pairs = [
        (By.NAME, "username"), (By.CSS_SELECTOR, 'input[name="username"]'),
        (By.XPATH, '//input[@name="username"]')
    ]
    for by, val in pairs:
        try:
            u = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, val)))
            p = driver.find_element(By.NAME, "password")
            return u, p
        except Exception:
            continue
    return None, None


# ================== AUTH ==================
def bootstrap_with_cookie() -> bool:
    """Login by setting session cookie (best reliability on cloud IPs)."""
    if not IG_SESSIONID:
        return False
    print("🍪 Using IG_SESSIONID cookie login…")
    driver.get("https://www.instagram.com/")
    time.sleep(2)
    try:
        driver.add_cookie({
            "name": "sessionid",
            "value": IG_SESSIONID,
            "domain": ".instagram.com",
            "path": "/",
            "httpOnly": True,
            "secure": True
        })
        driver.refresh()
        time.sleep(3)
        if "accounts/login" not in driver.current_url:
            print("✅ Cookie login successful.")
            return True
    except Exception as e:
        print(f"⚠️ Cookie login failed: {e}")
    return False


def login_with_form():
    print("🔐 Logging in via form…")
    attempts = 0
    while attempts < 3:
        attempts += 1
        driver.get("https://www.instagram.com/accounts/login/?hl=en")
        time.sleep(4)
        _dismiss_cookie_banners()

        # Sometimes a home page shows with "Log in" link; try it:
        if "accounts/login" not in driver.current_url:
            try:
                link = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, '//a[contains(@href,"/accounts/login/")]'))
                )
                driver.execute_script("arguments[0].click();", link)
                time.sleep(3)
            except Exception:
                pass

        u, p = _find_login_inputs(timeout=8)
        if u and p:
            try:
                u.clear(); p.clear()
                if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
                    raise SystemExit("❌ Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD env vars.")
                u.send_keys(INSTAGRAM_USERNAME)
                p.send_keys(INSTAGRAM_PASSWORD)
                WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, '//button[@type="submit"]'))
                ).click()
                time.sleep(7)

                # Sanity check
                driver.get("https://www.instagram.com/")
                time.sleep(4)
                if "accounts/login" not in driver.current_url:
                    print("✅ Logged in.")
                    return
            except Exception:
                pass

        print(f"⚠️ Login attempt {attempts} failed; retrying…")
        time.sleep(3)

    _save_debug("login_fail")
    raise SystemExit("❌ Could not locate/complete login form. See /data/login_fail.*")


# ================== NAVIGATION & SCRAPE ==================
def open_first_post():
    print(f"🌐 Opening profile: {PROFILE_URL}")
    driver.get(PROFILE_URL)
    time.sleep(5)
    _dismiss_cookie_banners()

    # Open first tile (anchor to post/reel)
    # This fallback picks the first visible post or reel link on the grid/modal page.
    candidates = [
        '//a[contains(@href, "/p/") or contains(@href,"/reel/")]',
    ]
    for xp in candidates:
        try:
            elem = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            driver.execute_script("arguments[0].scrollIntoView({block: " '"center"});', elem)
            time.sleep(1.5)
            driver.execute_script("arguments[0].click();", elem)
            time.sleep(3)
            print("✅ Opened first post.")
            return
        except Exception:
            continue
    _save_debug("first_post_fail")
    raise SystemExit("❌ Could not open first post (selector drift).")


def scrape():
    print("🧹 Starting scrape…")
    rows = []
    post_count = 0

    while True:
        post_count += 1
        print(f"\n📸 Post {post_count}")
        post_url = driver.current_url

        # Date
        try:
            date_el = wait.until(EC.presence_of_element_located((By.TAG_NAME, "time")))
            date_posted = date_el.get_attribute("datetime")[:10]  # yyyy-mm-dd
            date_obj = datetime.fromisoformat(date_posted).date()
        except Exception:
            date_posted = "Unknown"
            date_obj = None

        # Stop if older than START_DATE after a few posts
        if post_count > 3 and date_obj and date_obj < start_dt:
            print(f"🛑 Older than {START_DATE}. Stopping.")
            break

        # Likes (may be hidden)
        try:
            likes = driver.find_element(By.XPATH, '//section[2]//span[contains(@class,"_aamw")]').text
        except NoSuchElementException:
            likes = "Hidden"

        # Caption (best-effort; DOM changes frequently)
        caption_text = ""
        for xp in ['//div[@role="dialog"]//h1', '//h1']:
            try:
                caption_text = driver.find_element(By.XPATH, xp).text.strip()
                if caption_text:
                    break
            except Exception:
                pass

        rows.append({
            "Post_Number": post_count,
            "URL": post_url,
            "Date": date_posted,
            "Likes": likes,
            "Comment": caption_text
        })

        # Next button (modal or inline)
        try:
            next_btn = wait.until(EC.element_to_be_clickable((
                By.XPATH,
                '//button[@aria-label="Next" and contains(@class,"_abl-")] | '
                '//div[@role="dialog"]//button[@aria-label="Next"]'
            )))
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(random.uniform(2.5, 4.5))
        except TimeoutException:
            print("⚠️ Next button not found. Stopping.")
            break

    # Save CSV
    if rows:
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        df = pd.DataFrame(rows)
        df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
        print(f"\n✅ Saved {len(df)} rows to {OUTPUT_FILE}")
    else:
        print("\n⚠️ No data scraped.")


# ================== MAIN ==================
if __name__ == "__main__":
    try:
        # Prefer cookie login if provided; otherwise use form login
        if not bootstrap_with_cookie():
            login_with_form()
        open_first_post()
        scrape()
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    print("\n✅ Done.")
