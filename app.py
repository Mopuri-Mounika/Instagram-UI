# app.py — Render Web Service friendly Instagram scraper
# - Binds to $PORT via Flask (health endpoint) to satisfy Render Web Service
# - Cookie-first login (INSTAGRAM_SESSIONID, DS_USER_ID), fallback to robust login
# - Unique Chrome profile per run; container-safe flags

import os
import atexit
import time
import random
import shutil
import tempfile
import threading
from datetime import datetime
from typing import Optional, Tuple, List

import pandas as pd
from flask import Flask, jsonify

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, SessionNotCreatedException
)

# =======================
# CONFIG (use ENV on Render)
# =======================
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "").strip()
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "").strip()
PROFILE_URL        = os.getenv("PROFILE_URL", "https://www.instagram.com/srija_sweetiee/")
OUTPUT_FILE        = os.getenv("OUTPUT_FILE", "Srija_posts.csv")

# Cookie-based session (preferred in cloud)
IG_SESSIONID = os.getenv("INSTAGRAM_SESSIONID", "").strip()
IG_DS_USERID = os.getenv("DS_USER_ID", "").strip()  # ds_user_id cookie

# Date range (YYYY-M-D or YYYY-MM-DD)
START_DATE = os.getenv("START_DATE", "2025-09-29")
END_DATE   = os.getenv("END_DATE",   "2025-10-10")

# Optional: force mobile user-agent (often simpler flow on cloud IPs)
USE_MOBILE_UA = True

# =======================
# Chrome / Selenium setup
# =======================
def make_chrome_options(user_data_dir: str) -> Options:
    opts = Options()
    # Container-safe flags
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--mute-audio")
    opts.add_argument("--lang=en-US")

    # Unique profile paths (fixes "user data dir in use")
    opts.add_argument(f"--user-data-dir={user_data_dir}")
    opts.add_argument(f"--data-path={os.path.join(user_data_dir,'data')}")
    opts.add_argument(f"--disk-cache-dir={os.path.join(user_data_dir,'cache')}")
    opts.add_argument(f"--homedir={user_data_dir}")

    if USE_MOBILE_UA:
        MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                     "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
        opts.add_argument(f"--user-agent={MOBILE_UA}")

    chrome_bin = os.getenv("CHROME_PATH")  # optional if your image provides Chrome
    if chrome_bin:
        opts.binary_location = chrome_bin

    return opts

def create_driver_with_retry(retries: int = 2) -> Tuple[webdriver.Chrome, str]:
    """Start Chrome with a fresh temp profile; retry if SessionNotCreatedException occurs."""
    last_err = None
    for attempt in range(1, retries + 2):
        temp_profile = tempfile.mkdtemp(prefix="chrome-profile-")

        def _cleanup():
            try:
                shutil.rmtree(temp_profile, ignore_errors=True)
            except Exception:
                pass
        atexit.register(_cleanup)

        options = make_chrome_options(temp_profile)
        try:
            service = Service()  # Selenium Manager auto-resolves driver
            driver = webdriver.Chrome(service=service, options=options)
            return driver, temp_profile
        except SessionNotCreatedException as e:
            last_err = e
            try: shutil.rmtree(temp_profile, ignore_errors=True)
            except Exception: pass
            if attempt <= retries:
                print(f"⚠️ SessionNotCreatedException (profile lock). Retrying {attempt}/{retries} ...")
                time.sleep(1.5)
                continue
            raise
        except Exception as e:
            last_err = e
            try: shutil.rmtree(temp_profile, ignore_errors=True)
            except Exception: pass
            raise
    if last_err:
        raise last_err

# =======================
# Helpers
# =======================
def safe_click_js(driver: webdriver.Chrome, el):
    driver.execute_script("arguments[0].click();", el)

def try_click_any_text(wait: WebDriverWait, texts: List[str], timeout: int = 5) -> bool:
    drv = wait._driver
    for t in texts:
        try:
            btn = WebDriverWait(drv, timeout).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    f'//button[.//text()[contains(., "{t}")]]'
                    f' | //div[.//text()[contains(., "{t}")]]'
                ))
            )
            safe_click_js(drv, btn)
            time.sleep(0.8)
            return True
        except Exception:
            continue
    return False

def page_ready_after_login(wait: WebDriverWait, extra_timeout: int = 10) -> bool:
    drv = wait._driver
    try:
        WebDriverWait(drv, extra_timeout).until(
            EC.any_of(
                EC.presence_of_element_located((By.XPATH, '//a[contains(@href,"/accounts/edit/")]')),
                EC.presence_of_element_located((By.XPATH, '//a[contains(@href,"/direct/inbox/")]')),
                EC.presence_of_element_located((By.XPATH, '//nav')),
                EC.presence_of_element_located((By.XPATH, '//*[contains(@aria-label,"Home") or contains(@aria-label,"Profile")]')),
            )
        )
        return True
    except TimeoutException:
        return False

def cookie_login(driver: webdriver.Chrome, wait: WebDriverWait) -> bool:
    """Use existing session cookies to bypass form login (most reliable in cloud).
       Requires INSTAGRAM_SESSIONID and DS_USER_ID env vars.
    """
    if not IG_SESSIONID or not IG_DS_USERID:
        return False

    base = "https://www.instagram.com"
    driver.get(base)
    time.sleep(1)

    # Set cookies for the domain and its subdomain (defensive)
    cookie_params = [
        {"name": "sessionid", "value": IG_SESSIONID, "domain": ".instagram.com", "path": "/"},
        {"name": "ds_user_id", "value": IG_DS_USERID, "domain": ".instagram.com", "path": "/"},
    ]
    for ck in cookie_params:
        try:
            driver.add_cookie(ck)
        except Exception as e:
            print(f"⚠️ add_cookie error: {e}")

    driver.get(base)  # refresh with cookies
    time.sleep(2)

    # Dismiss cookie banners if any
    try_click_any_text(wait, [
        "Allow essential cookies", "Only allow essential cookies",
        "Allow all cookies", "Allow all", "Accept All", "Accept all"
    ], timeout=3)

    ok = page_ready_after_login(wait, extra_timeout=8)
    print("✅ Cookie login success" if ok else "⚠️ Cookie login did not reach home UI")
    return ok

def robust_ig_login(driver: webdriver.Chrome, wait: WebDriverWait, username: str, password: str) -> bool:
    login_urls = [
        "https://www.instagram.com/accounts/login/",
        "https://m.instagram.com/accounts/login/"
    ]
    for attempt_url in login_urls:
        driver.get(attempt_url)
        print(f"🔄 Opening {attempt_url}")
        time.sleep(2)

        # Cookie banners
        try_click_any_text(wait, [
            "Allow essential cookies", "Only allow essential cookies",
            "Allow all cookies", "Allow all", "Accept All", "Accept all"
        ], timeout=4)

        # Find inputs (desktop + mobile)
        try:
            user_el = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="username"]'))
            )
            pass_el = driver.find_element(By.CSS_SELECTOR, 'input[name="password"]')
        except Exception:
            try:
                user_el = WebDriverWait(driver, 6).until(
                    EC.presence_of_element_located((
                        By.XPATH, '//input[@name="username" or @aria-label="Phone number, username, or email"]'
                    ))
                )
                pass_el = driver.find_element(By.XPATH, '//input[@name="password" or @aria-label="Password"]')
            except Exception as e:
                print(f"⚠️ Could not find login inputs on {attempt_url}: {e}")
                continue

        # Submit
        try:
            user_el.clear(); user_el.send_keys(username)
            pass_el.clear(); pass_el.send_keys(password)
            pass_el.send_keys(Keys.ENTER)
            time.sleep(2.0)

            # If still on page, try clicking submit
            try:
                submit = driver.find_element(By.XPATH, '//button[@type="submit" or .//text()[contains(., "Log in")]]')
                safe_click_js(driver, submit)
            except NoSuchElementException:
                pass

            time.sleep(3)
            # Interstitials
            try_click_any_text(wait, ["Not now", "Not Now"], timeout=3)  # save login
            try_click_any_text(wait, ["Not now", "Not Now"], timeout=3)  # notifications

            if page_ready_after_login(wait, extra_timeout=10):
                print("✅ Logged into Instagram")
                return True
            else:
                print("⚠️ Login reached no home UI; dismissing and re-checking…")
                try_click_any_text(wait, ["Not now", "Not Now"], timeout=2)
                if page_ready_after_login(wait, extra_timeout=5):
                    print("✅ Logged into Instagram (after dismiss)")
                    return True

        except Exception as e:
            print(f"⚠️ Login submit error on {attempt_url}: {e}")
            # try next URL

    return False

def get_post_date_iso(driver: webdriver.Chrome) -> Tuple[str, Optional[datetime]]:
    try:
        t = driver.find_element(By.TAG_NAME, "time")
        iso = t.get_attribute("datetime")  # e.g., 2025-10-07T13:23:11.000Z
        date_str = iso[:10] if iso else "Unknown"
        dt_obj = None
        if date_str and date_str != "Unknown":
            dt_obj = datetime.fromisoformat(date_str)
        return date_str, dt_obj
    except NoSuchElementException:
        return "Unknown", None

def collect_caption_and_comments(article_el) -> List[str]:
    texts = []
    # Caption (often h1)
    try:
        cap = article_el.find_element(By.XPATH, './/h1')
        ct = cap.text.strip()
        if ct:
            texts.append(ct)
    except NoSuchElementException:
        pass
    # Comment spans (best-effort)
    spans = article_el.find_elements(By.XPATH, './/ul//span[normalize-space()]')
    for sp in spans:
        s = sp.text.strip()
        if s:
            texts.append(s)
    # Dedup preserve order
    seen = set(); out = []
    for s in texts:
        if s not in seen:
            seen.add(s); out.append(s)
    return out

def scrape_once():
    """Run one scrape cycle. Designed to run in a background thread while Flask serves /healthz."""
    print("🚀 Scraper starting…")

    # Dates
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt   = datetime.strptime(END_DATE,   "%Y-%m-%d")

    # Browser
    driver, profile_dir = create_driver_with_retry()
    wait = WebDriverWait(driver, 15)

    # Prefer cookie login
    logged_in = False
    if IG_SESSIONID and IG_DS_USERID:
        try:
            logged_in = cookie_login(driver, wait)
        except Exception as e:
            print(f"⚠️ Cookie login error: {e}")

    # Fallback to form login
    if not logged_in:
        if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
            print("❌ No cookies and no credentials; cannot log in.")
            driver.quit()
            return
        if not robust_ig_login(driver, wait, INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD):
            print("❌ Login failed after all attempts. (2FA/checkpoint may require manual action.)")
            driver.quit()
            return

    # Go to profile
    driver.get(PROFILE_URL)
    print("✅ Profile page loaded")
    time.sleep(4)

    # Open first post
    try:
        first_post = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "article a")))
        driver.execute_script("arguments[0].scrollIntoView({behavior:'instant',block:'center'});", first_post)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", first_post)
        print("✅ Opened first post")
        time.sleep(2.5)
    except Exception as e:
        print(f"⚠️ Error opening first post: {e}")
        driver.save_screenshot("/tmp/click_error.png")
        driver.quit()
        return

    # Scrape loop
    rows = []
    post_count = 0
    stop = False

    while not stop:
        post_count += 1
        print(f"\n📸 Scraping Post {post_count}")
        post_url = driver.current_url

        date_posted, date_obj = get_post_date_iso(driver)
        if post_count > 3 and date_obj and date_obj.date() < start_dt.date():
            print(f"🛑 Older than start date ({START_DATE}). Stopping.")
            break

        # Likes (may be hidden)
        try:
            likes = driver.find_element(By.XPATH, '//section//span//*[contains(text(),"likes")]/..').text
        except NoSuchElementException:
            try:
                likes = driver.find_element(By.XPATH, '//section//button[contains(.," likes")]').text
            except NoSuchElementException:
                likes = "Hidden"

        # Caption + comments
        all_comments = []
        try:
            article = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "article")))
            all_comments = collect_caption_and_comments(article)
        except Exception:
            print("⚠️ Could not parse caption/comments for this post.")

        # Save rows
        if not all_comments:
            rows.append({"Post_Number": post_count, "URL": post_url, "Date": date_posted, "Likes": likes, "Comment": ""})
        else:
            first = True
            for c in all_comments:
                if first:
                    rows.append({"Post_Number": post_count, "URL": post_url, "Date": date_posted, "Likes": likes, "Comment": c})
                    first = False
                else:
                    rows.append({"Post_Number": "", "URL": "", "Date": "", "Likes": "", "Comment": c})

        print(f"✅ Scraped {len(all_comments)} comments")

        # Next post
        try:
            next_btn = wait.until(EC.element_to_be_clickable((
                By.XPATH,
                '//button[contains(@class,"_abl-")]//*[local-name()="svg" and @aria-label="Next"]/ancestor::button'
                ' | //button[contains(@class,"_abl-")][last()]'
            )))
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(random.uniform(2.6, 4.1))
        except TimeoutException:
            print("⚠️ Next button not found — end reached.")
            stop = True

    # Filter by date window and write CSV
    if rows:
        df = pd.DataFrame(rows)
        df["Date_filled"] = df["Date"].replace("", pd.NA).ffill()
        df["URL_filled"]  = df["URL"].replace("", pd.NA).ffill()

        def in_window(dstr: str) -> bool:
            if not dstr or dstr == "Unknown":
                return False
            try:
                d = datetime.strptime(dstr, "%Y-%m-%d").date()
            except Exception:
                return False
            return start_dt.date() <= d <= end_dt.date()

        keep_urls = set(df[df["Date_filled"].apply(in_window)]["URL_filled"].dropna().unique())
        df = df[df["URL_filled"].isin(keep_urls)].drop(columns=["Date_filled", "URL_filled"])

        out_path = OUTPUT_FILE
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ Data saved to {out_path} (Rows: {len(df)})")
    else:
        print("\n⚠️ No data scraped.")

    driver.quit()
    print("\n✅ Scraper finished.")

# =======================
# Flask app for Render $PORT
# =======================
app = Flask(__name__)

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    # Start scraper in background thread so Flask can bind to $PORT
    t = threading.Thread(target=scrape_once, daemon=True)
    t.start()

    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
