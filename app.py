import os
import time
import random
import pandas as pd
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# ===================================
# CONFIGURATION
# ===================================
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME") 
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
PROFILE_URL = "https://www.instagram.com/srija_sweetiee/"
OUTPUT_FILE = "Srija_posts.csv"

# Date range filters
START_DATE = "2025-9-29"
END_DATE = "2025-10-10"

chrome_options = Options()
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument("--disable-notifications")
chrome_options.add_argument("--start-maximized")

service = Service()
driver = webdriver.Chrome(service=service, options=chrome_options)
wait = WebDriverWait(driver, 15)

# ===================================
# 1️⃣ LOGIN
# ===================================
driver.get("https://www.instagram.com/")
print("🔄 Opening Instagram...")

try:
    username_input = wait.until(EC.presence_of_element_located((By.NAME, "username")))
    password_input = driver.find_element(By.NAME, "password")
    username_input.send_keys(INSTAGRAM_USERNAME)
    password_input.send_keys(INSTAGRAM_PASSWORD)

    login_button = driver.find_element(By.XPATH, '//button[@type="submit"]')
    login_button.click()
    print("✅ Logged into Instagram")
    time.sleep(7)
except Exception as e:
    print(f"⚠️ Login error: {e}")
    driver.quit()
    exit()

# ===================================
# 2️⃣ NAVIGATE TO PROFILE
# ===================================
driver.get(PROFILE_URL)
print("✅ Profile page loaded")
time.sleep(5)

# ===================================
# 3️⃣ CLICK FIRST POST
# ===================================
first_post_xpath = '/html/body/div[1]/div/div/div[2]/div/div/div[1]/div[2]/div[1]/section/main/div/div/div[2]/div/div/div/div/div[1]/div[1]/a'

try:
    first_post = wait.until(EC.presence_of_element_located((By.XPATH, first_post_xpath)))
    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", first_post)
    time.sleep(2)
    driver.execute_script("arguments[0].click();", first_post)
    print("✅ Clicked first post")
    time.sleep(3)
except Exception as e:
    print(f"⚠️ Error clicking first post: {e}")
    driver.save_screenshot("click_error.png")
    driver.quit()
    exit()

# ===================================
# 4️⃣ SCRAPE POSTS
# ===================================
data = []

# Convert dates to datetime objects
start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
end_dt = datetime.strptime(END_DATE, "%Y-%m-%d")

post_count = 0
stop_scraping = False

while not stop_scraping:
    post_count += 1
    print(f"\n📸 Scraping Post {post_count}")
    try:
        post_url = driver.current_url

        # --- Date ---
        try:
            date_element = driver.find_element(By.XPATH, '//time')
            date_posted = date_element.get_attribute("datetime")[:10]
            date_obj = datetime.fromisoformat(date_posted).date()
        except NoSuchElementException:
            date_posted = "Unknown"
            date_obj = None

        # --- Date range check from 4th post onwards ---
        if post_count > 3 and date_obj and date_obj < start_dt.date():
            print(f"🛑 Post {post_count} is older than start date ({START_DATE}). Stopping scrape.")
            break

        # --- Likes ---
        try:
            likes = driver.find_element(By.XPATH, '//section[2]/div/div/span/a/span/span').text
        except NoSuchElementException:
            likes = "Hidden"

        # --- Caption and Comments ---
        all_comments_data = []
        try:
            comments_container = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '/html/body/div[4]/div[1]/div/div[3]/div/div/div/div/div[2]/div/article/div/div[2]/div/div/div[2]/div[1]/ul/div[3]/div/div'))
            )

            # Caption
            try:
                caption_elem = comments_container.find_element(By.XPATH, '/html/body/div[4]/div[1]/div/div[3]/div/div/div/div/div[2]/div/article/div/div[2]/div/div/div[2]/div[1]/ul/div[1]/li/div/div/div[2]/div[1]/h1')
                caption_text = caption_elem.text.strip()
                all_comments_data.append(caption_text)
                scraped_comments = set(all_comments_data)
                print(f"📝 Caption: {caption_text}")
            except NoSuchElementException:
                caption_text = "N/A"
                scraped_comments = set()

            # Load comments
            while True:
                comment_blocks = comments_container.find_elements(By.XPATH, './div[position()>=1]/ul/div/li/div/div/div[2]/div[1]/span')
                new_comment_found = False
                for comment_elem in comment_blocks:
                    try:
                        comment_text = comment_elem.text.strip()
                        if comment_text not in scraped_comments:
                            all_comments_data.append(comment_text)
                            scraped_comments.add(comment_text)
                            print(f"💬 Comment: {comment_text}")
                            new_comment_found = True
                    except Exception:
                        continue

                if comment_blocks:
                    driver.execute_script("arguments[0].scrollIntoView(true);", comment_blocks[-1])
                    time.sleep(1.5)

                try:
                    load_more_btn = comments_container.find_element(By.XPATH, './li/div/button')
                    driver.execute_script("arguments[0].click();", load_more_btn)
                    time.sleep(2)
                    new_comment_found = True
                except NoSuchElementException:
                    pass

                if not new_comment_found:
                    break

        except Exception:
            print("⚠️ Comments div not found")

        # --- Save post data ---
        first_row = True
        for comment in all_comments_data:
            if first_row:
                data.append({
                    "Post_Number": post_count,
                    "URL": post_url,
                    "Date": date_posted,
                    "Likes": likes,
                    "Comment": comment
                })
                first_row = False
            else:
                data.append({
                    "Post_Number": "",
                    "URL": "",
                    "Date": "",
                    "Likes": "",
                    "Comment": comment
                })

        print(f"✅ Scraped {len(all_comments_data)} comments for Post {post_count}")

        # --- Next post ---
        try:
            next_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//div[contains(@class, "_aaqg") and contains(@class, "_aaqh")]//button[contains(@class, "_abl-")]')
            ))
            driver.execute_script("arguments[0].click();", next_btn)
            time.sleep(random.uniform(3, 5))
        except TimeoutException:
            print("⚠️ Next button not found, stopping.")
            break

    except Exception as e:
        print(f"⚠️ Error scraping post {post_count}: {e}")
        continue

# ===================================
# 5️⃣ SAVE TO CSV
# ===================================
if data:
    df = pd.DataFrame(data)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"\n✅ Data saved to {OUTPUT_FILE} (Rows: {len(df)})")
else:
    print("\n⚠️ No data scraped.")

driver.quit()
print("\n✅ Scraping completed successfully!")
