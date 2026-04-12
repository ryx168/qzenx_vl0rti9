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
from urllib.parse import quote

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add autogui to path
script_dir = Path(__file__).parent.absolute()
# The script is in autogui/x_com/, so autogui root is the parent
autogui_root = script_dir.parent
project_root = autogui_root.parent

if str(autogui_root) not in sys.path:
    sys.path.append(str(autogui_root))

try:
    from template_finder import ScreenTemplateFinder
except ImportError:
    print(f"Error: Could not import ScreenTemplateFinder from {autogui_root}")
    # Fallback if somehow it's run from elsewhere
    potential_roots = [
        Path.cwd(),
        Path.cwd() / "autogui"
    ]
    for root in potential_roots:
        if (root / "template_finder.py").exists():
            sys.path.append(str(root))
            break
    from template_finder import ScreenTemplateFinder

# Configuration
CONFIG = {
    "userDataDir": project_root / ".browser-data",
    "historyFile": project_root / "posted_products.json",
    "amazonTag": "dayinhistory-20",
    # Multiple common Chrome paths on Windows
    "chromePaths": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")
    ]
}

# Resolve Chrome path
CONFIG["chromeExe"] = "chrome"  # Default to path
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

def parse_enhanced_shop_data(file_path):
    if not os.path.exists(file_path):
        return []
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    results = []
    # Match entries in the TS file similar to the JS regex
    # Match both:
    # 1. imageUrl: getAssetPath("...")
    # 2. previews: [getAssetPath("...")]
    entry_regex = r"\{[\s\S]*?title:\s*['\"](.*?)['\"][\s\S]*?price:\s*([\d.]+)[\s\S]*?(?:imageUrl|previews):\s*(?:\[)?(?:getAssetPath\(['\"])?(.*?)['\"\)](?:\])?"
    
    matches = re.finditer(entry_regex, content)
    for match in matches:
        results.append({
            "title": match.group(1),
            "price": match.group(2),
            "image": match.group(3)
        })
    return results

def get_pinned_post_url(finder):
    log("--- INITIALIZATION: Fetching Pinned Post URL ---")
    
    for attempt in range(1, 4):
        try:
            log(f"Fetching pinned post (Attempt {attempt}/3)...")
            
            # Go to profile
            pyautogui.hotkey('ctrl', 'l')
            time.sleep(1)
            pyautogui.write("https://x.com/home")
            pyautogui.press('enter')
            time.sleep(3)
            while True:
                profile_template = str(script_dir / "profile_current.png")
                if not finder.check_template_exists(profile_template, verbose=False):      
                    log("⚠️ Could not find profile_current.png. Trying manual profile link click...")
                    profile_template = str(script_dir / "profile.png")
                    if not finder.wait_and_click_template(profile_template, timeout=3):
                        log("⚠️ Could not find profile.png. Trying manual profile link click...")
                        # Fallback: maybe just type the URL if we knew the handle, 
                else:    
                    break
               
                time.sleep(2) 
           

            while True:
                profile_template = str(script_dir / "share.png")
                if not finder.check_template_exists(profile_template, verbose=False):
                    log("⚠️ Could not find share.png. Trying scroll...")
                    pyautogui.moveTo(1197, 705, duration=0.5)
                    pyautogui.click()
                    time.sleep(1)  
                    pyautogui.press('pagedown')  
                else:
                    break

            profile_template = str(script_dir / "share.png")
            if not finder.wait_and_click_template(profile_template, timeout=10):
                log("⚠️ Warning: Could not find share.png. Continuing...")
            else:
                time.sleep(2)

            profile_template = str(script_dir / "copy_link.png")
            if not finder.wait_and_click_template(profile_template, timeout=10):
                log("⚠️ Warning: Could not find copy_link.png. Continuing...")
            else:
                time.sleep(2)
 
            # import pyperclip
            pinned_url = pyperclip.paste().strip()
            
            if "status" in pinned_url:
                log(f"✅ Pinned Post URL captured: {pinned_url}")
                return pinned_url
            else:
                log(f"⚠️ Captured URL does not seem to be a status: {pinned_url}")
                
        except Exception as e:
            log(f"⚠️ Attempt {attempt} failed: {e}")
            time.sleep(2)
            
    return ""

def post_product(finder, product, project_dir, pinned_post_url):
    amazon_link = f"https://www.amazon.com/s?k={quote(product['title'])}&tag={CONFIG['amazonTag']}"
    text = f"✨ Featured Product: {product['title']}\n\nPrice: ${product['price']}\n\nCheck it out here: {amazon_link}\n\n#History #Gifts #TheDayInHistory"

    log(f"Posting product: {product['title']}")
    
    try:
        # 1. Navigate to Home/Compose
        # pyautogui.hotkey('ctrl', 'l')
        # time.sleep(0.5)
        # pyautogui.write("https://x.com/home")
        # pyautogui.press('enter')
        # time.sleep(5)

        profile_template = str(script_dir / "home.png")
        if not finder.wait_and_click_template(profile_template, timeout=10):
            log("⚠️ Warning: Could not find home.png. Continuing...")
        else:
            time.sleep(2)

        post_btn_template = str(script_dir / "post_wait.png")
        if finder.check_template_exists(post_btn_template, verbose=True, threshold=0.98, max_mean_diff=5.0):

            # print(f"\nPress ENTER to continue with post_wait...")
            # input()   

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
            
            # 2. Click Text Box
            text_box_template = str(script_dir / "1_text_box.png")
            if not finder.wait_and_click_template(text_box_template, timeout=15):
                log("❌ Error: Could not find text box template.")
                return False
                
            # 3. Handle Image Upload
            image_relative = product['image'].lstrip('/')
            image_path = project_dir / "public" / image_relative
            if not image_path.exists():
                image_path = project_dir / image_relative
                
            if image_path.exists():
                log(f"Uploading image: {image_path}")
                image_btn_template = str(script_dir / "2_image_button.png")
                if finder.wait_and_click_template(image_btn_template, timeout=10):
                    time.sleep(2)
                    pyautogui.write(str(image_path.absolute()))
                    time.sleep(1)
                    pyautogui.press('enter')
                    log("Waiting for upload (10s)...")
                    time.sleep(10)
            else:
                log(f"⚠️ Image not found: {image_path}")

            # 4. Type Text
            log("Typing product details...")
            finder.wait_and_click_template(text_box_template, timeout=5)
            pyautogui.write(text, interval=0.01)
            time.sleep(2)


        pyautogui.moveTo(1197, 705, duration=0.5)
        pyautogui.click()
        time.sleep(1)  
        pyautogui.press('pageup')  

        # print(f"\nPress ENTER to continue with project posting...")
        # input()   
        
        # 5. Click Post
        while True:
            post_btn_template = str(script_dir / "post.png")
            if finder.check_template_exists(post_btn_template, verbose=False):
                log("⚠️ Could not find post.png. Trying post..")

                post_btn_template = str(script_dir / "post.png")
                if finder.wait_and_click_template(post_btn_template, timeout=3):
                    log("❌ Error: Could not find post button.")
                    break
            else:
                break
            time.sleep(3)
       
        # print(f"\nPress ENTER to continue with -project posting...")
        # input()   

        
        # 6. Reply with Pinned Link
        # log("Navigating to Profile to add reply...")
        # profile_template = str(script_dir / "profile.png")
        # if not finder.wait_and_click_template(profile_template, timeout=15):
        #     log("⚠️ Could not navigate to profile. Skipping reply.")
        #     return True # Main post succeeded
            
        time.sleep(3)
        # Scroll down past pinned post
        pyautogui.press('pagedown')
        time.sleep(2)
        
        log("Finding the Reply button on the new tweet...")
        reply_template = str(script_dir / "reply.png")
        # In the JS version, it targets the 2nd article. 
        # In GUI mode, we just look for the reply template below the pinned one.
        if not finder.wait_and_click_template(reply_template, timeout=15):
            log("⚠️ Could not find reply button. Trying lower...")
            pyautogui.press('pagedown')
            time.sleep(1)
            if not finder.wait_and_click_template(reply_template, timeout=10):
                log("❌ Could not find reply button.")
                return True # Main post still succeeded
                
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

def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    all_products = "--all" in args
    
    date_arg = None
    if "--date" in args:
        idx = args.index("--date")
        if idx + 1 < len(args):
            date_arg = args[idx + 1]
            
    product_query = next((a for a in args if not a.startswith("--") and a != date_arg), None)

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
    
    # Project Discovery
    if date_arg:
        today = date_arg
        log(f"Using provided date: {today}")
    else:
        now = datetime.now()
        if now.hour > 10:
            if now.hour > 11:
                today = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                today = now.strftime("%Y-%m-%d")
        else:
            today = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        log(f"Calculated date: {today}")
       
    year_month = today.split("-")[0] + "/" + today.split("-")[1]
    project_dir = project_root / year_month / f"{today}-project"

    if not project_dir.exists():
        # Search in 2026/02 and 2026/03
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
    
    log(f"Working on project: {project_dir}")

    # 1. Discover and Parse Products
    shop_data_path = project_dir / "src" / "components" / "Shop" / "shopData.ts"
    hist_shop_data_path = project_dir / "src" / "components" / "Shop" / "historicalShopData.ts"
    
    products = parse_enhanced_shop_data(shop_data_path)
    if not products:
        log(f"No products in {shop_data_path}, checking historicalShopData.ts...")
        products = parse_enhanced_shop_data(hist_shop_data_path)
        
    if not products:
        log(f"❌ Error: No products found in {project_dir}")
        return

    # 2. Select Targets
    targets = []
    if all_products:
        targets = products
        log(f"Targeting ALL {len(targets)} products.")
    elif product_query:
        targets = [p for p in products if product_query.lower() in p['title'].lower()]
        log(f"Targeting products matching query: '{product_query}' ({len(targets)} found).")
    else:
        # Default: 1 random product
        targets = products
        log("No product specified. Defaulting to 1 random product selection.")

    # 3. Filter by History
    history = load_history()
    targets = [p for p in targets if p['title'] not in history]
    
    if not targets:
        log("No new products to post.")
        return

    # 4. Handle Dry Run
    if dry_run:
        log("--- DRY RUN: Selection Result ---")
        random.shuffle(targets)
        # If it was the default (random 1), limit it now
        if not all_products and not product_query:
            targets = targets[:1]
        
        for p in targets:
            print(f"- {p['title']} (${p['price']}) -> {p['image']}")
        return

    # 5. GUI Initialization (Only if not dry run)
    pinned_url = get_pinned_post_url(finder)
    if not pinned_url:
        log(f"❌ Error: Could not get pinned post URL. Exiting.")
        return

    print(f"\nPress ENTER to continue with {today}-project posting...")
    # input()   

    # Shuffle for actual posting
    random.shuffle(targets)
    if not all_products and not product_query:
        targets = targets[:1]
        log(f"Picked random product: {targets[0]['title']}")

    # Focus Chrome
    chrome_windows = pyautogui.getWindowsWithTitle("Google Chrome")
    if chrome_windows:
        chrome_window = chrome_windows[0]
        try:
            chrome_window.moveTo(0, 0)
            chrome_window.maximize()
            chrome_window.activate()
            log("✅ Chrome window focused.")
        except Exception as e:
            log(f"⚠️ Window manipulation error: {e}")
    else:
        log("⚠️ Warning: Chrome window not found. Ensure it is open.")

    for i, product in enumerate(targets):
        success = post_product(finder, product, project_dir, pinned_url)
        
        if success:
            history.append(product['title'])
            save_history(history)
            log(f"✅ Finished: {product['title']}")
            
        if i < len(targets) - 1:
            log("Waiting 1 hour until next post...")
            # We can use small steps to allow easier interruption if needed
            for _ in range(360):
                time.sleep(10)
    
    log("🎉 All targeted products processed!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nStopped by user.")
    except Exception as e:
        import traceback
        log(f"Fatal error: {e}")
        traceback.print_exc()
        input("Press ENTER to exit...")
