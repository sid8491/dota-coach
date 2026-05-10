# Dota Coach

A local Dota 2 coaching assistant. The Dota client streams game state to a Flask server on `127.0.0.1:8000`. Each tick is summarized, diffed against the previous tick, and key moments (death, low HP, ult ready, enemies missing, periodic check, etc.) trigger a local LLM via Ollama. The LLM's tip is logged and spoken via Piper TTS.

## What it does

- Listens to Dota 2's Game State Integration (GSI) and parses every tick into a flat summary (hero, items, abilities, allies/enemies in vision, score, roshan, server events).
- Detects gameplay moments worth coaching: deaths, low HP/MP, ult-ready, level-up milestones, talent picks, aghs/shard pickups, enemies missing from the map, lost buildings, roshan/aegis events, plus a periodic strategic check-in.
- Throttles LLM calls with a global cooldown, per-trigger cooldown, in-flight guard, and a freshness check that drops stale tips when the situation has already resolved by the time the model responds.
- Speaks the tip aloud via Piper TTS so you don't have to read it mid-fight.
- Logs every match to `logs/<matchid>.log` for after-game review.

## Requirements at a glance

- Windows 10/11 (audio path uses `winsound`)
- Python 3.10+, Flask, piper-tts
- Ollama running locally with a chat-capable model
- NVIDIA GPU recommended — ≥16 GB VRAM for the default `qwen3.6:27b`, ~5 GB for `gemma4:e4b`
- Dota 2 with a GSI cfg file dropped into `gamestate_integration/`

Full install, run, tuning, and troubleshooting steps below.

---

## 1. Prerequisites

- **Windows 10/11** (paths and audio playback use `winsound`; Linux/macOS would need `tts.py` adapted)
- **Dota 2** installed via Steam
- **Python 3.10+**
- **Ollama** (local LLM server)
- **NVIDIA GPU** strongly recommended; CPU-only inference is too slow for live use
- ~30 GB free disk for the 27B model, ~5 GB for smaller models

---

## 2. Clone / Get the Code

```powershell
cd D:\work\develop
git clone <your-repo-url> dota_coach
cd dota_coach
```

If you already have the folder, just `cd` into it.

---

## 3. Python Virtual Environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If activation is blocked by execution policy:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Install dependencies:

```powershell
pip install --upgrade pip
pip install flask piper-tts
```

That's it — the project uses only Python stdlib + Flask + piper-tts. (Ollama is called over HTTP, so no Python client needed.)

Verify:

```powershell
python -c "import flask, piper; print('ok')"
```

---

## 4. Ollama + Model

### 4.1 Install Ollama

Download from https://ollama.com/download/windows and run the installer. After install, Ollama runs as a background service and listens on `http://localhost:11434`.

Verify:

```powershell
ollama --version
curl http://localhost:11434/api/tags
```

The `curl` should return JSON (possibly an empty `models` list).

### 4.2 Pull a Model

The project defaults to `qwen3.6:27b` (high quality, 8–22s latency, needs ~16 GB VRAM). For lower latency use `gemma4:e4b` (~2–4s, ~5 GB VRAM, lower quality).

```powershell
# High quality, slower (recommended if you have ≥16 GB VRAM)
ollama pull qwen3.6:27b

# Faster, smaller (good for testing or lower-end hardware)
ollama pull gemma4:e4b
```

Verify the model loads:

```powershell
ollama run qwen3.6:27b "say hi"
```

You can press `Ctrl+D` (or `/bye`) to exit the Ollama REPL.

### 4.3 Model Comparison

| Model         | VRAM   | Latency | Hero/ability accuracy | Item rec quality |
|---------------|--------|---------|-----------------------|------------------|
| `qwen3.6:27b` | ~16 GB | 8–22 s  | High — names allies & abilities correctly | High |
| `gemma4:e4b`  | ~5 GB  | 2–4 s   | Medium — guesses ability mechanics from names | Medium |
| `gpt-oss:20b` | ~14 GB | varies  | Thinking model — often empty content under our token cap; **not recommended** |

---

## 5. Piper TTS Voice

The project bundles audio playback via `winsound` and uses Piper to synthesize tips.

### 5.1 Voice File

Download `en_US-amy-medium.onnx` and `en_US-amy-medium.onnx.json` from:
https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/amy/medium

Place both files here:

```
D:\work\develop\dota_coach\models\en_US-amy-medium.onnx
D:\work\develop\dota_coach\models\en_US-amy-medium.onnx.json
```

(The `.onnx.json` file is the metadata sidecar — Piper needs both.)

### 5.2 Test TTS

```powershell
python tts.py
```

You should hear "Coach online. Synthesis test, one two three." If no sound:

- Check the voice files exist at the path above
- Check Windows audio output device is correct
- Set `$env:TTS_DISABLED = "1"` to mute and run text-only

### 5.3 Use a Different Voice

```powershell
$env:PIPER_MODEL = "C:\path\to\en_GB-alan-medium.onnx"
```

Browse all Piper voices: https://huggingface.co/rhasspy/piper-voices

---

## 6. Dota 2 GSI Config

Dota's Game State Integration (GSI) posts JSON to your local server every tick. You enable it by dropping a `.cfg` file into Dota's gamestate folder.

### 6.1 Locate the Folder

Default Steam install:
```
C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta\game\dota\cfg\gamestate_integration\
```

If `gamestate_integration` doesn't exist, create it.

### 6.2 Create the Config File

Create `gamestate_integration_coach.cfg` in that folder with this content:

```
"Dota 2 Integration Configuration"
{
    "uri"           "http://127.0.0.1:8000/gsi/"
    "timeout"       "5.0"
    "buffer"        "0.1"
    "throttle"      "0.5"
    "heartbeat"     "30.0"
    "data"
    {
        "buildings"     "1"
        "provider"      "1"
        "map"           "1"
        "player"        "1"
        "hero"          "1"
        "abilities"     "1"
        "items"         "1"
        "draft"         "1"
        "wearables"     "0"
        "minimap"       "1"
        "roshan"        "1"
        "couriers"      "1"
        "neutralitems"  "1"
        "events"        "1"
    }
    "auth"
    {
        "token"         "my_super_secret_token_123!"
    }
}
```

### 6.3 Token

The default in `main.py` is `my_super_secret_token_123!`. To use a different token:

1. Edit the cfg file's `token` line
2. Set `$env:DOTA2_GSI_TOKEN = "your-new-token"` before running `main.py`

### 6.4 Restart Dota

Dota only loads GSI configs on startup. Quit and relaunch Dota after dropping the cfg.

---

## 7. Run the Coach

Open PowerShell, activate the venv, optionally set the model, then start the server:

```powershell
cd D:\work\develop\dota_coach
.\.venv\Scripts\Activate.ps1

# Optional: pick a model (default is qwen3.6:27b)
$env:DOTA_COACH_MODEL = "gemma4:e4b"

python main.py
```

You should see startup lines like:

```
[tts] loaded en_US-amy-medium.onnx
[coach] model: gemma4:e4b via http://localhost:11434/api/chat
 * Serving Flask app 'main'
 * Running on http://127.0.0.1:8000
```

Now launch a Dota match (bot match, lobby, or matchmaking — GSI works in all). Once you load in, you'll see snapshot lines and `[COACH][model] tip text...` lines in the console.

> **Important — PowerShell vs cmd.exe:** Use `$env:VAR = "value"`, **not** `set VAR=value`. The latter is cmd.exe syntax and silently does nothing in PowerShell, so the coach would fall back to the default model.

---

## 8. Verify End-to-End

1. **Server reachable**: in another PowerShell window:
   ```powershell
   curl http://127.0.0.1:8000/gsi/
   ```
   Expect 401 (no token) or "Method Not Allowed" — both mean the server is up.

2. **GSI flowing**: in Dota, after match starts, the console should print `[server_event]`, `[game_state]`, etc.

3. **LLM responding**: trigger an event (die, hit low HP, level up). Watch for `[COACH][model] ...` within a few seconds (gemma) or up to ~20s (qwen).

4. **TTS speaking**: you should hear Amy reading the tip.

5. **Logs persist**: a file named `logs\<matchid>.log` is created and updated. Pre-match output goes to `logs\session_<timestamp>.log` and gets renamed once the match ID is known.

---

## 9. Switching Models Mid-Session

Models are read at startup. To switch:

1. Stop `main.py` (Ctrl+C)
2. `$env:DOTA_COACH_MODEL = "qwen3.6:27b"`
3. Relaunch `python main.py`

You don't need to restart Dota.

---

## 10. Tunable Knobs

All in `coach.py`:

| Constant                     | Default      | What it does |
|------------------------------|--------------|--------------|
| `MIN_INTERVAL_SEC`           | 8            | Minimum game-time seconds between LLM calls |
| `PER_TRIGGER_COOLDOWN_SEC`   | 30           | Per trigger-type cooldown (so death tips don't fire 4× during a respawn) |
| `RECENT_TIPS_MEMORY`         | 8            | How many recent tips are sent back to the model so it doesn't repeat |
| `LEVEL_UP_MILESTONES`        | {2,6,10,12,15,18,20,25} | Only fire level_up tips at these levels |
| `FRESH_HP_RECOVERED_PCT`     | 60           | Drop a `low_hp` tip if HP is back above this when LLM responds |
| `FRESH_MP_RECOVERED_PCT`     | 50           | Same for `low_mp` |
| `FRESH_MISSING_ENEMIES_MIN`  | 2            | Drop an `enemies_missing` tip if fewer than this many are missing now |

In `events.py`:

| Constant                          | Default | What it does |
|-----------------------------------|---------|--------------|
| `LOW_HP_PCT`                      | 30      | Fire `low_hp` when HP ≤ this percent |
| `LOW_MP_PCT`                      | 20      | Fire `low_mp` when MP ≤ this percent |
| `MISSING_ENEMIES_THRESHOLD`       | 2       | How many missing enemies trigger the warning |
| `MISSING_ENEMIES_SUSTAIN_SEC`     | 5       | How long they must be missing before firing |
| `PERIODIC_CHECK_INTERVAL_SEC`     | 120     | How often the periodic check fires |

---

## 11. Troubleshooting

**`[coach] model: <something I didn't pick>`**
You used `set DOTA_COACH_MODEL=...` (cmd.exe). Use `$env:DOTA_COACH_MODEL = "..."` in PowerShell.

**`[coach] empty content from <model>`**
The model returned empty content — common with thinking models (gpt-oss). Either switch model or increase `num_predict` in `_call_ollama` (with a latency cost). Check the `thinking_excerpt` in the log to confirm.

**`[coach] ollama unreachable at http://localhost:11434/api/chat`**
Ollama isn't running. Check `Get-Process ollama` or restart the Ollama tray app.

**No `[COACH]` lines but events are flowing**
Either the cooldowns are filtering everything (lower `MIN_INTERVAL_SEC`), the model is returning `OK`, or all tips are being dropped as duplicates / stale. Check for `[coach] dropped ...` lines in the log.

**No GSI events arriving**
- Cfg file not picked up: confirm Dota was restarted after dropping the cfg
- Cfg in the wrong folder: must be `cfg\gamestate_integration\` under your Dota install
- Token mismatch: cfg `token` must equal `DOTA2_GSI_TOKEN` env var (or default)
- Server not running: make sure `python main.py` is still running and shows the Flask banner

**TTS silent but tips appear in console**
- Voice files missing — see section 5.1
- `TTS_DISABLED=1` env var still set
- Windows audio device routed wrong — check the system tray volume mixer

**Tips reference wrong heroes / wrong abilities**
That's a model quality limit. Larger models (qwen 27b) hallucinate less than smaller ones (gemma 4b). Some hero-specific mechanics (e.g. Earth Spirit's Magnetize) are guessed from names by smaller models.

**Werkzeug spamming `200 -` lines in the log**
Already silenced in `logger.py` (`werkzeug` logger set to WARNING). If you still see them, check that `setup_logging()` is called before `from coach import Coach` in `main.py`.

---

## 12. File Layout

```
dota_coach/
├── main.py              # Flask entry point + per-tick orchestration
├── coach.py             # LLM caller, prompt builder, dedup, freshness check
├── events.py            # EventDiffer — turns ticks into typed events
├── extract_info.py      # Raw GSI JSON → flat summary dict
├── constants.py         # Loads OpenDota items/heroes/abilities JSON
├── tts.py               # Piper voice synthesis + winsound playback
├── logger.py            # Tee'd stdout/stderr → console + per-match log
├── data/
│   ├── items.json       # OpenDota dotaconstants
│   ├── heroes.json
│   └── abilities.json
├── models/
│   ├── en_US-amy-medium.onnx       # Piper voice
│   └── en_US-amy-medium.onnx.json
├── logs/                # <matchid>.log per game, session_<ts>.log before match start
└── .venv/
```

---

## 13. Next Steps After First Run

After your first real match, look at the log file and check for:

- **Tip rate**: Aim for ~1.5–2.5 tips/min. Adjust `MIN_INTERVAL_SEC` and `PER_TRIGGER_COOLDOWN_SEC` if too noisy or too quiet.
- **`[coach] dropped duplicate tip`**: Healthy if 5–25% of attempts. If higher, the model is repeating itself — try a bigger model or raise `RECENT_TIPS_MEMORY`.
- **`[coach] dropped stale … tip`**: Indicates LLM latency exceeds the action window. Try a smaller model or accept it as a correctness safety net.
- **Hallucinated abilities/items**: Compare the tip's named ability vs. your hero's actual kit. Smaller models do this more often.
