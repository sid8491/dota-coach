# Dota Coach — Setup Guide

A local Dota 2 coaching assistant. The Dota client streams game state to a Flask server on `127.0.0.1:8000`. Each tick is summarized, diffed against the previous tick, and key moments (death, low HP, ult ready, enemies missing, periodic check, etc.) trigger an LLM (local via Ollama, or cloud via Gemini). The LLM's tip is logged and spoken via Piper TTS.

Tested on Windows 11 with PowerShell 7+. NVIDIA GPU recommended for local models (≥16 GB VRAM for the 27B model, 8 GB for smaller ones); cloud models need only an API key.

---

## 1. Prerequisites

- **Windows 10/11** (paths and audio playback use `winsound`; Linux/macOS would need `tts.py` adapted)
- **Dota 2** installed via Steam
- **Python 3.10+**
- **Ollama** (only if running local models)
- **NVIDIA GPU** strongly recommended for local; CPU-only inference is too slow for live use
- ~30 GB free disk for the 27B model, ~5 GB for smaller models (cloud needs none)

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
pip install flask piper-tts python-dotenv
```

That's it — the project uses only Python stdlib + Flask + piper-tts. (Both Ollama and Gemini are called over HTTP, so no provider SDKs needed.)

Verify:

```powershell
python -c "import flask, piper, dotenv; print('ok')"
```

---

## 4. Local LLM — Ollama

Skip this section if you only plan to use cloud models (jump to section 5).

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

Press `Ctrl+D` (or type `/bye`) to exit the Ollama REPL.

### 4.3 Local Model Comparison

| Model         | VRAM   | Latency | Hero/ability accuracy | Item rec quality |
|---------------|--------|---------|-----------------------|------------------|
| `qwen3.6:27b` | ~16 GB | 8–22 s  | High — names allies & abilities correctly | High |
| `gemma4:e4b`  | ~5 GB  | 2–4 s   | Medium — guesses ability mechanics from names | Medium |
| `gpt-oss:20b` | ~14 GB | varies  | Thinking model — often empty content under our token cap; **not recommended** |

---

## 5. Cloud LLM — Gemini (Optional)

The coach auto-detects cloud routing from the model name. If you set the model to a `gemini-*` ID and provide an API key, requests go to Google's OpenAI-compatible endpoint instead of Ollama.

### 5.1 Get an API Key
1. Go to https://aistudio.google.com/apikey
2. Create an API key (free tier available)
3. Copy the key

### 5.2 Configure
```powershell
$env:GEMINI_API_KEY = "AIza...your-key..."
$env:DOTA_COACH_MODEL = "gemini-2.5-flash"
```

The startup line should now show:
```
[coach] model: gemini-2.5-flash via https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
```

### 5.3 Recommended Cloud Models

| Model                | Latency  | Cost / 50-min game | Notes |
|----------------------|----------|--------------------|-------|
| `gemini-2.5-flash`   | ~0.5–1 s | ~$0.02             | Default cloud choice — fast, cheap, smart |
| `gemini-2.5-pro`     | ~1–3 s   | ~$0.30             | Higher quality, slower |
| `gemini-2.0-flash`   | ~0.5–1 s | ~$0.01             | Cheapest |

### 5.4 Privacy Note
When using cloud models, your live game state (hero, items, position, KDA, enemy lineup) is sent to Google for each tip. Don't use cloud routing if that's a concern.

### 5.5 Switching Back to Local
Set the model to a non-`gemini-` name:
```powershell
$env:DOTA_COACH_MODEL = "qwen3.6:27b"
```

---

## 6. Piper TTS Voice

The project synthesizes tips locally with Piper and plays them via `winsound`.

### 6.1 Voice File

Download `en_US-amy-medium.onnx` and `en_US-amy-medium.onnx.json` from:
https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/amy/medium

Place both files here:

```
D:\work\develop\dota_coach\models\en_US-amy-medium.onnx
D:\work\develop\dota_coach\models\en_US-amy-medium.onnx.json
```

(The `.onnx.json` file is the metadata sidecar — Piper needs both.)

### 6.2 Test TTS

```powershell
python tts.py
```

You should hear "Coach online. Synthesis test, one two three." If no sound:

- Check the voice files exist at the path above
- Check Windows audio output device is correct
- Set `$env:TTS_DISABLED = "1"` to mute and run text-only

### 6.3 Use a Different Voice

```powershell
$env:PIPER_MODEL = "C:\path\to\en_GB-alan-medium.onnx"
```

Browse all Piper voices: https://huggingface.co/rhasspy/piper-voices

---

## 7. Dota 2 GSI Config

Dota's Game State Integration (GSI) posts JSON to your local server every tick. You enable it by dropping a `.cfg` file into Dota's gamestate folder.

### 7.1 Locate the Folder

Default Steam install:
```
C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta\game\dota\cfg\gamestate_integration\
```

If `gamestate_integration` doesn't exist, create it.

### 7.2 Create the Config File

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

### 7.3 Token

The default in `main.py` is `my_super_secret_token_123!`. To use a different token:

1. Edit the cfg file's `token` line
2. Set `$env:DOTA2_GSI_TOKEN = "your-new-token"` before running `main.py`

### 7.4 Restart Dota

Dota only loads GSI configs on startup. Quit and relaunch Dota after dropping the cfg.

---

## 8. Configure via `.env` (Recommended)

Instead of setting env vars in your shell every session, drop a `.env` file in the project root. `main.py` loads it automatically before importing the coach.

Copy the template:
```powershell
copy .env.example .env
```

Then edit `.env`:
```
DOTA_COACH_MODEL=gemini-2.5-flash
GEMINI_API_KEY=AIza...your-key...
```

**`.env` is gitignored** — your key won't be committed. `.env.example` is the committed template.

**`.env` is authoritative.** `main.py` calls `load_dotenv(override=True)`, which means values in `.env` overwrite anything you may have already set with `$env:...` in your shell. Edit `.env` and re-run — that's the workflow.

If you want to test a one-off model without editing `.env`, comment the relevant line out:
```
# DOTA_COACH_MODEL=gemini-2.5-flash
```
Then in your shell:
```powershell
$env:DOTA_COACH_MODEL = "qwen3.6:27b"
python main.py
```
(With the `.env` line commented, the shell var is the only source.)

---

## 9. Run the Coach

Open PowerShell, activate the venv, then start the server. With `.env` configured, no `$env:` lines are needed:

```powershell
cd D:\work\develop\dota_coach
.\.venv\Scripts\Activate.ps1
python main.py
```

Or override the model just for this run:
```powershell
$env:DOTA_COACH_MODEL = "gemma4:e4b"
python main.py
```

You should see startup lines like:

```
[tts] loaded en_US-amy-medium.onnx
[coach] model: gemini-2.5-flash via https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
 * Serving Flask app 'main'
 * Running on http://127.0.0.1:8000
```

Now launch a Dota match (bot match, lobby, or matchmaking — GSI works in all). Once you load in, you'll see snapshot lines and `[COACH][model] tip text...` lines in the console.

> **Important — PowerShell vs cmd.exe:** Use `$env:VAR = "value"`, **not** `set VAR=value`. The latter is cmd.exe syntax and silently does nothing in PowerShell, so the coach would fall back to the default model.

---

## 9. Verify End-to-End

1. **Server reachable**: in another PowerShell window:
   ```powershell
   curl http://127.0.0.1:8000/gsi/
   ```
   Expect 401 (no token) or "Method Not Allowed" — both mean the server is up.

2. **GSI flowing**: in Dota, after match starts, the console should print `[server_event]`, `[game_state]`, etc.

3. **LLM responding**: trigger an event (die, hit low HP, level up). Watch for `[COACH][model] ...` within a few seconds (gemini/gemma) or up to ~20s (qwen).

4. **TTS speaking**: you should hear Amy reading the tip.

5. **Logs persist**: a file named `logs\<matchid>.log` is created and updated. Pre-match output goes to `logs\session_<timestamp>.log` and gets renamed once the match ID is known.

---

## 10. Switching Models Mid-Session

Models are read at startup. To switch:

1. Stop `main.py` (Ctrl+C)
2. `$env:DOTA_COACH_MODEL = "..."`
3. Relaunch `python main.py`

You don't need to restart Dota.

---

## 11. Tunable Knobs

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

### Per-match item dedup

The coach automatically tracks every item it recommends within a single match, and tells the model "don't recommend any of these again." Reset happens when `matchid` changes. No knob needed; this is on by default and runs for both local and cloud models.

### Ability descriptions

Every ability in your hero's kit is sent to the model with a short one-line description from OpenDota's `abilities.json`. This stops smaller models from guessing what an ability does based on its name (e.g., gemma calling Magnetize a "single-target reposition tool"). No knob needed; on by default.

---

## 12. Environment Variables Reference

| Var                  | Purpose | Default |
|----------------------|---------|---------|
| `DOTA_COACH_MODEL`   | Model name. `gemini-*` routes to cloud, anything else to Ollama. | `qwen3.6:27b` |
| `OLLAMA_URL`         | Ollama chat endpoint | `http://localhost:11434/api/chat` |
| `GEMINI_API_KEY`     | Required when model name starts with `gemini-` | (unset) |
| `GEMINI_URL`         | Gemini OpenAI-compat endpoint (rarely overridden) | Google's default |
| `DOTA2_GSI_TOKEN`    | Must match the cfg file's token | `my_super_secret_token_123!` |
| `PIPER_MODEL`        | Path to a different Piper voice | `models/en_US-amy-medium.onnx` |
| `TTS_DISABLED`       | Set to `1` to mute speech (text only) | (unset) |

---

## 13. Troubleshooting

**`[coach] model: <something I didn't pick>`**
You used `set DOTA_COACH_MODEL=...` (cmd.exe). Use `$env:DOTA_COACH_MODEL = "..."` in PowerShell.

**`[coach] empty content from <model>` or `(error: GEMINI_API_KEY not set)`**
For Ollama: the model returned empty content — common with thinking models (gpt-oss). Switch model or increase `num_predict` in `_call_ollama`.
For Gemini: set `$env:GEMINI_API_KEY` before launching.

**`HTTP 400 / 401 / 403` from Gemini**
- 401/403: invalid or expired API key. Check at https://aistudio.google.com/apikey.
- 400: model name not recognized — try `gemini-2.5-flash` exactly, or check available models with `curl https://generativelanguage.googleapis.com/v1beta/models?key=$env:GEMINI_API_KEY`.

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
- Voice files missing — see section 6.1
- `TTS_DISABLED=1` env var still set
- Windows audio device routed wrong — check the system tray volume mixer

**Tips reference wrong heroes / wrong abilities**
That's a model quality limit. Cloud models (gemini-2.5-pro, gemini-2.5-flash) and large local models (qwen 27b) hallucinate less than smaller ones (gemma 4b). Ability descriptions are now injected into the prompt to mitigate this for smaller local models.

**Werkzeug spamming `200 -` lines in the log**
Already silenced in `logger.py` (`werkzeug` logger set to WARNING). If you still see them, check that `setup_logging()` is called before `from coach import Coach` in `main.py`.

---

## 14. File Layout

```
dota_coach/
├── main.py              # Flask entry point + per-tick orchestration
├── coach.py             # LLM caller (Ollama + Gemini routing), prompt builder, dedup, freshness check
├── events.py            # EventDiffer — turns ticks into typed events
├── extract_info.py      # Raw GSI JSON → flat summary dict
├── constants.py         # Loads OpenDota items/heroes/abilities; exposes desc + dname helpers
├── tts.py               # Piper voice synthesis + winsound playback
├── logger.py            # Tee'd stdout/stderr → console + per-match log
├── data/
│   ├── items.json       # OpenDota dotaconstants
│   ├── heroes.json
│   └── abilities.json   # Includes ability descriptions injected into the prompt
├── models/
│   ├── en_US-amy-medium.onnx       # Piper voice
│   └── en_US-amy-medium.onnx.json
├── logs/                # <matchid>.log per game, session_<ts>.log before match start
└── .venv/
```

---

## 15. Next Steps After First Run

After your first real match, look at the log file and check for:

- **Tip rate**: Aim for ~1.5–2.5 tips/min. Adjust `MIN_INTERVAL_SEC` and `PER_TRIGGER_COOLDOWN_SEC` if too noisy or too quiet.
- **`[coach] dropped duplicate tip`**: Healthy if 5–25% of attempts. If higher, the model is repeating itself — try a bigger model or raise `RECENT_TIPS_MEMORY`.
- **`[coach] dropped stale … tip`**: Indicates LLM latency exceeds the action window. Try a faster/cloud model or accept it as a correctness safety net.
- **Hallucinated abilities/items**: Compare the tip's named ability vs. your hero's actual kit. If a small local model is misusing abilities, try `gemini-2.5-flash` for a noticeable jump in correctness.
