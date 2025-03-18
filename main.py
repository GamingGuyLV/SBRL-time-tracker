import os
import re
import json
import time
import base64
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- Configuration ---
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

#SERVICE_ACCOUNT_FILE = "gcp_key.json"  # Your Google API key file

ENCODED_GCP_KEY = ""
SERVICE_ACCOUNT_FILE = json.loads(base64.b64decode(ENCODED_GCP_KEY))

settings_file = "settings.txt"
scores_file = "scores.txt"
check_interval = 5  # Seconds between checks

# --- Helper Functions ---

def load_settings(settings_file):
    """Load settings from settings.txt (each line: key = "value")."""
    if not os.path.exists(settings_file):
        return None
    with open(settings_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    settings = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip().strip('"')
    return settings

def get_nickname(loginusers_path):
    """Extract the most recent PersonaName from a Steam loginusers.vdf file."""
    if not os.path.exists(loginusers_path):
        return None
    with open(loginusers_path, "r", encoding="utf-8") as f:
        data = f.read()
    users = re.findall(r'"(\d+)"\s*\{[^}]*?"PersonaName"\s+"(.*?)"[^}]*?"MostRecent"\s+"(\d)"', data, re.DOTALL)
    for steam_id, nick, most_recent in users:
        if most_recent == "1":
            return nick
    return None

def procHS(highscores_file):
    """Process the highscores JSON and return a dictionary of fastest times."""
    if not os.path.exists(highscores_file):
        return None
    with open(highscores_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = {}
    for track, trials in data.items():
        for trial_name, standings in trials.items():
            fastest_times = {}
            for standing_type, entries in standings.items():
                valid_entries = [entry for entry in entries if "sbrl" in entry["vehicleModel"].lower()]
                if valid_entries:
                    fastest_entry = min(valid_entries, key=lambda x: x["timeInMillis"])
                    fastest_times[standing_type] = fastest_entry["formattedTime"]
            if fastest_times:
                results[trial_name] = fastest_times
    return results

def load_old_scores(scores_file):
    """Load previous scores from scores.txt (stored as JSON)."""
    if os.path.exists(scores_file):
        with open(scores_file, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_scores(scores_file, data):
    """Save the processed scores (and nickname) as JSON."""
    with open(scores_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

def num_to_col(n):
    """Convert a 1-indexed column number to an Excel-style column letter."""
    result = ""
    while n:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result

def update_google_sheets(sheet_id, new_results, old_data, nickname):
    """Updates Google Sheets with the latest scores."""
    sorted_trials = sorted(new_results.keys())
    row_nickname = [nickname]
    row_fastest = ["Fastest"]

    for trial in sorted_trials:
        new_multi = new_results[trial].get("standing2", "")
        old_multi = old_data.get("results", {}).get(trial, {}).get("standing2", "") if old_data else ""
        row_nickname.append(new_multi if new_multi != old_multi else "")
        
        new_fast = new_results[trial].get("standing0", "")
        old_fast = old_data.get("results", {}).get(trial, {}).get("standing0", "") if old_data else ""
        row_fastest.append(new_fast if new_fast != old_fast else "")

    num_cols = len(row_nickname)
    end_col = num_to_col(num_cols)
    range_str = f"Uploads!A2:{end_col}3"

    credentials = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('sheets', 'v4', credentials=credentials)
    sheet = service.spreadsheets()

    values = [row_nickname, row_fastest]
    body = {'values': values}
    sheet.values().update(spreadsheetId=sheet_id, range=range_str, valueInputOption='USER_ENTERED', body=body).execute()

    print("Scores updated on Google Sheets.")

# --- Main Loop ---
if not os.path.exists(settings_file):
    print("Settings file missing. Exiting.")
    exit()

settings = load_settings(settings_file)
for key in ["LOGINUSERS", "HIGHSCORES", "SHEETID"]:
    if settings.get(key, "X/X/X/X") == "X/X/X/X":
        print(f"Invalid setting for {key}. Exiting.")
        exit()

loginusers_path = settings["LOGINUSERS"]
highscores_path = settings["HIGHSCORES"]
sheet_id = settings["SHEETID"]

if not os.path.exists(loginusers_path):
    print("Loginusers file not found. Exiting.")
    exit()
if not os.path.exists(highscores_path):
    print("Highscores file not found. Exiting.")
    exit()

nickname = get_nickname(loginusers_path)
if not nickname:
    print("Could not determine nickname. Exiting.")
    exit()

last_modified = os.path.getmtime(highscores_path)  # Get initial timestamp

print("Monitoring highscores.json for updates... Press Ctrl + C to stop.")

while True:
    try:
        time.sleep(check_interval)
        current_modified = os.path.getmtime(highscores_path)

        if current_modified != last_modified:
            print("\nHighscores file updated, processing changes...")

            new_results = procHS(highscores_path)
            if new_results is None:
                print("Failed to process highscores.")
                continue

            new_data = {"nickname": nickname, "results": new_results}
            old_data = load_old_scores(scores_file)

            if new_data != old_data:
                print("Changes detected. Updating scores file and Google Sheets...")
                save_scores(scores_file, new_data)
                update_google_sheets(sheet_id, new_results, old_data, nickname)
            else:
                print("No actual score changes, skipping update.")

            last_modified = current_modified

    except KeyboardInterrupt:
        print("\nExiting...")
        break