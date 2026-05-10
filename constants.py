import json
import os

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _load(name):
    path = os.path.join(_DATA_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_items = _load("items")
_heroes = _load("heroes")
_abilities = _load("abilities")

ITEM_BY_ID = {v["id"]: k for k, v in _items.items() if "id" in v}
ITEM_DNAME_BY_ID = {v["id"]: v.get("dname", k) for k, v in _items.items() if "id" in v}

HERO_BY_ID = {
    int(k): v["name"].replace("npc_dota_hero_", "")
    for k, v in _heroes.items()
    if "name" in v
}
HERO_DNAME_BY_ID = {
    int(k): v.get("localized_name", v["name"])
    for k, v in _heroes.items()
    if "name" in v
}

# hero short name (e.g. "sniper") -> full info dict
HERO_BY_SHORT_NAME = {
    v["name"].replace("npc_dota_hero_", ""): {
        "dname": v.get("localized_name", v["name"]),
        "primary_attr": v.get("primary_attr"),
        "roles": v.get("roles", []) or [],
    }
    for v in _heroes.values()
    if isinstance(v, dict) and "name" in v
}


def item_name(item_id):
    return ITEM_BY_ID.get(item_id, f"item_{item_id}")


def hero_name(hero_id):
    return HERO_BY_ID.get(hero_id, f"hero_{hero_id}")


def item_dname(name):
    v = _items.get(name)
    if isinstance(v, dict) and v.get("dname"):
        return v["dname"]
    return name


def ability_dname(name):
    v = _abilities.get(name)
    if isinstance(v, dict) and v.get("dname"):
        return v["dname"]
    # fallback: drop a hero-name prefix if the ability name contains an
    # underscore and titlecase the rest (best-effort, not perfect)
    cleaned = name
    if "_" in cleaned:
        cleaned = cleaned.split("_", 1)[1] if cleaned.count("_") > 1 else cleaned
    return cleaned.replace("_", " ").title()


def ability_desc(name, max_len=180):
    """Short one-line description of an ability for prompt injection.
    Returns the first sentence of the OpenDota desc, truncated to max_len.
    """
    v = _abilities.get(name)
    if not isinstance(v, dict):
        return ""
    desc = (v.get("desc") or "").strip()
    if not desc:
        return ""
    # First sentence: cut at first newline or period+space.
    cut = len(desc)
    for sep in ("\n", ". "):
        i = desc.find(sep)
        if 0 <= i < cut:
            cut = i
    one_line = desc[:cut].strip().rstrip(".")
    if len(one_line) > max_len:
        one_line = one_line[: max_len - 1].rstrip() + "…"
    return one_line


# Lowercase set of all item display names — used to detect which items
# a coach tip is recommending (for per-match dedup).
ALL_ITEM_DNAMES = {
    v["dname"].lower()
    for v in _items.values()
    if isinstance(v, dict) and v.get("dname")
}


def hero_info(hero_short_name):
    return HERO_BY_SHORT_NAME.get(hero_short_name)


def slot_side(slot, my_player_slot, my_team):
    """Map a 0–9 player slot to one of: 'self', 'ally', 'enemy', 'unknown'."""
    if slot is None or slot < 0:
        return "unknown"
    if slot == my_player_slot:
        return "self"
    my_side_slots = range(0, 5) if my_team == "radiant" else range(5, 10)
    if slot in my_side_slots:
        return "ally"
    return "enemy"
