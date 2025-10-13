# app.py
import os, time, random, tempfile, uuid
from datetime import datetime
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# ================== CONFIG FROM ENV ==================
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
PROFILE_URL       = os.getenv("PROFILE_URL", "https://www.instagram.com/srija_sweetiee/")
OUTPUT_FILE       = os.getenv("OUTPUT_FILE", "/data/Srija_posts.csv")
START_DATE        = os.getenv("START_DATE", "2025-09-29")
END_DATE          = os.getenv("END_DATE", "2025-10-10")

assert INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD, "Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD env vars."

# Convert date strings early (fail fast if wrong format)
start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").date()
end_dt   = datetime.strptime(END_DATE, "%Y-%m-%d").date()

# ================== CHROME OPTIONS (Render-safe) ==================
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

# ✅ UNIQUE user-data-dir to avoid “already in use” lock
user_data_dir = os.path.join(tempfile.gettempdir(), f"chrome-user-data-{uuid.uuid4().hex}")
chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

# If Dockerfile sets CHROME_BIN/CHROMEDRIVER, Selenium will use them automatically
service = Service()
driver = webdriver.Chrome(service=service, options=chrome_options)
wait = WebDriverWait(driver, 20)

def login():
    print("🔐 Logging in…")
    driver.get("https://www.instagram.com/accounts/login/")
    time.sleep(4)
    try:
        u = wait.until(EC.presence_of_element_located((By.NAME, "username")))
        p = wait.until(EC.presence_of_element_located((By.NAME, "password")))
        u.clear(); p.clear()
        u.send_keys(INSTAGRAM_USERNAME)
        p.send_keys(INSTAGRAM_PASSWORD)
        wait.until(EC.element_to_be_clickable((By.XPATH, '//button[@type="submit"]'))).click()
        time.sleep(7)
        print("✅ Logged in.")
    except TimeoutException:
        raise SystemExit("❌ Could not locate login form (DOM changed or blocked).")

def open_first_post():
    print("🌐 Opening profile…", PROFILE_URL)
    driver.get(PROFILE_URL)
    time.sleep(5)
    # Try to click the first post tile.
    # Note: Instagram DOM changes often; this selector works for now but may need adjustments.
    candidates = [
        # grid v1
        '//a[contains(@href, "/p/") or contains(@href,"/reel/")]',
    ]
    for xp in candidates:
        try:
            elem = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            time.sleep(1.5)
            driver.execute_script("arguments[0].click();", elem)
            time.sleep(3)
            print("✅ Opened first post.")
            return
        except Exception:
            continue
    raise SystemExit("❌ Could not open first post (selector drift).")

def scrape():
    data = []
    post_count = 0

    while True:
        post_count += 1
        print(f"\n📸 Scraping Post {post_count}")

        # Current post URL
        post_url = driver.current_url

        # Date
        try:
            date_element = wait.until(EC.presence_of_element_located((By.TAG_NAME, "time")))
            date_posted = date_element.get_attribute("datetime")[:10]  # yyyy-mm-dd
            date_obj = datetime.fromisoformat(date_posted).date()
        except Exception:
            date_posted = "Unknown"
            date_obj = None

        # Stop if older than START_DATE (after we’ve moved a bit)
        if post_count > 3 and date_obj and date_obj < start_dt:
            print(f"🛑 Older than {START_DATE}. Stopping.")
            break

        # Likes (may be hidden)
        try:
            likes = driver.find_element(By.XPATH, '//section[2]//span[contains(@class,"_aamw")]').text
        except NoSuchElementException:
            likes = "Hidden"

        # Caption + Comments (best-effort; DOM often changes)
        all_comments = []
        try:
            # Caption (post owner)
            cap_candidates = [
                '//h1',                          # new layout
                '//div[@role="dialog"]//h1',     # modal layout
            ]
            caption_text = "N/A"
            for xp in cap_candidates:
                try:
                    caption_text = driver.find_element(By.XPATH, xp).text.strip()
                    break
                except Exception:
                    pass
            if caption_text and caption_text != "N/A":
                all_comments.append(caption_text)
        except Exception:
            pass

        # Save one row (comments elided for brevity)
        data.append({
            "Post_Number": post_count,
            "URL": post_url,
            "Date": date_posted,
            "Likes": likes,
            "Comment": caption_text if caption_text else ""
        })

        # Next button
        try:
            next_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//button[contains(@class,"_abl-") and @aria-label="Next"] | //div[@role="dialog"]//button[@aria-label="Next"]')
            ))
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(random.uniform(2.5, 4.5))
        except TimeoutException:
            print("⚠️ Next button not found. Stopping.")
            break

    # Save
    if data:
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        df = pd.DataFrame(data)
        df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
        print(f"\n✅ Saved {len(df)} rows to {OUTPUT_FILE}")
    else:
        print("\n⚠️ No data scraped.")

if __name__ == "__main__":
    try:
        login()
        open_first_post()
        scrape()
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    print("\n✅ Done.")
