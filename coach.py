import json
import os
import re
import threading
import urllib.error
import urllib.request
from collections import deque

from constants import (
    ability_desc,
    ability_dname,
    ALL_ITEM_DNAMES,
    hero_info,
    item_dname,
)
from tts import TTS

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
GEMINI_URL = os.getenv(
    "GEMINI_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("DOTA_COACH_MODEL", "qwen3.6:27b")
MIN_INTERVAL_SEC = 8
MAX_RECENT_EVENTS = 8
RECENT_TIPS_MEMORY = 8
PER_TRIGGER_COOLDOWN_SEC = 30

SYSTEM_PROMPT = """You are a concise in-game Dota 2 coach.
The user is playing right now and just hit a moment that warrants advice.
Reply with ONE actionable tip, one or two short sentences. No greeting, no padding.
If you have nothing useful to add, reply just: OK

What you know:
- The user's own hero, items, HP, mana, abilities, gold, and position are fully visible.
- Enemy hero items, HP, mana, levels, and gold are NEVER visible to GSI.
- Enemy positions are visible only when an enemy is in your team's vision.
- Ally hero items, HP, levels are NOT visible. Only positions when in vision.
- ONLY suggest using abilities and items that appear in the user message's "Your abilities" / "Your items" sections. Do not invent abilities or items the player doesn't have.

Trigger-type focus:
- death: respawn-window decision (buyback?, rotation, farm path).
- low_hp / low_mp: immediate survival action — TP out, salve, escape ability.
- ult_ready: where/whom to use it on, given visible enemies and missing count.
- level_up (milestone): what timing this enables (lvl 6 first ult, 12/15/18 talents).
- talent: how to leverage it in the next 30 seconds.
- enemies_missing: defensive — likely smoke gank or rosh; ward placement, group up, don't farm exposed.
- periodic_check: recommend the NEXT item to buy given current items, gold, and enemy lineup. OR a strategic call (push, defend, farm, smoke). Be concrete.
- our_building_fell: defend or counter-push decision.
- roshan_killed / aegis_picked_up: rosh management, smoke gank, push timing.
- aghs / shard: how the upgrade changes your kit, what fight to look for.

GOOD tip style — be specific to THIS state. Name a target, a window, an item synergy, a spatial choice. AVOID empty phrases like "save it for the right moment", "don't waste your ult", "use your team's momentum".

Examples:
  Trigger: ult_ready (Sniper, lvl 12, 1 visible enemy alone in their jungle, 4 missing)
  Bad: "Save your ultimate for a key fight."
  Good: "Snipe the visible enemy from fog with Assassinate — they're alone and you have 1700 range. Stay back."

  Trigger: death (Earth Spirit, 0/2 KDA, 800 gold, 8 min)
  Bad: "Don't overextend, group with your team."
  Good: "Stack Dire small camp at 8:53, then take a wave bot — you need farm before forcing fights."

  Trigger: low_hp (Hoodwink, 22% HP, 3 enemies visible nearby, Maelstrom + TP scroll)
  Bad: "Use Town Portal Scroll to reposition safely."
  Good: "TP out now into trees — they're all visible on you, they'll commit."

  Trigger: periodic_check (Sniper, lvl 12, items: Mjollnir + Dragon Lance + Boots, gold 2200, enemy lineup includes Lion, Razor)
  Bad: "Keep farming and look for fights."
  Good: "Save up for Hurricane Pike — Lion's hex and Razor's Plasma Field both want you in close, Pike kicks you out."
"""

TRIGGER_TYPES = {
    "death", "low_hp", "low_mp", "ult_ready",
    "aghs", "shard", "level_up", "talent",
    "roshan_killed", "aegis_picked_up",
    "our_building_fell",
    "first_blood", "smoked",
    "enemies_missing", "periodic_check",
    "kill", "team_kill",
}

LEVEL_UP_MILESTONES = {2, 6, 10, 12, 15, 18, 20, 25}

# Freshness thresholds — applied to the LATEST summary at LLM-response time.
# If the original trigger no longer holds (player respawned, HP/MP recovered,
# enemies came back into vision), the tip is dropped as stale.
FRESH_HP_RECOVERED_PCT = 60
FRESH_MP_RECOVERED_PCT = 50
FRESH_MISSING_ENEMIES_MIN = 2


def _pick_trigger(events):
    for e in events:
        if e["type"] not in TRIGGER_TYPES:
            continue
        if e["type"] == "level_up" and e.get("level") not in LEVEL_UP_MILESTONES:
            continue
        return f"{e['type']}: {e['msg']}"
    return None


def _format_clock(clock):
    mins = clock // 60
    secs = abs(clock) % 60
    sign = "-" if clock < 0 else ""
    return f"{sign}{abs(mins):02d}:{secs:02d}"


_WORD_RE = re.compile(r"\w+")


def _classify_lane(x, y):
    # Approximate Dota 2 lane regions. Mid runs along the y=x diagonal;
    # top/bot are L-shaped corridors along the map edges.
    if x is None or y is None:
        return "?"
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return "?"
    if abs(y - x) < 1500:
        return "mid"
    if y > x:
        if x < -2500 or y > 2500:
            return "top"
        return "jungle"
    if y < -2500 or x > 2500:
        return "bot"
    return "jungle"


def _is_too_similar(tip, recent_tips, threshold=0.65):
    new_words = set(_WORD_RE.findall(tip.lower()))
    if not new_words:
        return False
    for prev in recent_tips:
        prev_words = set(_WORD_RE.findall(prev.lower()))
        if not prev_words:
            continue
        sim = len(new_words & prev_words) / max(len(new_words), len(prev_words))
        if sim >= threshold:
            return True
    return False


def _game_phase(clock):
    if clock < 0:
        return "PRE-GAME (horn hasn't blown; heroes at fountain; no farming yet; finalize starting items / wards / runes)"
    if clock < 600:
        return "EARLY GAME / LANING (focus: CS, deny, harass; lvl 1-6 power; first item piece)"
    if clock < 1200:
        return "MID GAME (focus: rotations, towers, smoke ganks, secure runes/rosh setup, push first-tier objectives)"
    if clock < 1800:
        return "MID-LATE (focus: aegis windows, T2/T3 sieges, 5-man fights, key item timings)"
    return "LATE GAME (focus: 5-man only, high ground, buyback management, end the game or win the next fight)"


_ATTR_LABEL = {"agi": "agility", "str": "strength", "int": "intelligence", "all": "universal"}


def _format_hero_line(self_obj):
    name = self_obj["hero"]
    info = hero_info(name)
    if not info:
        return name or "unknown"
    attr = _ATTR_LABEL.get(info.get("primary_attr"), info.get("primary_attr") or "?")
    roles = ", ".join(info.get("roles") or []) or "—"
    return f"{info['dname']} ({attr}; roles: {roles})"


def _format_abilities(abilities):
    lines = []
    for a in abilities:
        name = a.get("name") or ""
        if not name or name.startswith("plus_"):
            continue
        dname = ability_dname(name)
        bits = [f"lvl {a.get('level', 0)}"]
        if a.get("ultimate"):
            bits.append("ult")
        if a.get("passive"):
            bits.append("passive")
        else:
            cd = a.get("cooldown", 0) or 0
            bits.append("off cd" if cd == 0 else f"ON CD {cd}s")
        desc = ability_desc(name)
        head = f"  - {dname} ({', '.join(bits)})"
        lines.append(f"{head}: {desc}" if desc else head)
    return "\n".join(lines) if lines else "  (none)"


def _extract_recommended_items(tip):
    """Return the set of item display names this tip recommends.
    Matches case-insensitively against the full OpenDota item display-name set.
    """
    text = tip.lower()
    found = set()
    for dname in ALL_ITEM_DNAMES:
        # Word-boundary-ish: surround with spaces to avoid 'tango' matching 'pentangle'.
        if f" {dname} " in f" {text} " or f" {dname}." in text or f" {dname}," in text:
            found.add(dname)
    return found


def _build_prompt(summary, events, trigger, recent_tips=None, recommended_items=None):
    s = summary
    h = s["self"]
    p = s["player"]
    items = ", ".join(item_dname(i["name"]) for i in s["items"]) or "none"
    self_pos = h.get("pos") or (None, None)
    self_lane = _classify_lane(*self_pos)
    ally_lines = (
        "\n".join(
            f"  - {a['hero']} ({_classify_lane(*(a.get('pos') or (None, None)))})"
            for a in s["allies"]
        )
        or "  (none in vision)"
    )
    enemy_lines = (
        "\n".join(
            f"  - {e['hero']} ({_classify_lane(*(e.get('pos') or (None, None)))})"
            for e in s["enemies"]
        )
        or "  (none visible)"
    )
    enemy_lineup = ", ".join(s.get("enemy_lineup") or []) or "unknown"
    status = ", ".join(h["status"]) or "ok"
    recent = "\n".join(
        f"  - {e['type']}: {e['msg']}" for e in events[-MAX_RECENT_EVENTS:]
    ) or "  (none)"
    abilities_block = _format_abilities(s.get("abilities", []))
    facet = h.get("facet")
    tips_block = ""
    if recent_tips:
        tips_lines = "\n".join(f"  - {t}" for t in recent_tips)
        tips_block = (
            f"\nRecent tips you already gave (DO NOT repeat or rephrase, "
            f"give a DIFFERENT angle):\n{tips_lines}\n"
        )
    items_already_block = ""
    if recommended_items:
        items_already_block = (
            f"\nItems you have ALREADY recommended this match "
            f"(DO NOT recommend any of these again — pick a different next item):\n"
            f"  {', '.join(sorted(recommended_items))}\n"
        )
    trigger_tail = ""
    trigger_type = trigger.split(":", 1)[0].strip()
    if trigger_type == "periodic_check":
        trigger_tail = (
            "\nFor this periodic check-in, give EXACTLY ONE of:\n"
            "(a) Item recommendation: 'Build [Item Name] next — [one-line reason "
            "tied to specific enemy heroes].' Pick from items not already in your "
            "inventory AND not in the already-recommended list.\n"
            "(b) Specific objective call: 'Push [lane] T1 now — [reason].' / "
            "'Smoke for rosh — [reason].' / 'Defend [lane] — [reason].'\n"
            "Reference your actual gold and the enemy lineup. NOT generic advice.\n"
        )
    return (
        f"Game context:\n"
        f"  phase: {_game_phase(s['clock'])}\n"
        f"  hero: {_format_hero_line(h)}\n"
        f"  position: {self_lane} lane\n"
        f"  facet: {facet if facet is not None else '?'}\n"
        f"  clock {_format_clock(s['clock'])}, score R{s['score']['radiant']}-D{s['score']['dire']}, "
        f"{'day' if s['daytime'] else 'night'}\n"
        f"  level {h['level']}, HP {h['hp_pct']}%, MP {h['mp_pct']}%, status: {status}\n"
        f"  K/D/A {p['kills']}/{p['deaths']}/{p['assists']}, CS {p['last_hits']}/{p['denies']}, "
        f"gold {p['gold']}, GPM {p['gpm']}\n"
        f"\n"
        f"Your abilities:\n{abilities_block}\n"
        f"\n"
        f"Your items: {items}\n"
        f"\n"
        f"Allies in vision (lane shown):\n{ally_lines}\n"
        f"\n"
        f"Enemy lineup (heroes scouted so far): {enemy_lineup}\n"
        f"Visible enemies ({len(s['enemies'])}/5), {s['enemies_missing']} missing:\n"
        f"{enemy_lines}\n"
        f"\n"
        f"Recent events:\n{recent}\n"
        f"{tips_block}"
        f"{items_already_block}"
        f"\n"
        f"Trigger: {trigger}\n"
        f"{trigger_tail}"
    )


def _is_gemini_model(model):
    return model.startswith("gemini-") or model.startswith("models/gemini-")


def _call_llm(model, user_msg, timeout=120):
    """Route to the right provider based on model name. Returns (content, debug)."""
    if _is_gemini_model(model):
        if not GEMINI_API_KEY:
            return "", {"error": "GEMINI_API_KEY not set"}
        return _call_openai_compatible(
            GEMINI_URL,
            {"Authorization": f"Bearer {GEMINI_API_KEY}"},
            model,
            user_msg,
            timeout,
        )
    return _call_ollama(model, user_msg, timeout)


def _call_ollama(model, user_msg, timeout=120):
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.4, "num_predict": 400},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    msg = data.get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        # Some thinking models put output in `thinking` when `think:false` is
        # ignored. Report what we got so silent drops are visible.
        thinking = (msg.get("thinking") or "").strip()
        done_reason = data.get("done_reason")
        return "", {"thinking": thinking, "done_reason": done_reason, "raw_keys": list(msg.keys())}
    return content, None


def _call_openai_compatible(url, headers, model, user_msg, timeout=120):
    """Call any OpenAI-compatible chat-completions endpoint (Gemini, OpenRouter, OpenAI)."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.4,
            "max_tokens": 400,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:300]
        return "", {"http_status": ex.code, "body": body}
    choices = data.get("choices") or []
    if not choices:
        return "", {"raw_keys": list(data.keys()), "preview": str(data)[:200]}
    msg = choices[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        finish = choices[0].get("finish_reason")
        return "", {"finish_reason": finish, "raw_keys": list(msg.keys())}
    return content, None


class Coach:
    def __init__(self, model=MODEL, min_interval_sec=MIN_INTERVAL_SEC, tts=None):
        self.model = model
        self.min_interval = min_interval_sec
        self.last_call_clock = -10**9
        self._inflight = False
        self._lock = threading.Lock()
        self.tts = tts if tts is not None else TTS()
        self.recent_tips = deque(maxlen=RECENT_TIPS_MEMORY)
        self.last_trigger_clock = {}
        self.latest_summary = None
        self.current_matchid = None
        self.recommended_items = set()
        endpoint = GEMINI_URL if _is_gemini_model(self.model) else OLLAMA_URL
        print(f"[coach] model: {self.model} via {endpoint}", flush=True)

    def maybe_advise(self, summary, events):
        # Always refresh the latest snapshot — used for the freshness check
        # when an in-flight LLM response returns.
        self.latest_summary = summary
        # Reset per-match item dedup when match changes.
        matchid = summary.get("matchid")
        if matchid and matchid != self.current_matchid:
            self.current_matchid = matchid
            self.recommended_items = set()
        if not events:
            return
        trigger = _pick_trigger(events)
        if trigger is None:
            return
        trigger_type = trigger.split(":", 1)[0].strip()
        clk = summary.get("clock", 0)
        with self._lock:
            if self._inflight:
                return
            if clk - self.last_call_clock < self.min_interval:
                return
            last_for_type = self.last_trigger_clock.get(trigger_type, -10**9)
            if clk - last_for_type < PER_TRIGGER_COOLDOWN_SEC:
                return
            self.last_call_clock = clk
            self.last_trigger_clock[trigger_type] = clk
            self._inflight = True
        threading.Thread(
            target=self._run,
            args=(summary, list(events), trigger),
            daemon=True,
        ).start()

    def _stale_reason(self, trigger_type):
        """Return a short reason string if the trigger no longer holds, else None."""
        latest = self.latest_summary
        if latest is None:
            return None
        s = latest.get("self") or {}
        if trigger_type == "death":
            if s.get("alive", True):
                return f"player respawned (alive again)"
        elif trigger_type == "low_hp":
            hp = s.get("hp_pct", 100)
            if s.get("alive", True) and hp >= FRESH_HP_RECOVERED_PCT:
                return f"HP recovered to {hp}%"
        elif trigger_type == "low_mp":
            mp = s.get("mp_pct", 100)
            if s.get("alive", True) and mp >= FRESH_MP_RECOVERED_PCT:
                return f"MP recovered to {mp}%"
        elif trigger_type == "enemies_missing":
            miss = latest.get("enemies_missing", 0)
            if miss < FRESH_MISSING_ENEMIES_MIN:
                return f"only {miss} enemies missing now"
        return None

    def _run(self, summary, events, trigger):
        try:
            prompt = _build_prompt(
                summary,
                events,
                trigger,
                recent_tips=list(self.recent_tips),
                recommended_items=set(self.recommended_items),
            )
            trigger_type = trigger.split(":", 1)[0].strip()
            tip, debug = _call_llm(self.model, prompt)
            if not tip:
                thinking_excerpt = (debug or {}).get("thinking", "")[:120]
                done_reason = (debug or {}).get("done_reason")
                print(
                    f"[coach] empty content from {self.model} "
                    f"(done_reason={done_reason}, thinking_excerpt={thinking_excerpt!r})",
                    flush=True,
                )
                return
            if tip.upper() == "OK":
                print(f"[coach] {self.model} declined (OK)", flush=True)
                return
            stale = self._stale_reason(trigger_type)
            if stale:
                print(
                    f"[coach] dropped stale {trigger_type} tip ({stale}): "
                    f"{tip[:70]}...",
                    flush=True,
                )
                return
            if _is_too_similar(tip, list(self.recent_tips)):
                print(
                    f"[coach] dropped duplicate tip: {tip[:70]}...",
                    flush=True,
                )
                return
            print(f"[COACH][{self.model}] {tip}", flush=True)
            self.recent_tips.append(tip)
            new_items = _extract_recommended_items(tip)
            if new_items:
                self.recommended_items.update(new_items)
            self.tts.speak(tip)
        except urllib.error.URLError as ex:
            print(f"[coach] ollama unreachable at {OLLAMA_URL}: {ex}", flush=True)
        except Exception as ex:
            print(f"[coach] error: {ex}", flush=True)
        finally:
            with self._lock:
                self._inflight = False
