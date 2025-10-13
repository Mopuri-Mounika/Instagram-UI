# app.py — Render-friendly Selenium setup + your scraper (with unique Chrome profile)

import os
import time
import random
import shutil
import atexit
import tempfile
from datetime import datetime

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, TimeoutException, SessionNotCreatedException

# ===================================
# CONFIGURATION
# ===================================
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME") or "adiadiadi1044"
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD") or "Heybro@"
PROFILE_URL = os.getenv("PROFILE_URL") or "https://www.instagram.com/srija_sweetiee/"
OUTPUT_FILE = os.getenv("OUTPUT_FILE") or "Srija_posts.csv"

# Date range filters (YYYY-M-D or YYYY-MM-DD)
START_DATE = os.getenv("START_DATE") or "2025-9-29"
END_DATE   = os.getenv("END_DATE")   or "2025-10-10"

# ===================================
# Chrome / Selenium bootstrap (Render-safe)
# ===================================

def make_chrome_options(user_data_dir: str) -> Options:
    opts = Options()

    # Cloud/container friendly flags
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--start-maximized")
    opts.add_argument("--mute-audio")

    # Use a UNIQUE user data dir to avoid "already in use" locks
    opts.add_argument(f"--user-data-dir={user_data_dir}")
    # Also isolate other storage paths inside the same temp dir
    opts.add_argument(f"--data-path={os.path.join(user_data_dir,'data')}")
    opts.add_argument(f"--disk-cache-dir={os.path.join(user_data_dir,'cache')}")
    opts.add_argument(f"--homedir={user_data_dir}")

    # If your Render service sets CHROME_PATH / CHROMEDRIVER_PATH, respect them
    chrome_bin = os.getenv("CHROME_PATH")
    if chrome_bin:
        opts.binary_location = chrome_bin

    # Prefer English content for more predictable selectors
    opts.add_argument("--lang=en-US")

    return opts

def create_driver_with_retry(retries: int = 2):
    """Create a Chrome driver with a fresh temp profile; retry on SessionNotCreated."""
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
            service = Service()  # Selenium Manager will fetch chromedriver
            driver = webdriver.Chrome(service=service, options=options)
            return driver, temp_profile
        except SessionNotCreatedException as e:
            last_err = e
            # Clean temp dir and retry with a new one
            try:
                shutil.rmtree(temp_profile, ignore_errors=True)
            except Exception:
                pass
            if attempt <= retries:
                print(f"⚠️ SessionNotCreatedException (profile lock). Retrying ({attempt}/{retries})...")
                time.sleep(1.5)
                continue
            else:
                raise
        except Exception as e:
            last_err = e
            # Other init errors (e.g., no Chrome in container)
            try:
                shutil.rmtree(temp_profile, ignore_errors=True)
            except Exception:
                pass
            raise
    # Shouldn’t reach here
    if last_err:
        raise last_err

# ===================================
# MAIN
# ===================================
if __name__ == "__main__":
    # Prepare date window
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
    end_dt   = datetime.strptime(END_DATE, "%Y-%m-%d")

    # Spin up browser
    driver, profile_dir = create_driver_with_retry()
    wait = WebDriverWait(driver, 15)

    # 1) LOGIN
    driver.get("https://www.instagram.com/")
    print("🔄 Opening Instagram...")

    try:
        username_input = wait.until(EC.presence_of_element_located((By.NAME, "username")))
        password_input = driver.find_element(By.NAME, "password")
        username_input.clear()
        password_input.clear()
        username_input.send_keys(INSTAGRAM_USERNAME)
        password_input.send_keys(INSTAGRAM_PASSWORD)

        login_button = driver.find_element(By.XPATH, '//button[@type="submit"]')
        login_button.click()
        print("✅ Submitted login form")

        # Wait for either the home feed or profile avatar to appear
        # (IG DOM changes often; this is a simple but effective readiness check)
        try:
            wait.until(
                EC.any_of(
                    EC.presence_of_element_located((By.XPATH, '//a[contains(@href, "/accounts/edit/")]')),
                    EC.presence_of_element_located((By.XPATH, '//img[contains(@alt, "Profile") or contains(@alt, "profile")]')),
                    EC.presence_of_element_located((By.XPATH, '//nav'))
                )
            )
        except TimeoutException:
            print("⚠️ Login may have 2FA or checkpoint. Proceeding to profile anyway.")
        time.sleep(3)
    except Exception as e:
        print(f"⚠️ Login error: {e}")
        driver.quit()
        raise SystemExit(1)

    # 2) NAVIGATE TO PROFILE
    driver.get(PROFILE_URL)
    print("✅ Profile page loaded")
    time.sleep(4)

    # 3) OPEN FIRST POST (use a slightly more flexible selector than a brittle absolute XPath)
    # Try common layout: first grid anchor under the <article> (posts grid)
    first_post = None
    try:
        # Many profile pages render posts inside an <article> tag; find first link
        first_post = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "article a"))
        )
        driver.execute_script("arguments[0].scrollIntoView({behavior:'instant',block:'center'});", first_post)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", first_post)
        print("✅ Clicked first post")
        time.sleep(2.5)
    except Exception as e:
        print(f"⚠️ Error clicking first post: {e}")
        driver.save_screenshot("/tmp/click_error.png")
        driver.quit()
        raise SystemExit(1)

    # 4) SCRAPE POSTS
    rows = []
    post_count = 0

    def get_post_date_iso() -> tuple[str, datetime | None]:
        try:
            t = driver.find_element(By.TAG_NAME, "time")
            # time[datetime] is ISO like "2025-10-07T13:23:11.000Z"
            iso = t.get_attribute("datetime")
            date_str = iso[:10] if iso else "Unknown"
            dt_obj = None
            if date_str and date_str != "Unknown":
                dt_obj = datetime.fromisoformat(date_str)
            return date_str, dt_obj
        except NoSuchElementException:
            return "Unknown", None

    stop = False
    while not stop:
        post_count += 1
        print(f"\n📸 Scraping Post {post_count}")

        post_url = driver.current_url

        # Date
        date_posted, date_obj = get_post_date_iso()

        # Early stop if older than start window after first few posts (speeds up)
        if post_count > 3 and date_obj and date_obj.date() < start_dt.date():
            print(f"🛑 Post {post_count} is older than start date ({START_DATE}). Stopping scrape.")
            break

        # Likes (often hidden for some accounts)
        try:
            # This often changes; attempt a generic likes counter
            likes = driver.find_element(By.XPATH, '//section//span//*[contains(text(),"likes")]/..').text
        except NoSuchElementException:
            try:
                likes = driver.find_element(By.XPATH, '//section//button[contains(.," likes")]').text
            except NoSuchElementException:
                likes = "Hidden"

        # Caption + comments (best-effort; IG DOM changes frequently)
        all_comments = []
        try:
            # Try to locate the main article area that contains caption/comments
            article = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "article")))
            # Caption h1 (if present)
            try:
                caption_elem = article.find_element(By.XPATH, './/h1')
                cap_text = caption_elem.text.strip()
                if cap_text:
                    all_comments.append(cap_text)
            except NoSuchElementException:
                pass

            # Comment spans
            # This XPath tries to grab comment text blocks commonly used
            comment_spans = article.find_elements(By.XPATH, './/ul//span[normalize-space()]')
            for sp in comment_spans:
                t = sp.text.strip()
                if t:
                    all_comments.append(t)

            # Deduplicate while preserving order
            seen = set()
            deduped = []
            for c in all_comments:
                if c not in seen:
                    seen.add(c)
                    deduped.append(c)
            all_comments = deduped

        except Exception:
            print("⚠️ Could not parse caption/comments on this post.")

        # Save rows (first row has metadata, next rows only comment text)
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

        print(f"✅ Scraped {len(all_comments)} comments for Post {post_count}")

        # Move next
        try:
            # Next arrow button is usually a button inside overlay; look for generic next button class
            next_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//button[contains(@class,"_abl-")]//div/*[local-name()="svg" and @aria-label="Next"] | //button[contains(@class,"_abl-")][last()]')
            ))
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(random.uniform(2.8, 4.2))
        except TimeoutException:
            print("⚠️ Next button not found, stopping.")
            stop = True

    # Filter by date window (keep rows whose first (metadata) row is within range)
    if rows:
        df = pd.DataFrame(rows)
        # Propagate metadata down grouped blocks to filter cleanly
        df["Date_filled"] = df["Date"].replace("", pd.NA).ffill()
        df["URL_filled"] = df["URL"].replace("", pd.NA).ffill()

        def in_window(dstr: str) -> bool:
            if not dstr or dstr == "Unknown":
                return False
            try:
                d = datetime.strptime(dstr, "%Y-%m-%d")
            except Exception:
                return False
            return start_dt.date() <= d.date() <= end_dt.date()

        keep_urls = set(df[df["Date_filled"].apply(in_window)]["URL_filled"].dropna().unique())
        df = df[df["URL_filled"].isin(keep_urls)].drop(columns=["Date_filled","URL_filled"])

        # Save
        out_path = OUTPUT_FILE
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ Data saved to {out_path} (Rows: {len(df)})")
    else:
        print("\n⚠️ No data scraped.")

    driver.quit()
    # temp profile gets cleaned up by atexit
    print("\n✅ Scraping completed successfully!")
