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
  5. Waits 10 minutes before the next post
  6. If today has no unposted projects, falls back to previous days

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
import itertools
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
import zoneinfo

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--max-posts", type=int, default=None,
                    help="Maximum number of posts to make in this run (default: unlimited)")
parser.add_argument("--lookback-days", type=int, default=30,
                    help="How many previous days to look back if today has no projects (default: 30)")
args = parser.parse_args()
IS_DRY_RUN    = args.dry_run
MAX_POSTS     = args.max_posts
LOOKBACK_DAYS = args.lookback_days

POST_WAIT_SECONDS = 600  # 10 minutes between posts

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


# ── LLM Source selector ───────────────────────────────────────────────────────
def get_client():
    """Return (base_url, api_key, model) trying in order:
       1. GitHub Models  (cloud, free tier)
       2. Ollama         (local, CPU)
       3. Antigravity    (local Docker)
    """
    if GH_MODELS_KEY:
        return GH_MODELS_URL, GH_MODELS_KEY, GH_MODEL

    try:
        urllib.request.urlopen(f"{OLLAMA_BASE_URL}/models", timeout=3)
        return OLLAMA_BASE_URL, "ollama", OLLAMA_MODEL
    except Exception:
        pass

    return API_BASE_URL, API_KEY, AG_MODEL


# ── Pacific time ──────────────────────────────────────────────────────────────
def get_pacific_time():
    return datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))


def date_parts(dt: datetime):
    """Return (year_str, month_str, date_str) for a given datetime."""
    return dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%Y-%m-%d")


# ── Posted IDs ────────────────────────────────────────────────────────────────
def load_posted_ids() -> list:
    if POSTED_IDS_FILE.exists():
        return json.loads(POSTED_IDS_FILE.read_text())
    return []

def save_posted_ids(ids: list):
    if IS_DRY_RUN:
        return
    POSTED_IDS_FILE.write_text(json.dumps(ids, indent=2))


# ── Google Drive helpers ──────────────────────────────────────────────────────
def get_drive_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
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
                except Exception:
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
        print("⚠️ Google Drive not authenticated. Check GOOGLE_DRIVE_TOKEN secret.")
        return None
    return build('drive', 'v3', credentials=creds)


# ── Local Project Fallback ────────────────────────────────────────────────────
def list_projects_local(year: str, month: str, date: str):
    base_dir = Path("news") / year / month / date
    if not base_dir.exists():
        return []
    folders = []
    for d in base_dir.iterdir():
        if d.is_dir() and d.name.startswith("News-"):
            folders.append({"id": str(d), "name": d.name, "is_local": True})
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
            print(f"  ⚠️ Failed to download {fname}: {e}")


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


def list_projects_for_date(service, year: str, month: str, date: str):
    """Return project folders for a specific date from Drive, or [] if not found."""
    year_id  = find_folder(service, ROOT_FOLDER_ID, year)
    if not year_id:  return []
    month_id = find_folder(service, year_id, month)
    if not month_id: return []
    date_id  = find_folder(service, month_id, date)
    if not date_id:  return []
    q = f"'{date_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    r = service.files().list(q=q, fields='files(id,name,modifiedTime)').execute()
    return r.get('files', [])


# ── Date range iterator (today → today - lookback_days) ──────────────────────
def iter_dates(lookback_days: int):
    """Yield (year, month, date_str) from today backwards."""
    pt = get_pacific_time()
    for offset in range(lookback_days + 1):
        day = pt - timedelta(days=offset)
        yield date_parts(day)


# ── Lazy per-date project scanner ─────────────────────────────────────────────
def collect_unposted_for_date(service, posted_ids: list, year: str, month: str, date_str: str):
    """Return unposted projects for a single date, sorted newest folder first."""
    if service:
        folders = list_projects_for_date(service, year, month, date_str)
    else:
        folders = list_projects_local(year, month, date_str)

    if not folders:
        return []

    unposted = []
    for folder in folders:
        is_local = folder.get('is_local', False)
        if folder['id'] in posted_ids:
            continue
        if not check_has_mp4(service, folder['id'], is_local):
            continue
        if has_file(service, folder['id'], "x_post.json", is_local):
            continue
        folder['_date']  = date_str
        folder['_year']  = year
        folder['_month'] = month
        unposted.append(folder)

    unposted.sort(key=lambda f: f['name'], reverse=True)
    return unposted


def iter_unposted_projects(service, posted_ids: list, lookback_days: int):
    """
    Generator — scans one date at a time and yields projects immediately.
    Posting begins as soon as today's first project is found; older dates
    are only checked once all projects from the current date are exhausted.
    """
    for year, month, date_str in iter_dates(lookback_days):
        print(f"  Checking {date_str}...", end=" ", flush=True)
        projects = collect_unposted_for_date(service, posted_ids, year, month, date_str)
        print(f"{len(projects)} unposted.")
        yield from projects


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

    last_text = ""
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
            last_text = text

            if len(text) >= 200 and '#' in text:
                return text

            print(f"  ⚠️ Post too short ({len(text)} chars) or missing hashtags. Retrying ({attempt+1}/3)...")
            time.sleep(1)
        except Exception as e:
            print(f"  ⚠️ LLM request failed: {e}")
            time.sleep(2)

    return last_text


# ── Selenium X poster ─────────────────────────────────────────────────────────
def get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import WebDriverException

    try:
        opts = Options()
        opts.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        driver = webdriver.Chrome(options=opts)
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
    return driver


def set_cookies(driver, session: dict):
    driver.get("https://x.com/")
    driver.add_cookie({"name": "auth_token", "value": session["auth_token"], "domain": ".x.com"})
    driver.add_cookie({"name": "ct0",        "value": session["ct0"],        "domain": ".x.com"})


def post_tweet(driver, text: str, media_path: str = None):
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
            file_input = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[type='file'][data-testid='fileInput']")
            ))
            file_input.send_keys(media_path)
            time.sleep(5)
        except Exception as e:
            print(f"  ⚠️ Failed to attach media: {e}")

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

        try:
            btn = get_post_btn(driver)
            btn.click()
        except Exception:
            btn = get_post_btn(driver)
            driver.execute_script("arguments[0].click();", btn)
    except Exception as e:
        print(f"  ❌ Wait for button or click failed: {e}")
        raise

    try:
        toast_link = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-testid="toast"] a'))
        )
        post_link = toast_link.get_attribute("href")
        time.sleep(2)
        return post_link
    except Exception:
        return "https://x.com/home"


# ── Post one project ──────────────────────────────────────────────────────────
def process_project(service, project: dict, driver, session: dict, posted_ids: list) -> tuple:
    """
    Generate and post a single project.
    Returns (success: bool, driver, session) — driver/session may be
    initialized here on first call so the caller can reuse them.
    """
    year        = project['_year']
    month       = project['_month']
    date_str    = project['_date']
    folder_name = project['name']
    is_local    = project.get('is_local', False)

    local_dest        = Path("news") / year / month / date_str / folder_name
    original_drive_id = project['id'] if not is_local else None

    # Download from Drive if needed
    if service and not is_local:
        download_drive_folder_contents(service, project['id'], local_dest)
        project['id']       = str(local_dest)
        project['is_local'] = True
        is_local            = True

    folder_id = project['id']

    # Find mp4
    mp4_path = None
    if local_dest.exists():
        for f in local_dest.iterdir():
            if f.is_file() and f.name.lower().endswith('.mp4'):
                mp4_path = str(f.absolute())
                break

    m     = re.match(r'News-(\d{4})-(\d+)-(.+)', folder_name)
    title = m.group(3).replace('-', ' ') if m else folder_name

    print(f"\n[{date_str}] {folder_name}")

    lyrics    = read_file_content(service, folder_id, "lyrics_with_prompts.md", is_local=is_local)
    charactor = read_file_content(service, folder_id, "charactor.md",           is_local=is_local)

    post_text = generate_post(title, lyrics, charactor, date_str).strip().strip('"\'')
    if not post_text:
        print("  ❌ Could not generate post text. Skipping.")
        return False, driver, session

    print(f"  Post ({len(post_text)} chars): {post_text}")

    x_post_path = local_dest / "x_post.json"

    if IS_DRY_RUN:
        print(f"  [Dry Run] Would post and create {x_post_path}")
        return True, driver, session

    local_dest.mkdir(parents=True, exist_ok=True)

    # ── Lazy-init session + browser on first real post ────────────────────────
    if session is None:
        if not SESSION_FILE.exists():
            print(f"  ❌ No session file at {SESSION_FILE}. Cannot post.")
            return False, driver, session
        session = json.loads(SESSION_FILE.read_text())

    if driver is None:
        driver = get_driver()

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    set_cookies(driver, session)
    driver.get("https://x.com/home")
    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, '[data-testid="AppTabBar_Home_Link"]')
        ))
    except TimeoutException:
        print("  ❌ Auth failed — check cookies.")
        return False, driver, session

    try:
        post_url = post_tweet(driver, post_text, media_path=mp4_path)
        print(f"  ✅ Posted: {post_url}")

        x_post_data = {"post_text": post_text, "post_url": post_url}
        x_post_path.write_text(json.dumps(x_post_data, indent=2))

        if folder_id not in posted_ids:
            posted_ids.append(folder_id)
        save_posted_ids(posted_ids)

        if service and original_drive_id:
            from googleapiclient.http import MediaFileUpload
            file_metadata = {'name': 'x_post.json', 'parents': [original_drive_id]}
            media = MediaFileUpload(str(x_post_path), mimetype='application/json')
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()

        return True, driver, session

    except Exception as e:
        print(f"  ❌ Failed to post: {e}")
        return False, driver, session


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    PT    = get_pacific_time()
    TODAY = PT.strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"X Auto-Poster {'(DRY RUN) ' if IS_DRY_RUN else ''}— {TODAY} (PT)")
    print(f"Lookback: {LOOKBACK_DAYS} days | Max posts: {MAX_POSTS or 'unlimited'}")
    print(f"Wait between posts: {POST_WAIT_SECONDS // 60} min")
    print(f"{'='*60}\n")

    service    = get_drive_service()
    posted_ids = load_posted_ids()

    # ── Browser + session are initialized lazily on first post ───────────────
    driver  = None
    session = None

    # ── Lazy generator: scans one date at a time, yields projects immediately.
    # Posting starts as soon as today's first project is found — no full
    # upfront scan across all 30 lookback days before the first post begins.
    # ─────────────────────────────────────────────────────────────────────────
    gen = iter_unposted_projects(service, posted_ids, LOOKBACK_DAYS)

    # Peek at the very first item to detect the "nothing found" case cleanly.
    first = next(gen, None)
    if first is None:
        print(f"\nNo unposted projects found in the last {LOOKBACK_DAYS} days.")
        return

    # Re-attach the peeked item so the post loop sees it.
    projects = itertools.chain([first], gen)

    # ── Post loop ─────────────────────────────────────────────────────────────
    posted_count = 0
    total_seen   = 0

    for project in projects:
        total_seen += 1

        if MAX_POSTS and posted_count >= MAX_POSTS:
            print(f"\nReached max posts limit ({MAX_POSTS}). Stopping.")
            break

        success, driver, session = process_project(service, project, driver, session, posted_ids)
        if success:
            posted_count += 1

        # Peek at the next project to decide whether to wait.
        # If nothing follows we're done; skip the inter-post wait.
        next_project = next(projects, None)
        if next_project is None:
            break

        if MAX_POSTS and posted_count >= MAX_POSTS:
            print(f"\nReached max posts limit ({MAX_POSTS}). Stopping.")
            break

        print(f"\n  ⏳ Waiting {POST_WAIT_SECONDS // 60} minutes before next post...")
        if not IS_DRY_RUN:
            time.sleep(POST_WAIT_SECONDS)
        else:
            print("  [Dry Run] Skipping wait.")

        # Re-chain the peeked item back onto the front so the for-loop
        # picks it up on the next iteration.
        projects = itertools.chain([next_project], projects)

    print(f"\n{'='*60}")
    print(f"Done. Posted {posted_count}/{total_seen} project(s) processed.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()