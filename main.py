import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, abort, request

# Load .env BEFORE importing coach — coach reads env vars at module level
# (DOTA_COACH_MODEL, OLLAMA_URL, GEMINI_URL, GEMINI_API_KEY).
load_dotenv()

from logger import setup_logging, switch_to_match

setup_logging()

from coach import Coach
from events import ACTIVE_GAME_STATES, EventDiffer
from extract_info import filter_minimap, print_snapshot, summarize

EXPECTED_TOKEN = os.getenv("DOTA2_GSI_TOKEN", "my_super_secret_token_123!")

SNAPSHOT_INTERVAL_SECONDS = 30

app = Flask(__name__)

differ = EventDiffer()
coach = Coach()
last_snapshot_time = 0.0
current_matchid = None


def safe_get(d, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


@app.post("/gsi/")
def gsi():
    global last_snapshot_time, current_matchid

    data = request.get_json(silent=True, force=True)
    if not data:
        return ("Expected JSON body", 400)

    token = safe_get(data, "auth", "token")
    if EXPECTED_TOKEN and token != EXPECTED_TOKEN:
        abort(401)

    if "minimap" in data:
        data["minimap"] = filter_minimap(data["minimap"])

    curr = summarize(data)

    matchid = curr.get("matchid")
    if matchid and matchid != current_matchid:
        switch_to_match(matchid)
        current_matchid = matchid

    events = differ.process(curr)
    curr["enemy_lineup"] = sorted(differ.seen_enemy_heroes)
    if events:
        ts = datetime.now().strftime("%H:%M:%S")
        for e in events:
            mins, secs = divmod(e["clock"], 60)
            print(f"[{ts}] [{mins:02d}:{secs:02d}] [{e['type']}] {e['msg']}")
        sys.stdout.flush()

    coach.maybe_advise(curr, events)

    now = time.time()
    if now - last_snapshot_time >= SNAPSHOT_INTERVAL_SECONDS:
        if curr.get("game_state") in ACTIVE_GAME_STATES:
            print_snapshot(curr)
        last_snapshot_time = now
        sys.stdout.flush()

    return ("ok", 200)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
