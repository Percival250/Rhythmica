import json
import os

CONFIG_FILE = "config.json"

def load_download_dir():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("download_dir", "")
    return ""

def save_download_dir(path):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"download_dir": path}, f, indent=2) 