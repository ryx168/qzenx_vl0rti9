import os
import sys
import json
import time
import random
import re
import subprocess
import pyautogui
import pyperclip
from datetime import datetime, timedelta
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Find repository root (where autogui/ exists)
script_dir = Path(__file__).parent.absolute()
# The script is in autogui/x_com/, so autogui root is the parent
autogui_root = script_dir.parent
project_root = autogui_root.parent

if str(autogui_root) not in sys.path:
    sys.path.append(str(autogui_root))

try:
    from template_finder import ScreenTemplateFinder
except ImportError:
    # Fallback
    if str(project_root) not in sys.path:
        sys.path.append(str(project_root))
    from template_finder import ScreenTemplateFinder

# Configuration
CONFIG = {
    "userDataDir": project_root / ".browser-data",
    "historyFile": project_root / "posted_story_styles.json",
    # Multiple common Chrome paths on Windows
    "chromePaths": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")
    ]
}

# Resolve Chrome path
CONFIG["chromeExe"] = "chrome" # Default to path
for path in CONFIG["chromePaths"]:
    if os.path.exists(path):
        CONFIG["chromeExe"] = path
        break

def log(msg):
    timestamp = datetime.now().isoformat()
    # Handle console encoding issues by ignoring non-mappable chars
    try:
        print(f"[{timestamp}] {msg}", flush=True)
    except UnicodeEncodeError:
        safe_msg = str(msg).encode(sys.stdout.encoding or 'ascii', 'replace').decode(sys.stdout.encoding or 'ascii')
        print(f"[{timestamp}] {safe_msg}", flush=True)

def load_history():
    if CONFIG["historyFile"].exists():
        try:
            with open(CONFIG["historyFile"], "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception as e:
            log(f"Warning: Could not parse history file: {e}")
            return []
    return []

def save_history(history):
    try:
        with open(CONFIG["historyFile"], "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        log(f"Error: Could not save history file: {e}")

def style_to_hashtag(folder_name):
    # split_images_arabic_calligraphy -> #ArabicCalligraphy
    parts = folder_name.replace("split_images_", "").split("_")
    hashtag = "#" + "".join(p.capitalize() for p in parts)
    return hashtag

def parse_story_script(file_path):
    if not os.path.exists(file_path):
        log(f"Error: Story script file not found: {file_path}")
        return []
    with open(file_path, "r", encoding="utf-8-sig") as f:
        content = f.read()
        lines = content.splitlines()
    
    scenes = []
    # Matchers for different formats
    # Format A: 1. **Title**: Text
    regex_a = r"^\d+[\.\)]\s*(?:\*\*)?(.*?)(?:\*\*)?[:：]\s*(.*)$"
    # Format B: ## Scene 1: Title
    regex_b = r"^##\s*Scene\s*\d+"
    
    current_scene_found = False
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        # Check Format A
        match_a = re.match(regex_a, line)
        if match_a:
            scenes.append(match_a.group(2).strip())
            continue
            
        # Check Format B
        if re.match(regex_b, line):
            current_scene_found = True
            continue
            
        if current_scene_found:
            # This is the text after the scene header
            if len(line) > 20: # Sanity check for content length
                scenes.append(line)
                current_scene_found = False
            continue

    return scenes

def parse_daily_metadata(file_path):
    if not os.path.exists(file_path):
        return {"theme": "", "keywords": []}
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    lines = content.split("\n")
    first_line = lines[0] if lines else ""
    
    # Extract theme from first line: 第4176天, [Theme] 2026-02-09...
    theme_match = re.search(r", (.*?) \d{4}", first_line)
    theme = theme_match.group(1) if theme_match else ""
    
    keywords = []
    # Look for locations like "City/Region" or after a Tab
    for line in lines:
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) > 1:
                loc = parts[1].split("/")[0].strip()
                if loc and len(loc) > 2 and " " not in loc:
                    keywords.append("#" + loc)
    
    # Look for topic names in English in headers
    topic_matches = re.findall(r"\((.*?)\)", content)
    for word in topic_matches:
        word = word.split(",")[0].strip()
        if word and len(word) > 3 and " " not in word:
            keywords.append("#" + re.sub(r"[^a-zA-Z]", "", word))
            
    # Common Middle East Locations/Keywords fallback
    common_locs = ["Doha", "Dubai", "Aleppo", "Damascus", "Antioch", "MiddleEast", "Qatar", "Syria", "Arabia"]
    for loc in common_locs:
        if loc.lower() in content.lower():
            keywords.append("#" + loc)
            
    return {"theme": theme, "keywords": list(set(keywords))[:15]}

def enhance_text(text, style_hashtag, daily_data=None):
    processed_text = text
    
    core_tiers = {
        "niche": ["#IslamicHistory", "#DesertCulture", "#AncientLegends", "#CulturalHeritage"],
        "art": ["#HistoryArt", "#ConceptualArt", "#Storytelling", "#HistoricalFiction"],
        "branding": ["#TheDayInHistory", "#MalikTheMerchant"]
    }
    
    # 1. Sparse keyword conversion
    critical_keywords = ["History", "Ancient", "Tradition", "Resilience", "Merchant", "Caravan"]
    for kw in critical_keywords:
        regex = re.compile(rf"\b{kw}\b", re.IGNORECASE)
        if regex.search(processed_text) and random.random() > 0.4:
            processed_text = regex.sub(f"#{kw}", processed_text, count=1)
            
    def count_tags(s):
        return s.count("#")
    
    extra_tags = [style_hashtag]
    
    if daily_data and daily_data["keywords"]:
        random_daily = daily_data["keywords"][:]
        random.shuffle(random_daily)
        random_daily = [t for t in random_daily if t not in extra_tags]
        if random_daily:
            extra_tags.append(random_daily[0])
            
    rotate_tags = core_tiers["niche"] + core_tiers["art"] + core_tiers["branding"]
    random_pool = rotate_tags[:]
    random.shuffle(random_pool)
    
    while count_tags(processed_text) + len(extra_tags) < 3 and random_pool:
        candidate = random_pool.pop()
        if candidate not in extra_tags:
            extra_tags.append(candidate)
            
    random.shuffle(extra_tags)
    tags_text = "\n\n" + " ".join(extra_tags)
    
    if (len(processed_text) + len(tags_text)) > 280:
        processed_text = processed_text[:280 - len(tags_text) - 3] + "..."
        
    return processed_text + tags_text

def launch_browser():
    log("Launching Browser...")
    chrome_path = CONFIG["chromeExe"]
    user_data_dir = str(CONFIG["userDataDir"])
    
    if not os.path.exists(user_data_dir):
        os.makedirs(user_data_dir, exist_ok=True)
    
    args = [
        chrome_path,
        "--remote-debugging-port=9222",
        f"--user-data-dir={user_data_dir}",
        "--start-maximized",
        "--new-window",
        "https://x.com/home"
    ]
    
    try:
        subprocess.Popen(args)
        log("Browser launched. Waiting for page load (10s)...")
        time.sleep(10)
        return True
    except Exception as e:
        log(f"❌ Error launching browser: {e}")
        return False

def perform_post(finder, text, image_path=None, pinned_post_url=""):
    try:
        log("Starting manual post interaction...")
        
        profile_template = str(script_dir / "home.png")
        if not finder.wait_and_click_template(profile_template, timeout=10):
            log("⚠️ Warning: Could not find home.png. Continuing...")
        else:
            time.sleep(2)

        post_btn_template = str(script_dir / "post_wait.png")
        if finder.check_template_exists(post_btn_template, verbose=True, threshold=0.98, max_mean_diff=5.0):
            while True:
                profile_template = str(script_dir / "1_text_box.png")
                if not finder.check_template_exists(profile_template, verbose=False):
                    log("⚠️ Could not find 1_text_box.png. Trying scroll up...")
                    pyautogui.moveTo(1197, 705, duration=0.5)
                    pyautogui.click()
                    time.sleep(1)  
                    pyautogui.press('pageup')  
                else:
                    break

            log("Finding text box...")
            text_box_template = str(script_dir / "1_text_box.png")
            if not finder.wait_and_click_template(text_box_template, timeout=10):
                log("Error: Could not find '1_text_box.png'. Please ensure composer is visible.")
                return False
                    
            if image_path and os.path.exists(image_path):
                log(f"Uploading image: {image_path}")
                image_btn_template = str(script_dir / "2_image_button.png")
                if finder.wait_and_click_template(image_btn_template, timeout=10):
                    time.sleep(1.5)
                    pyautogui.write(os.path.abspath(image_path))
                    time.sleep(0.5)
                    pyautogui.press('enter')
                    log("Image path submitted. Waiting for upload (5s)...")
                    time.sleep(5)
                else:
                    log("Warning: Could not find '2_image_button.png'. Skipping image upload.")

            log("Typing text...")
            finder.wait_and_click_template(text_box_template, timeout=5)
            pyautogui.write(text, interval=0.01)
            time.sleep(1)

        pyautogui.moveTo(1197, 705, duration=0.5)
        pyautogui.click()
        time.sleep(1)  
        pyautogui.press('pageup')  

        while True:
            post_btn_template = str(script_dir / "post.png")
            if finder.check_template_exists(post_btn_template, verbose=False):
                log("⚠️ Could not find post.png. Trying post..")
                if finder.wait_and_click_template(post_btn_template, timeout=3):
                    log("❌ Error: Could not find post button.")
                    break
            else:
                break
            time.sleep(3)

        time.sleep(3)
        pyautogui.press('pagedown')
        time.sleep(2)
        
        log("Finding the Reply button on the new tweet...")
        reply_template = str(script_dir / "reply.png")
        if not finder.wait_and_click_template(reply_template, timeout=15):
            log("⚠️ Could not find reply button. Trying lower...")
            pyautogui.press('pagedown')
            time.sleep(1)
            if not finder.wait_and_click_template(reply_template, timeout=10):
                log("❌ Could not find reply button.")
                return True 
                
        time.sleep(2)
        reply_text = f"{pinned_post_url}"
        pyautogui.write(reply_text, interval=0.01)
        time.sleep(1)

        reply_btn_template = str(script_dir / "post.png")
        if finder.wait_and_click_template(reply_btn_template, timeout=10):
            log("✅ post posted successfully.")
        else:
            log("⚠️ Could not find post button to submit.")
            
        return True

    except Exception as e:
        log(f"❌ Post failed: {e}")
        return False

def get_pinned_post_url(finder):
    log("--- INITIALIZATION: Fetching Pinned Post URL ---")
    for attempt in range(1, 4):
        try:
            log(f"Fetching pinned post (Attempt {attempt}/3)...")
            pyautogui.hotkey('ctrl', 'l')
            time.sleep(1)
            pyautogui.write("https://x.com/home")
            pyautogui.press('enter')
            time.sleep(3)
            while True:
                profile_template = str(script_dir / "profile_current.png")
                if not finder.check_template_exists(profile_template, verbose=False):      
                    profile_template = str(script_dir / "profile.png")
                    if not finder.wait_and_click_template(profile_template, timeout=3):
                        time.sleep(2)
                        continue
                break
            
            while True:
                share_template = str(script_dir / "share.png")
                if not finder.check_template_exists(share_template, verbose=False):
                    pyautogui.press('pagedown')
                    time.sleep(1)
                else:
                    break

            if finder.wait_and_click_template(str(script_dir / "share.png"), timeout=10):
                time.sleep(2)
                if finder.wait_and_click_template(str(script_dir / "copy_link.png"), timeout=10):
                    time.sleep(2)
                    pinned_url = pyperclip.paste().strip()
                    if "status" in pinned_url:
                        log(f"✅ Pinned Post URL captured: {pinned_url}")
                        return pinned_url
        except Exception as e:
            log(f"⚠️ Attempt {attempt} failed: {e}")
            time.sleep(2)
    return ""

def post_full_story(finder, style_folder, scenes, clips_dir, pinned_post_url, daily_data):
    style_hashtag = style_to_hashtag(style_folder)
    log(f"Starting Story Thread for style: {style_folder} ({style_hashtag})")
    scene1_path = os.path.join(clips_dir, style_folder, "scene_1.png")
    scene1_text = enhance_text(scenes[0], style_hashtag, daily_data)
    success = perform_post(finder, scene1_text, scene1_path, pinned_post_url)
    return success

def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    
    date_arg = None
    if "--date" in args:
        idx = args.index("--date")
        if idx + 1 < len(args):
            date_arg = args[idx + 1]
    if date_arg:
        today = date_arg
        log(f"Using provided date: {today}")
    else:
        now = datetime.now()
        if now.hour > 10:
            today = now.strftime("%Y-%m-%d")
        else:
            today = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        log(f"Calculated date: {today}")
       
    year_month = today.split("-")[0] + "/" + today.split("-")[1]
    project_dir = project_root / year_month / f"{today}-project"

    if not project_dir.exists():
        for m in ["03", "02"]:
            base_dir = project_root / "2026" / m
            if base_dir.exists():
                # Priority for the target date
                target_name = f"{today}-project"
                if (base_dir / target_name).exists():
                    project_dir = base_dir / target_name
                    break
                
                dirs = sorted([d for d in os.listdir(base_dir) if d.endswith("-project")], reverse=True)
                if dirs:
                    project_dir = base_dir / dirs[0]
                    if project_dir.exists():
                        break

    if not project_dir.exists():
        log("Error: No project directory found.")
        return
    
    log(f"Project Dir: {project_dir}")
    log(f"Root Dir: {project_root}")

    clips_dir = project_dir / "public" / "assets" / "clips"
    story_script_path = project_dir / "0.sources" / "story_script.md"
    if not story_script_path.exists():
        story_script_path = project_dir / "0.sources" / "story.md"

    folders_json_path = clips_dir / "folders.json"
    
    if not story_script_path.exists() or not folders_json_path.exists():
        log(f"❌ Error: Required assets missing. Checked for {story_script_path} and {folders_json_path}")
        return

    scenes = parse_story_script(story_script_path)
    if not scenes:
        log("Error: Could not parse any scenes.")
        return
    log(f"Successfully parsed {len(scenes)} scenes.")
    
    with open(folders_json_path, "r", encoding="utf-8-sig") as f:
        styles = json.load(f)

    if dry_run:
        log("--- DRY RUN ---")
        log(f"Styles: {styles}")
        log(f"Scenes: {len(scenes)}")
        return

    log("Manual Browser Session mode.")
    time.sleep(3)
    
    finder = ScreenTemplateFinder(confidence_threshold=0.8)
    daily_file_path = project_dir / "0.sources" / f"{project_dir.name.replace('-project', '')}.md"
    daily_data = parse_daily_metadata(daily_file_path)
    
    history = load_history()
    available_styles = [s for s in styles if s not in history]
    if not available_styles:
        log("🎉 All styles posted!")
        return
        
    style = random.choice(available_styles)
    pinned_url = get_pinned_post_url(finder)
    if not pinned_url:
        log(f"❌ Error: Could not get pinned post URL. Exiting.")
        return

    success = post_full_story(finder, style, scenes, str(clips_dir), pinned_url, daily_data)
    if success:
        history.append(style)
        save_history(history)
        log(f"Style added to history: {style}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"FATAL EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()
