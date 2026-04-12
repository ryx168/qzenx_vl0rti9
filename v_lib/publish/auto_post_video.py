import os
import sys
import json
import time
import random
import re
import subprocess
import pyautogui
from datetime import datetime, timedelta
from pathlib import Path

# Find repository root (where autogui/ exists)
script_path = Path(__file__).absolute()
root_dir = script_path.parent
while root_dir.parent != root_dir:
    if (root_dir / "autogui").exists() and (root_dir / "2026").exists():
        break
    root_dir = root_dir.parent

autogui_dir = root_dir / "autogui"
if str(autogui_dir) not in sys.path:
    sys.path.append(str(autogui_dir))

from template_finder import ScreenTemplateFinder

# Configuration
CONFIG = {
    "historyFile": root_dir / "posted_story_styles.json",
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
    print(f"[{timestamp}] {msg}")

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
        lines = f.read().splitlines()
    
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

    # deduplicate or clean if needed, but usually just return
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

def parse_x_posts(file_path):
    if not os.path.exists(file_path):
        return ""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    final_text = ""
    # Extract Variant A or first block of text
    match = re.search(r"### Variant A:.*?\n(.*?)\n---", content, re.DOTALL)
    if match:
        # Clean up character count and other metadata
        text = match.group(1).strip()
        text = re.sub(r"\*\*Character Count\*\*.*", "", text).strip()
        final_text = text
    else:
        # Fallback to simple regex if Variant A not found as expected
        parts = content.split("---")
        if len(parts) > 1:
            text = parts[1].strip()
            # Remove headers like "### Variant A..."
            text = re.sub(r"^###.*?\n", "", text).strip()
            text = re.sub(r"\*\*Character Count\*\*.*", "", text).strip()
            final_text = text
        
    if len(final_text) > 280:
        log(f"⚠️ Warning: Post text too long ({len(final_text)} chars). Truncating...")
        final_text = final_text[:277] + "..."
        
    return final_text

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
    """
    Launches Chrome.
    """
    log("Launching Browser...")
    chrome_path = CONFIG["chromeExe"]
    
    args = [
        chrome_path,
        "--start-maximized",
        "--new-window",
        "https://x.com/home"
    ]
    
    try:
        # Launch browser as a separate process
        subprocess.Popen(args)
        log("Browser launched. Waiting for page load (10s)...")
        time.sleep(10)
        return True
    except Exception as e:
        log(f"❌ Error launching browser: {e}")
        return False

def perform_post(finder, text, image_path=None, is_reply=False):
    """
    Posts a single tweet or reply using GUI automation.
    """
    try:
        log("Starting manual post interaction...")
        
        # 1. Focus and Click Text Box
        log("Finding text box...")
        text_box_template = str(root_dir / "autogui" / "x_com" / "1_text_box.png")
        if not finder.wait_and_click_template(text_box_template, timeout=10):
            log("Error: Could not find '1_text_box.png'. Please ensure composer is visible.")
            return False
             
        # 2. Handle Image Upload First (opens separate dialog)
        if image_path and os.path.exists(image_path):
            log(f"Uploading image: {image_path}")
            image_btn_template = str(root_dir / "autogui" / "x_com" / "2_image_button.png")
            if finder.wait_and_click_template(image_btn_template, timeout=10):
                time.sleep(1.5)
                # Type image path in file dialog
                pyautogui.write(os.path.abspath(image_path))
                time.sleep(0.5)
                pyautogui.press('enter')
                log("Media path submitted. Waiting for upload (15s)...")
                time.sleep(15)
            else:
                log("Warning: Could not find '2_image_button.png'. Skipping media upload.")

        # 3. Input Text (after image upload window is closed)
        log("Typing text...")
        # Refocus text box just in case
        finder.wait_and_click_template(text_box_template, timeout=5)
        pyautogui.write(text, interval=0.01)
        time.sleep(1)

        # 4. Final Confirmation
        print("\n" + "="*30)
        print(f"PREVIEW: {text[:100]}...")
        if image_path: print(f"IMAGE: {os.path.basename(image_path)}")
        print("="*30)
        print("Press ENTER in this console to CLICK POST, or Ctrl+C to cancel.")
        input()

        # 5. Click Post Button
        # User referenced 3_post_button.png, but only post.png exists
        post_btn_template = str(root_dir / "autogui" / "x_com" / "post.png")
        # Try finding the post button
        success = finder.wait_and_click_template(post_btn_template, timeout=10)
        
        if success:
            log("Click executed. Waiting for UI update...")
            time.sleep(3)
            # Scroll down check as requested
            pyautogui.press('pagedown')
            time.sleep(1)
            
            # Check if button is still there (Retry Logic)
            if finder.check_template_exists(post_btn_template, verbose=False):
                log("⚠️ Button still visible. Performing one retry...")
                finder.wait_and_click_template(post_btn_template)
                time.sleep(2)
            return True
        else:
            log("❌ Error: Could not find Post button template.")
            return False

    except Exception as e:
        log(f"❌ Post failed: {e}")
        return False

def post_full_story(finder, style_folder, scenes, clips_dir, pinned_post_url, daily_data):
    style_hashtag = style_to_hashtag(style_folder)
    log(f"Starting Story Thread for style: {style_folder} ({style_hashtag})")

    # 1. Post Scene 1
    scene1_path = os.path.join(clips_dir, style_folder, "scene_1.png")
    scene1_text = enhance_text(scenes[0], style_hashtag, daily_data)
    
    log("Please navigate to Home manually if not already there.")
    success = perform_post(finder, scene1_text, scene1_path)
    if not success: return False
    log("✅ Scene 1 posted.")

    # In Python, we don't have the page navigation as easily as Playwright 
    # without Selenium/Playwright-python. 
    # We rely on templates and user to be in the right place, 
    # or we can use keyboard shortcuts.
    
    # 2. Reply with Pinned Link & 3. Scenes 2-9
    # This requires clicking 'Reply' on the tweet we just posted.
    # Without a 'reply.png' template, this is hard to automate reliably.
    
    log("⚠️ Automation of replies requires a 'reply.png' template.")
    log("Please provide more templates in autogui/x_com/ for full thread automation.")
    
    return True

def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    
    # Project Discovery
    date_arg = None
    if "--date" in args:
        idx = args.index("--date")
        if idx + 1 < len(args):
            date_arg = args[idx + 1]

    if date_arg:
        today = date_arg
        today_dt = datetime.strptime(today, "%Y-%m-%d")
        log(f"Using provided date: {today}")
    else:
        now = datetime.now()
        if now.hour > 6:
            today_dt = now + timedelta(days=1)
        else:
            # Change to last day  
            today_dt = now - timedelta(days=1)
        today = today_dt.strftime("%Y-%m-%d")
        log(f"Calculated date: {today}")
       
    project_dir = root_dir / "2026" / today_dt.strftime("%m") / f"{today}-project"

    print(f"Press ENTER in this console to continue with {today}-project ...")
 

    if not project_dir.exists():
        base_dir = root_dir / "2026" / now.strftime("%m") 
        if base_dir.exists():
            dirs = sorted([d for d in os.listdir(base_dir) if d.endswith("-project")], reverse=True)
            if dirs:
                project_dir = base_dir / dirs[0]

    if not project_dir.exists():
        log("Error: No project directory found.")
        return
    
    
    log(f"Working on project: {project_dir}")
    # input()
    x_posts_path = project_dir / "0.sources" / "x_posts.md"
    post_text = parse_x_posts(x_posts_path)
    
    if not post_text:
        log("⚠️ Warning: Could not parse post text from x_posts.md. Generating dynamic fallback...")
        
        # Parse available data for fallback
        daily_file_path = project_dir / "0.sources" / f"{project_dir.name.replace('-project', '')}.md"
        daily_data = parse_daily_metadata(daily_file_path)
        
        char_file_path = project_dir / "0.sources" / "charactor.md"
        char_name = "The Guardian"
        if char_file_path.exists():
            try:
                import json
                with open(char_file_path, "r", encoding="utf-8-sig") as f:
                    char_data = json.load(f)
                    char_name = char_data.get("image_description", {}).get("title", "The Guardian")
            except: pass

        theme = daily_data.get("theme", "A Day in History")
        keywords = daily_data.get("keywords", [])
        
        # Construct a meaningful fallback post
        post_text = f"✨ {theme}\n\nFeaturing {char_name}. A journey through time and legend.\n\n"
        if keywords:
            post_text += " ".join(keywords[:5])
        else:
            post_text += f"#History #Storytelling #{project_dir.name.replace('-project', '')}"
        
        if len(post_text) > 280: post_text = post_text[:277] + "..."

    log(f"📋 Parsed Post Content:\n{'-'*20}\n{post_text}\n{'-'*20}")
 
   
    # print(f"Press ENTER in this console to continue with {today}-project ...")
    # input()
 
    video_path = project_dir / "0.sources" / "cleaned_video.mp4"
 
    if not video_path.exists():
        log("❌ Error: Required assets missing.")
        return
 
    if dry_run:
        log("--- DRY RUN ---")
        log(f"Video Path: {video_path}")
        return
 

    # Skip automatic launch per user request
    log("Manual Browser Session mode.")
    print("\n" + "!"*50)
    print("IMPORTANT: Please make sure Chrome is OPEN and at x.com/home")
    print("The script will begin GUI automation in 3 seconds.")
    print("!"*50 + "\n")
    time.sleep(3)
    # find chrome window
    chrome_windows = pyautogui.getWindowsWithTitle("Google Chrome")
    if chrome_windows:
        chrome_window = chrome_windows[0]
        try:
            # move window to top left
            chrome_window.moveTo(0, 0)
            # maximize window
            chrome_window.maximize()
            # Bring to front
            chrome_window.activate()
            log("✅ Chrome window found and focused.")
        except Exception as e:
            log(f"⚠️ Could not manipulate Chrome window: {e}")
    else:
        log("⚠️ Warning: Could not find 'Google Chrome' window. Proceeding anyway...")


    # finder = ScreenTemplateFinder(confidence_threshold=0.8)
    # profile_template = str(script_dir / "autogui" / "x_com" / "reply_button.png")
    # if not finder.wait_and_click_template(profile_template, timeout=10):
    #     log("⚠️ Warning: Could not find reply_button.png. Continuing...")
    # else:
    #     time.sleep(2)

    # print(f"\nPress ENTER to continue with {today}-project posting...")
    # input()


    # goto https://x.com/home
    # goto address bar
    pyautogui.hotkey('ctrl', 'l')
    time.sleep(1)   
    pyautogui.write("https://x.com/home")
    pyautogui.press('enter')
    time.sleep(2)
    
    finder = ScreenTemplateFinder(confidence_threshold=0.8)
    
    log("Navigating via profile click...")
    profile_template = str(root_dir / "autogui" / "x_com" / "profile.png")
    if not finder.wait_and_click_template(profile_template, timeout=10):
        log("⚠️ Warning: Could not find profile.png. Continuing...")
    else:
        time.sleep(2)
    
    pyautogui.press('pagedown')
    time.sleep(2)
    pyautogui.press('pagedown')
    time.sleep(2)
    pyautogui.press('pagedown')
    time.sleep(2)

    profile_template = str(root_dir / "autogui" / "x_com" / "reply.png")
    if not finder.wait_and_click_template(profile_template, timeout=10):
        log("⚠️ Warning: Could not find profile.png. Continuing...")
    else:
        time.sleep(2)

 
    daily_file_path = project_dir / "0.sources" / f"{project_dir.name.replace('-project', '')}.md"
    daily_data = parse_daily_metadata(daily_file_path)

   

             
    # 2. Handle Image Upload First (opens separate dialog)
    if video_path and os.path.exists(video_path):
        log(f"Uploading image: {video_path}")
        image_btn_template = str(root_dir / "autogui" / "x_com" / "2_image_button.png")
        if finder.wait_and_click_template(image_btn_template, timeout=10):
            time.sleep(1.5)
            # Type image path in file dialog
            pyautogui.write(os.path.abspath(video_path))
            time.sleep(0.5)
            pyautogui.press('enter')
            log("Media path submitted. Waiting for upload (15s)...")
            time.sleep(15)
        else:
            log("Warning: Could not find '2_image_button.png'. Skipping media upload.")


   # 1. Focus and Click Text Box
    log("Finding text box...")
    text_box_template = str(root_dir / "autogui" / "x_com" / "post_text.png")
    if not finder.wait_and_click_template(text_box_template, timeout=10):
        log("Error: Could not find 'ost_text.png'. Please ensure composer is visible.")
        return False

    # 3. Input Text (after image upload window is closed)
    log("Typing text...")
    # Refocus text box just in case
    finder.wait_and_click_template(text_box_template, timeout=5)
    pyautogui.write(post_text, interval=0.01)
    time.sleep(1)

    pyautogui.press('pagedown')
    time.sleep(2)

    log("Finding captions...")
    text_caption_template = str(root_dir / "autogui" / "x_com" / "captions_link.png")
    if not finder.wait_and_click_template(text_caption_template, timeout=10):
        log("Error: Could not find 'captions_link.png'. Please ensure composer is visible.")
        return False
    
    text_caption_template = str(root_dir / "autogui" / "x_com" / "captions_button.png")
    if not finder.wait_and_click_template(text_caption_template, timeout=10):
        log("Error: Could not find 'captions_button.png'. Please ensure composer is visible.")
        return False

    caption_path = project_dir / "0.sources" / "script.srt"
    if not caption_path.exists():
        log("Error: Could not find 'script.srt'. Please ensure composer is visible.")
        return False

    pyautogui.write(os.path.abspath(caption_path))
    time.sleep(0.5)
    pyautogui.press('enter')
    log("Media path submitted. Waiting for upload (5s)...")
    time.sleep(5)

    text_caption_template = str(root_dir / "autogui" / "x_com" / "captions_done.png")
    if not finder.wait_and_click_template(text_caption_template, timeout=10):
        log("Error: Could not find 'captions_done.png'. Please ensure composer is visible.")
        return False
    time.sleep(3)


    finder = ScreenTemplateFinder(confidence_threshold=0.8)
    profile_template = str(root_dir / "autogui" / "x_com" / "reply_button.png")
    if not finder.wait_and_click_template(profile_template, timeout=10):
        log("⚠️ Warning: Could not find reply_button.png. Continuing...")
    else:
        time.sleep(2)

    print(f"\nPress ENTER to continue with {today}-project posting...")
    input()
   
 

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"Error: {e}")
        traceback.print_exc()
        input("\nPress ENTER to exit...")
