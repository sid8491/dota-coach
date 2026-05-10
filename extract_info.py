import json


def safe_get(d, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def clean_hero(name):
    return (name or "").replace("npc_dota_hero_", "")


def clean_item(name):
    return (name or "").replace("item_", "")


_KEEP_MINIMAP_IMAGES = {
    "minimap_herocircle",
    "minimap_herocircle_self",
    "minimap_enemyicon",
    "minimap_courier_flying",
    "minimap_courier_ground",
    "minimap_tower45",
    "minimap_tower90",
    "minimap_racks45",
    "minimap_racks90",
    "minimap_ancient",
}


def filter_minimap(minimap):
    if not isinstance(minimap, dict):
        return {}
    out = {}
    for k, v in minimap.items():
        if isinstance(v, dict) and v.get("image") in _KEEP_MINIMAP_IMAGES:
            out[k] = v
    return out


def summarize(data):
    minimap = data.get("minimap", {}) or {}

    self_team = safe_get(data, "player", "team_name", default="radiant")
    self_team_id = 2 if self_team == "radiant" else 3

    allies_by_hero = {}
    enemies_by_hero = {}
    enemy_buildings_visible = []
    for k, v in minimap.items():
        if not isinstance(v, dict):
            continue
        img = v.get("image", "")
        nm = v.get("name")
        if img == "minimap_enemyicon" and nm:
            hero = clean_hero(nm)
            # Dedup by hero name — the minimap can list illusions or duplicate
            # entries for the same hero. Keep first sighting.
            if hero not in enemies_by_hero:
                enemies_by_hero[hero] = {
                    "hero": hero,
                    "pos": (v.get("xpos"), v.get("ypos")),
                    "yaw": v.get("yaw"),
                }
        elif img == "minimap_herocircle" and nm:
            hero = clean_hero(nm)
            if hero not in allies_by_hero:
                allies_by_hero[hero] = {
                    "hero": hero,
                    "pos": (v.get("xpos"), v.get("ypos")),
                }
        elif img.startswith(("minimap_tower", "minimap_racks", "minimap_ancient")):
            if v.get("team") and v.get("team") != self_team_id:
                unit = v.get("unitname")
                if unit:
                    enemy_buildings_visible.append(unit)

    allies = list(allies_by_hero.values())
    enemies = list(enemies_by_hero.values())

    items = []
    items_obj = data.get("items", {}) or {}
    if isinstance(items_obj, dict):
        for slot, v in items_obj.items():
            if not isinstance(v, dict):
                continue
            nm = v.get("name")
            if not nm or nm == "empty":
                continue
            items.append(
                {
                    "slot": slot,
                    "name": clean_item(nm),
                    "cooldown": v.get("cooldown", 0),
                    "charges": v.get("charges"),
                }
            )

    abilities = []
    ab_obj = data.get("abilities", {}) or {}
    if isinstance(ab_obj, dict):
        for slot, v in ab_obj.items():
            if not isinstance(v, dict) or not v.get("name"):
                continue
            abilities.append(
                {
                    "slot": slot,
                    "name": v["name"],
                    "level": v.get("level", 0),
                    "cooldown": v.get("cooldown", 0),
                    "ultimate": v.get("ultimate", False),
                    "can_cast": v.get("can_cast", False),
                    "passive": v.get("passive", False),
                }
            )

    hero = data.get("hero", {}) or {}
    status_flags = [
        f
        for f in (
            "silenced",
            "stunned",
            "disarmed",
            "magicimmune",
            "hexed",
            "muted",
            "break",
            "smoked",
        )
        if hero.get(f)
    ]
    talents = [i for i in range(1, 9) if hero.get(f"talent_{i}")]

    our_buildings = safe_get(data, "buildings", self_team, default={}) or {}

    return {
        "matchid": safe_get(data, "map", "matchid"),
        "clock": safe_get(data, "map", "clock_time", default=0),
        "game_time": safe_get(data, "map", "game_time", default=0),
        "daytime": safe_get(data, "map", "daytime", default=True),
        "game_state": safe_get(data, "map", "game_state"),
        "paused": safe_get(data, "map", "paused", default=False),
        "score": {
            "radiant": safe_get(data, "map", "radiant_score", default=0),
            "dire": safe_get(data, "map", "dire_score", default=0),
        },
        "self": {
            "hero": clean_hero(hero.get("name")),
            "facet": hero.get("facet"),
            "level": hero.get("level", 0),
            "xp": hero.get("xp", 0),
            "alive": hero.get("alive", True),
            "respawn": hero.get("respawn_seconds", 0),
            "buyback_cost": hero.get("buyback_cost", 0),
            "buyback_cd": hero.get("buyback_cooldown", 0),
            "hp_pct": hero.get("health_percent", 100),
            "mp_pct": hero.get("mana_percent", 100),
            "hp": hero.get("health", 0),
            "mp": hero.get("mana", 0),
            "pos": (hero.get("xpos"), hero.get("ypos")),
            "status": status_flags,
            "talents": talents,
            "aghs": hero.get("aghanims_scepter", False),
            "shard": hero.get("aghanims_shard", False),
        },
        "player": {
            "name": safe_get(data, "player", "name"),
            "kills": safe_get(data, "player", "kills", default=0),
            "deaths": safe_get(data, "player", "deaths", default=0),
            "assists": safe_get(data, "player", "assists", default=0),
            "last_hits": safe_get(data, "player", "last_hits", default=0),
            "denies": safe_get(data, "player", "denies", default=0),
            "gold": safe_get(data, "player", "gold", default=0),
            "gpm": safe_get(data, "player", "gpm", default=0),
            "xpm": safe_get(data, "player", "xpm", default=0),
            "team": self_team,
            "player_slot": safe_get(data, "player", "player_slot", default=0),
        },
        "items": items,
        "abilities": abilities,
        "allies": allies,
        "enemies": enemies,
        "enemies_seen_count": len(enemies),
        "enemies_missing": max(0, 5 - len(enemies)),
        "our_buildings": list(our_buildings.keys()),
        "enemy_buildings_visible": enemy_buildings_visible,
        "roshan": data.get("roshan", {}) or {},
        "events": data.get("events", []) or [],
        "couriers": data.get("couriers", {}) or {},
        "neutralitems": data.get("neutralitems", {}) or {},
    }


def print_snapshot(s):
    h = s["self"]
    p = s["player"]
    mins = s["clock"] // 60
    secs = s["clock"] % 60
    print("--- Game ---")
    print(
        f"Clock: {mins:02d}:{secs:02d}  "
        f"Score R{s['score']['radiant']}-D{s['score']['dire']}  "
        f"{'Day' if s['daytime'] else 'Night'}"
    )
    print(
        f"Hero: {h['hero']} (lvl {h['level']})  "
        f"HP {h['hp_pct']}%  MP {h['mp_pct']}%"
    )
    print(
        f"K/D/A: {p['kills']}/{p['deaths']}/{p['assists']}  "
        f"CS: {p['last_hits']}/{p['denies']}  "
        f"Gold: {p['gold']} (GPM {p['gpm']}, XPM {p['xpm']})"
    )
    if h["status"]:
        print(f"Status: {', '.join(h['status'])}")
    if h["aghs"] or h["shard"]:
        upgrades = []
        if h["aghs"]:
            upgrades.append("aghs")
        if h["shard"]:
            upgrades.append("shard")
        print(f"Upgrades: {', '.join(upgrades)}")
    if s["items"]:
        print(f"Items: {', '.join(i['name'] for i in s['items'])}")
    visible = ", ".join(e["hero"] for e in s["enemies"]) or "none"
    print(
        f"Visible enemies ({s['enemies_seen_count']}/5): {visible}  "
        f"Missing: {s['enemies_missing']}"
    )
    if s["roshan"]:
        print(f"Roshan: {s['roshan']}")
    print("*" * 80)


def get_game_info(data):
    s = summarize(data)
    print_snapshot(s)
    return s


if __name__ == "__main__":
    try:
        with open("data_less_kill2.json", "r") as f:
            game_data = json.load(f)
        get_game_info(game_data)
    except FileNotFoundError:
        print("Error: data_less_kill2.json not found.")
    except json.JSONDecodeError:
        print("Error: Could not decode JSON from the file.")
