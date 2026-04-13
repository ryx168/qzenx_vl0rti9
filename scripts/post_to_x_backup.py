#!/usr/bin/env python3
"""
post_to_x.py — Reads today's news projects from Google Drive and posts to X.

Mirrors get_todays_processed_titles() path logic from pipeline.py:
  Root → YEAR → MONTH → DATE → News-HHMM-idx-Title folders

For each unposted project it:
  1. Reads lyrics_with_prompts.md for the title/description
  2. Generates a post with the LLM (same generate_text() as pipeline.py)
  3. Posts via Selenium (reuses reply_bot.py browser logic)
  4. Saves posted IDs to posted-ids.json
  5. Waits 10 minutes, then posts the next one — loops until all done.

LLM priority:
  1. GitHub Models  (cloud, free tier)   — GH_MODELS_TOKEN + GH_MODELS_BASE_URL
  2. Ollama         (local, CPU)         — OLLAMA_BASE_URL + OLLAMA_MODEL
  3. Antigravity    (local Docker)       — API_BASE_URL + API_KEY

Usage:
  python scripts/post_to_x.py           # normal run
  python scripts/post_to_x.py --dry-run # generate posts but do NOT submit
"""

import sys
import os
import re
import json
import time
import argparse
import pickle
import io
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta, timezone
import zoneinfo

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
IS_DRY_RUN = args.dry_run

# ── Config ────────────────────────────────────────────────────────────────────
# LLM Source 1: GitHub Models (cloud, free tier)
GH_MODELS_URL  = os.getenv("GH_MODELS_BASE_URL", "https://models.inference.ai.azure.com")
GH_MODELS_KEY  = os.getenv("GH_MODELS_TOKEN")
GH_MODEL       = os.getenv("GH_MODEL",            "gpt-4o")

# LLM Source 2: Ollama (local CPU)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",    "llama3.2:3b")

# LLM Source 3: Antigravity Manager (local Docker, last resort)
API_KEY      = os.environ.get("API_KEY",      "password")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8045/v1")
AG_MODEL     = "gemini-3-flash"

ROOT_FOLDER_ID  = "1tnTb4BjVjOARRKaQjmrse4kddddj9ogj"
SESSION_FILE    = Path("/tmp/x_session.json")
POSTED_IDS_FILE = Path("posted-ids.json")
BROWSER_SESSION = str(Path(os.getcwd()) / ".browser-session")

WAIT_MINUTES    = 10   # minutes to wait between consecutive posts


# ── LLM Source selector ───────────────────────────────────────────────────────
def get_client():
    """Return (base_url, api_key, model) trying in order:
       1. GitHub Models  (cloud, free tier)
       2. Ollama         (local, CPU)
       3. Antigravity    (local Docker)
    """
    # ── 1. GitHub Models ──────────────────────────────────────────────────────
    if GH_MODELS_KEY:
        print("✅ LLM source: GitHub Models")
        return GH_MODELS_URL, GH_MODELS_KEY, GH_MODEL

    # ── 2. Ollama (local) ─────────────────────────────────────────────────────
    try:
        urllib.request.urlopen(f"{OLLAMA_BASE_URL}/models", timeout=3)
        print("✅ LLM source: Ollama (local)")
        return OLLAMA_BASE_URL, "ollama", OLLAMA_MODEL
    except Exception as e:
        print(f"⚠️ Ollama unavailable ({e}) — falling back to Antigravity...")

    # ── 3. Antigravity (last resort) ──────────────────────────────────────────
    print("✅ LLM source: Antigravity Manager")
    return API_BASE_URL, API_KEY, AG_MODEL


# ── Pacific time ──────────────────────────────────────────────────────────────
def get_pacific_time():
    return datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))

PT    = get_pacific_time()
YEAR  = PT.strftime("%Y")
MONTH = PT.strftime("%m")
DATE  = PT.strftime("%Y-%m-%d")

# ── Posted IDs ────────────────────────────────────────────────────────────────
def load_posted_ids() -> list:
    if POSTED_IDS_FILE.exists():
        return json.loads(POSTED_IDS_FILE.read_text())
    return []

def save_posted_ids(ids: list):
    if IS_DRY_RUN:
        print("[Dry Run] Skipping save of posted-ids.json")
        return
    POSTED_IDS_FILE.write_text(json.dumps(ids, indent=2))
    print(f"Saved posted IDs to {POSTED_IDS_FILE}")

# ── Google Drive helpers ──────────────────────────────────────────────────────
def get_drive_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("pip install google-api-python-client google-auth-oauthlib")
        return None

    FALLBACK_SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
    creds = None

    for path in ['token.json', os.path.expanduser('~/.api_tools/token.json')]:
        if not os.path.exists(path):
            continue
        try:
            content = open(path, 'rb').read()
            pkl = None
            if content.startswith(b'\x80'):
                pkl = pickle.loads(content)
            else:
                try:
                    import base64
                    decoded = base64.b64decode(content)
                    if decoded.startswith(b'\x80'):
                        pkl = pickle.loads(decoded)
                except:
                    pass

            if pkl:
                raw_scopes = getattr(pkl, '_scopes', getattr(pkl, 'scopes', None))
                scopes = list(raw_scopes) if raw_scopes else FALLBACK_SCOPES
                d = {
                    "token":         getattr(pkl, 'token', None),
                    "refresh_token": getattr(pkl, '_refresh_token', getattr(pkl, 'refresh_token', None)),
                    "token_uri":     getattr(pkl, '_token_uri', 'https://oauth2.googleapis.com/token'),
                    "client_id":     getattr(pkl, '_client_id', None),
                    "client_secret": getattr(pkl, '_client_secret', None),
                    "scopes":        scopes,
                }
                with open(path, 'w') as f:
                    json.dump(d, f, indent=2)
                creds = Credentials.from_authorized_user_info(d, scopes)
            else:
                d = json.loads(content)
                scopes = d.get('scopes', FALLBACK_SCOPES)
                if isinstance(scopes, str):
                    scopes = scopes.split()
                creds = Credentials.from_authorized_user_info(d, scopes)

            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                open(path, 'w').write(creds.to_json())
        except Exception as e:
            print(f"Auth error: {e}")
        break

    if not creds or not creds.valid:
        print("⚠️ Google Drive not authenticated.")
        print("   Check that GOOGLE_DRIVE_TOKEN secret is set.")
        return None
    return build('drive', 'v3', credentials=creds)


# ── Local Project Fallback ────────────────────────────────────────────────────
def list_today_projects_local():
    base_dir = Path("news") / YEAR / MONTH / DATE
    if not base_dir.exists():
        print(f"ℹ️ Local path {base_dir} does not exist.")
        return []
    folders = []
    for d in base_dir.iterdir():
        if d.is_dir() and d.name.startswith("News-"):
            folders.append({"id": str(d), "name": d.name, "is_local": True})
    print(f"Found {len(folders)} local project(s) for {DATE}.")
    return folders


def read_file_content(service, folder_id, filename, is_local=False) -> str:
    if is_local:
        path = Path(folder_id) / filename
        if path.exists():
            return path.read_text(encoding='utf-8', errors='replace')
        return ""
    from googleapiclient.http import MediaIoBaseDownload
    q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    r = service.files().list(q=q, fields='files(id)').execute()
    files = r.get('files', [])
    if not files:
        return ""
    fid = files[0]['id']
    req = service.files().get_media(fileId=fid)
    buf = io.BytesIO()
    dl  = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue().decode('utf-8', errors='replace')


def download_drive_folder_contents(service, folder_id, local_dir: Path):
    if not local_dir.exists():
        local_dir.mkdir(parents=True, exist_ok=True)
    from googleapiclient.http import MediaIoBaseDownload
    q = f"'{folder_id}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=q, fields='files(id,name)').execute()
    files = results.get('files', [])
    if not files:
        return
    print(f"    📥 Syncing {len(files)} file(s) to {local_dir}...")
    for f in files:
        fid   = f['id']
        fname = f['name']
        fpath = local_dir / fname
        if fpath.exists() and fpath.stat().st_size > 0:
            continue
        req = service.files().get_media(fileId=fid)
        buf = io.BytesIO()
        dl  = MediaIoBaseDownload(buf, req)
        done = False
        try:
            while not done:
                _, done = dl.next_chunk()
            fpath.write_bytes(buf.getvalue())
        except Exception as e:
            print(f"      ⚠️ Failed to download {fname}: {e}")


def has_file(service, folder_id, filename, is_local=False):
    if is_local:
        return (Path(folder_id) / filename).exists()
    q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    r = service.files().list(q=q, fields='files(id)').execute()
    return len(r.get('files', [])) > 0


def find_folder(service, parent_id, name):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    r = service.files().list(q=q, fields='files(id,name)').execute()
    files = r.get('files', [])
    return files[0]['id'] if files else None


def check_has_mp4(service, folder_id, is_local=False):
    if is_local:
        return any(f.name.lower().endswith('.mp4') for f in Path(folder_id).iterdir() if f.is_file())
    q = f"mimeType='video/mp4' and '{folder_id}' in parents and trashed=false"
    r = service.files().list(q=q, fields='files(id)').execute()
    if len(r.get('files', [])) > 0:
        return True
    q_name = f"name contains '.mp4' and '{folder_id}' in parents and trashed=false"
    r_name = service.files().list(q=q_name, fields='files(id)').execute()
    return len(r_name.get('files', [])) > 0


def list_today_projects(service):
    print(f"🔍 Drive path: {ROOT_FOLDER_ID} → {YEAR} → {MONTH} → {DATE}")
    year_id  = find_folder(service, ROOT_FOLDER_ID, YEAR)
    if not year_id:  return []
    month_id = find_folder(service, year_id, MONTH)
    if not month_id: return []
    date_id  = find_folder(service, month_id, DATE)
    if not date_id:
        print(f"ℹ️ No folder for {DATE} yet.")
        return []
    q = f"'{date_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    r = service.files().list(q=q, fields='files(id,name,modifiedTime)').execute()
    folders = r.get('files', [])
    print(f"Found {len(folders)} project(s) for {DATE}.")
    return folders


# ── LLM post generation ───────────────────────────────────────────────────────
def generate_post(title: str, lyrics: str, charactor: str, date: str) -> str:
    base_url, api_key, model_name = get_client()
    url     = f"{base_url}/chat/completions"
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    context = f"Title: {title}\n"
    if charactor: context += f"Character: {charactor}\n"
    if lyrics:    context += f"Story Details: {lyrics[:1500]}\n"

    for attempt in range(3):
        rules_prefix = "" if attempt == 0 else "IMPORTANT: PREVIOUS ATTEMPT WAS TOO SHORT. PLEASE EXPAND. "

        prompt = f"""Task: Write a VIRAL X (Twitter) post for this story.

Rules:
- {rules_prefix}MUST be between 240 and 270 characters long.
- MUST include exactly 2-3 relevant hashtags at the end.
- MUST use 3-5 dramatic emojis.
- Start with a gripping hook.
- Return ONLY the post text, no other commentary.

Context:
{context}

Post:"""
        print(f"\n{'─'*50}")
        print(f"[LLM PROMPT — attempt {attempt+1}/3] ({len(prompt)} chars)")
        print(prompt)
        print(f"{'─'*50}\n")

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You are a viral social media manager. You strictly follow character count rules."},
                {"role": "user",   "content": prompt}
            ],
            "temperature": 0.7
        }

        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(), headers=headers, method='POST'
            )
            resp = urllib.request.urlopen(req, timeout=60)
            data = json.loads(resp.read().decode())
            text = data['choices'][0]['message']['content'].strip().strip('"\'')
            print(f"[LLM RESPONSE] ({len(text)} chars)\n{text}\n")

            has_hashtags   = '#' in text
            is_long_enough = len(text) >= 200

            if is_long_enough and has_hashtags:
                return text

            print(f"  ⚠️ Post too short ({len(text)} chars) or missing hashtags. Retrying (attempt {attempt+1}/3)...")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠️ LLM request failed: {e}")
            time.sleep(2)

    return text if 'text' in locals() else ""


# ── Selenium X poster ─────────────────────────────────────────────────────────
def get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import WebDriverException

    try:
        opts = Options()
        opts.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        driver = webdriver.Chrome(options=opts)
        print("Connected to existing Chrome on port 9222.")
        return driver
    except WebDriverException:
        pass

    opts = Options()
    opts.binary_location = "/usr/bin/google-chrome"
    for arg in [
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-session-crashed-bubble", "--no-first-run",
        "--no-default-browser-check", "--disable-infobars",
        "--window-size=2000,1550", "--window-position=0,0",
        f"--user-data-dir={BROWSER_SESSION}",
        "--remote-debugging-port=9222",
    ]:
        opts.add_argument(arg)
    if not os.environ.get("DISPLAY"):
        opts.add_argument("--headless=new")
    driver = webdriver.Chrome(options=opts)
    print("Launched new Chrome instance.")
    return driver


def set_cookies(driver, session: dict):
    driver.get("https://x.com/")
    driver.add_cookie({"name": "auth_token", "value": session["auth_token"], "domain": ".x.com"})
    driver.add_cookie({"name": "ct0",        "value": session["ct0"],        "domain": ".x.com"})


def post_tweet(driver, text: str, media_path: str = None) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    wait = WebDriverWait(driver, 20)
    driver.get("https://x.com/home")

    compose = wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, '[data-testid="SideNav_NewTweet_Button"], [data-testid="tweetTextarea_0"]')
    ))
    compose.click()
    time.sleep(1)

    textarea = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, '[data-testid="tweetTextarea_0"]')
    ))
    textarea.click()

    driver.execute_script("""
        const text = arguments[0];
        const dataTransfer = new DataTransfer();
        dataTransfer.setData('text/plain', text);
        const event = new ClipboardEvent('paste', {
            clipboardData: dataTransfer,
            bubbles: true
        });
        arguments[1].dispatchEvent(event);
    """, text, textarea)
    time.sleep(1)

    if media_path:
        try:
            print(f"  📎 Attaching media: {os.path.basename(media_path)}")
            file_input = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='file'][data-testid='fileInput']")
            ))
            file_input.send_keys(media_path)
            time.sleep(5)
        except Exception as e:
            print(f"  ⚠️ Failed to attach media: {e}")

    print("  ⏳ Waiting for media to process...")
    try:
        def get_post_btn(d):
            for b in d.find_elements(By.CSS_SELECTOR, '[data-testid="tweetButton"], [data-testid="tweetButtonInline"]'):
                if b.is_displayed():
                    return b
            return None

        long_wait = WebDriverWait(driver, 120)
        long_wait.until(
            lambda d: get_post_btn(d) is not None
            and get_post_btn(d).is_enabled()
            and get_post_btn(d).get_attribute("aria-disabled") != "true"
        )

        print("  🚀 Clicking post button...")
        try:
            btn = get_post_btn(driver)
            btn.click()
        except:
            btn = get_post_btn(driver)
            driver.execute_script("arguments[0].click();", btn)
    except Exception as e:
        print(f"  ❌ Wait for button or click failed: {e}")
        raise e

    try:
        print("  ⏳ Waiting for success toast...")
        toast_link = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-testid="toast"] a'))
        )
        post_link = toast_link.get_attribute("href")
        print(f"  👉 Post URL: {post_link}")
        time.sleep(2)
        return post_link
    except Exception as e:
        print(f"  ⚠️ Could not grab post URL from toast popup.")
        return "https://x.com/home"


# ── Countdown helper ──────────────────────────────────────────────────────────
def wait_with_countdown(minutes: int, next_name: str = ""):
    total_secs = minutes * 60
    print(f"\n⏳ Waiting {minutes} minutes before next post{f' ({next_name})' if next_name else ''}...")
    for remaining in range(total_secs, 0, -15):
        mins, secs = divmod(remaining, 60)
        print(f"   ⏱  {mins:02d}:{secs:02d} remaining...", end='\r', flush=True)
        time.sleep(15)
    print()  # newline after countdown clears


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"X Auto-Poster {'(DRY RUN) ' if IS_DRY_RUN else ''}— {DATE} (PT)")
    print(f"LLM priority: GitHub Models → Ollama → Antigravity")
    print(f"{'='*60}\n")

    if not SESSION_FILE.exists():
        print(f"❌ No session file at {SESSION_FILE}. Exiting.")
        sys.exit(1)
    session = json.loads(SESSION_FILE.read_text())
    print(f"Session loaded for @{session.get('username','?')}")

    service = get_drive_service()
    if not service:
        print("⚠️ Proceeding with local project fallback...")
        projects = list_today_projects_local()
    else:
        projects = list_today_projects(service)

    if not projects:
        print("No projects found today (checked Drive and Local).")
        return

    projects.sort(key=lambda f: f['name'], reverse=True)

    # ── Build the posting queue (unposted projects with an mp4) ───────────────
    queue = []
    for folder in projects:
        folder_is_local = folder.get('is_local', False)
        if not check_has_mp4(service, folder['id'], folder_is_local):
            print(f"Skipping {folder['name']} — no .mp4 found.")
            continue
        if has_file(service, folder['id'], "x_post.json", folder_is_local):
            print(f"Skipping {folder['name']} — already posted (x_post.json exists).")
            continue
        queue.append(folder)

    if not queue:
        print("✅ All today's projects already have x_post.json.")
        return

    print(f"\n📋 {len(queue)} project(s) queued to post (10-min gap between each).\n")

    # ── Init browser once; reuse across all posts ─────────────────────────────
    driver = None
    if not IS_DRY_RUN:
        driver = get_driver()
        set_cookies(driver, session)

        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException

        driver.get("https://x.com/home")
        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, '[data-testid="AppTabBar_Home_Link"]')
            ))
            print("  ✅ Authenticated on X.\n")
        except TimeoutException:
            print("  ❌ Auth failed — check cookies.")
            sys.exit(1)

    # ── Post loop ─────────────────────────────────────────────────────────────
    posted_count = 0
    for idx, target_project in enumerate(queue):
        print(f"\n{'='*60}")
        print(f"🎯 [{idx+1}/{len(queue)}] {target_project['name']}")
        print(f"{'='*60}")

        local_dest        = Path("news") / YEAR / MONTH / DATE / target_project['name']
        original_drive_id = target_project['id'] if not target_project.get('is_local') else None

        # Download from Drive if needed
        if service and not target_project.get('is_local'):
            print(f"🔄 Downloading folder contents...")
            download_drive_folder_contents(service, target_project['id'], local_dest)
            target_project['id']       = str(local_dest)
            target_project['is_local'] = True

        # Find the mp4
        mp4_path = None
        if local_dest.exists():
            for f in local_dest.iterdir():
                if f.is_file() and f.name.lower().endswith('.mp4'):
                    mp4_path = str(f.absolute())
                    break

        folder_id   = target_project['id']
        folder_name = target_project['name']
        is_local    = target_project.get('is_local', False)

        m     = re.match(r'News-(\d{4})-(\d+)-(.+)', folder_name)
        title = m.group(3).replace('-', ' ') if m else folder_name

        # Read source files
        lyrics    = read_file_content(service, folder_id, "lyrics_with_prompts.md", is_local=is_local)
        charactor = read_file_content(service, folder_id, "charactor.md",           is_local=is_local)

        # Generate post text
        post_text = generate_post(title, lyrics, charactor, DATE).strip().strip('"\'')
        if not post_text:
            print("  ❌ Could not generate post text. Skipping.")
            continue

        print(f"  Title: {title}")
        print(f"  Post:  {post_text}")
        print(f"  Chars: {len(post_text)}/280")

        # Save x_post.json draft
        x_post_path = local_dest / "x_post.json"
        if IS_DRY_RUN:
            print(f"  [Dry Run] Would write {x_post_path}")
        else:
            local_dest.mkdir(parents=True, exist_ok=True)
            x_post_path.write_text(json.dumps({"post_text": post_text}, indent=2))
            print(f"  ✅ Saved draft to {x_post_path}")

        # Post to X
        if not IS_DRY_RUN and driver:
            try:
                post_url = post_tweet(driver, post_text, media_path=mp4_path)
                if post_url:
                    print(f"  ✅ Posted!")
                    posted_count += 1

                    # Update posted-ids.json
                    posted_ids = load_posted_ids()
                    if folder_id not in posted_ids:
                        posted_ids.append(folder_id)
                        save_posted_ids(posted_ids)

                    # Update x_post.json with URL
                    x_post_path.write_text(json.dumps({"post_text": post_text, "post_url": post_url}, indent=2))

                    # Upload x_post.json back to Drive
                    if service and original_drive_id:
                        print("  ☁️ Uploading x_post.json back to Google Drive...")
                        from googleapiclient.http import MediaFileUpload
                        file_metadata = {'name': 'x_post.json', 'parents': [original_drive_id]}
                        media = MediaFileUpload(str(x_post_path), mimetype='application/json')
                        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                        print("  ☁️ Drive upload complete.")

            except Exception as e:
                print(f"  ❌ Failed to post: {e}")
        elif IS_DRY_RUN:
            posted_count += 1  # count dry-run "posts" too

        # ── Wait between posts (skip after the last one) ───────────────────
        if idx < len(queue) - 1:
            next_name = queue[idx + 1]['name']
            wait_with_countdown(WAIT_MINUTES, next_name)

    print(f"\n{'='*60}")
    print(f"✅ Done. {'(Dry Run) ' if IS_DRY_RUN else ''}Posted {posted_count}/{len(queue)} project(s) for {DATE}.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()