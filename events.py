import json

from constants import item_name, slot_side

LOW_HP_PCT = 30
LOW_MP_PCT = 20
STATUS_DEBOUNCE_SEC = 2
VISION_DEBOUNCE_SEC = 3
MISSING_ENEMIES_THRESHOLD = 2
MISSING_ENEMIES_SUSTAIN_SEC = 5
PERIODIC_CHECK_INTERVAL_SEC = 120

ACTIVE_GAME_STATES = {
    "DOTA_GAMERULES_STATE_PRE_GAME",
    "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
}

EQUIPPED_SLOT_PREFIXES = ("slot", "teleport", "neutral")

EMITTED_SERVER_EVENT_CAP = 500
EMITTED_SERVER_EVENT_TRIM = 300


def _hero_set(heroes):
    return {h["hero"] for h in heroes}


def _equipped_items(items):
    return {
        i["name"]
        for i in items
        if any(i["slot"].startswith(p) for p in EQUIPPED_SLOT_PREFIXES)
    }


def _server_event_key(e):
    if isinstance(e, dict):
        return tuple(sorted((k, str(v)) for k, v in e.items()))
    return ("raw", str(e))


class EventDiffer:
    def __init__(self):
        self.prev = None
        self.prev_active = False
        self.pending_status = {}
        self.pending_vision = {}
        self.committed_status = set()
        self.committed_vision = set()
        self.emitted_server_events = set()
        self.matchid = None
        self.slot_to_hero = {}
        self.seen_ally_heroes = set()
        self.seen_enemy_heroes = set()
        self.missing_high_since_clock = None
        self.missing_warning_emitted = False
        self.last_periodic_check_clock = None

    def _reset(self):
        self.prev = None
        self.prev_active = False
        self.pending_status = {}
        self.pending_vision = {}
        self.committed_status = set()
        self.committed_vision = set()
        self.emitted_server_events = set()
        self.slot_to_hero = {}
        self.seen_ally_heroes = set()
        self.seen_enemy_heroes = set()
        self.missing_high_since_clock = None
        self.missing_warning_emitted = False
        self.last_periodic_check_clock = None

    def _format_slot(self, slot):
        if slot is None:
            return "slot ?"
        side = "?"
        if self.prev is not None:
            side = slot_side(
                slot,
                self.prev["player"].get("player_slot", 0),
                self.prev["player"]["team"],
            )
        hero = self.slot_to_hero.get(slot)
        if hero:
            return f"{hero} ({side})"
        return f"slot {slot} ({side})"

    @staticmethod
    def _ev(clk, kind, msg, **extra):
        return {"clock": clk, "type": kind, "msg": msg, **extra}

    def process(self, curr):
        events = []

        new_match = curr.get("matchid")
        if new_match and self.matchid and new_match != self.matchid:
            self._reset()
        self.matchid = new_match or self.matchid

        active = curr.get("game_state") in ACTIVE_GAME_STATES

        my_slot = curr["player"].get("player_slot", 0)
        my_team = curr["player"]["team"]
        if curr["self"].get("hero"):
            self.slot_to_hero[my_slot] = curr["self"]["hero"]

        prev_ally_set = (
            _hero_set(self.prev["allies"]) if self.prev else set()
        )
        prev_enemy_set = (
            _hero_set(self.prev["enemies"]) if self.prev else set()
        )
        curr_ally_set = _hero_set(curr["allies"])
        curr_enemy_set = _hero_set(curr["enemies"])
        self.seen_ally_heroes |= curr_ally_set
        self.seen_enemy_heroes |= curr_enemy_set
        if curr["self"].get("hero"):
            self.seen_ally_heroes.add(curr["self"]["hero"])

        if self.prev and self.prev["game_state"] != curr["game_state"]:
            events.append(
                self._ev(
                    curr["clock"], "game_state", f"Game state: {curr['game_state']}"
                )
            )

        if self.prev and self.prev_active and active:
            events.extend(self._diff_kda(curr))
            events.extend(self._diff_score(curr))
            events.extend(self._diff_self(curr))
            events.extend(self._diff_items(curr))
            events.extend(self._diff_abilities(curr))
            events.extend(self._diff_buildings(curr))
            events.extend(self._diff_misc(curr))
            events.extend(self._diff_status_debounced(curr))
            events.extend(self._diff_vision_debounced(curr))
            events.extend(
                self._diff_server_events(
                    curr, prev_ally_set, prev_enemy_set, curr_ally_set, curr_enemy_set
                )
            )
            events.extend(self._derived_triggers(curr))

        self._infer_by_elimination(my_slot, my_team)

        if active and not self.prev_active:
            self.committed_status = set(curr["self"]["status"])
            self.committed_vision = _hero_set(curr["enemies"])
            self.pending_status.clear()
            self.pending_vision.clear()

        self.prev = curr
        self.prev_active = active
        return events

    def _derived_triggers(self, curr):
        out = []
        clk = curr["clock"]
        # Skip both derived triggers in pre-game (heroes at fountain — no
        # actionable enemy info, periodic check is meaningless).
        if clk < 0:
            return out
        miss = curr.get("enemies_missing", 0) or 0
        if miss >= MISSING_ENEMIES_THRESHOLD:
            if self.missing_high_since_clock is None:
                self.missing_high_since_clock = clk
                self.missing_warning_emitted = False
            elif (
                clk - self.missing_high_since_clock >= MISSING_ENEMIES_SUSTAIN_SEC
                and not self.missing_warning_emitted
            ):
                out.append(
                    self._ev(
                        clk,
                        "enemies_missing",
                        f"{miss} enemies off the map",
                        missing_count=miss,
                    )
                )
                self.missing_warning_emitted = True
        else:
            self.missing_high_since_clock = None
            self.missing_warning_emitted = False

        if self.last_periodic_check_clock is None:
            self.last_periodic_check_clock = clk
        elif clk - self.last_periodic_check_clock >= PERIODIC_CHECK_INTERVAL_SEC:
            out.append(self._ev(clk, "periodic_check", "periodic check-in"))
            self.last_periodic_check_clock = clk
        return out

    def _slots_for_side(self, side, my_slot, my_team):
        # side relative to user: 'ally' or 'enemy'
        radiant = range(0, 5)
        dire = range(5, 10)
        my_side = radiant if my_team == "radiant" else dire
        their_side = dire if my_team == "radiant" else radiant
        if side == "ally":
            return [s for s in my_side if s != my_slot]
        return list(their_side)

    def _try_map_slot(self, slot, hero, my_slot, my_team):
        if slot is None or slot < 0 or hero is None:
            return
        side = slot_side(slot, my_slot, my_team)
        if side not in ("ally", "enemy"):
            return
        if slot in self.slot_to_hero:
            return
        if hero in self.slot_to_hero.values():
            return
        self.slot_to_hero[slot] = hero

    def _infer_by_elimination(self, my_slot, my_team):
        for side, seen in (
            ("ally", self.seen_ally_heroes),
            ("enemy", self.seen_enemy_heroes),
        ):
            if len(seen) < 5:
                continue
            slots = self._slots_for_side(side, my_slot, my_team)
            unmapped_slots = [s for s in slots if s not in self.slot_to_hero]
            mapped_heroes = {
                self.slot_to_hero[s] for s in slots if s in self.slot_to_hero
            }
            unmapped_heroes = seen - mapped_heroes
            if len(unmapped_slots) == 1 and len(unmapped_heroes) == 1:
                self.slot_to_hero[unmapped_slots[0]] = next(iter(unmapped_heroes))

    def _diff_kda(self, curr):
        out = []
        clk = curr["clock"]
        p, c = self.prev["player"], curr["player"]
        if c["kills"] > p["kills"]:
            out.append(self._ev(clk, "kill", f"You got a kill (total {c['kills']})"))
        if c["deaths"] > p["deaths"]:
            out.append(self._ev(clk, "death", f"You died (total {c['deaths']})"))
        if c["assists"] > p["assists"]:
            out.append(
                self._ev(clk, "assist", f"Assist credited (total {c['assists']})")
            )
        return out

    def _diff_score(self, curr):
        out = []
        clk = curr["clock"]
        for team in ("radiant", "dire"):
            if curr["score"][team] > self.prev["score"][team]:
                side = "Allies" if team == curr["player"]["team"] else "Enemies"
                out.append(
                    self._ev(
                        clk,
                        "team_kill",
                        f"{side} got a kill "
                        f"(R{curr['score']['radiant']}-D{curr['score']['dire']})",
                    )
                )
        return out

    def _diff_self(self, curr):
        out = []
        clk = curr["clock"]
        p, c = self.prev["self"], curr["self"]
        if c["level"] > p["level"] and p["level"] > 0:
            out.append(self._ev(clk, "level_up", f"Leveled to {c['level']}", level=c["level"]))
        if p["alive"] and not c["alive"]:
            out.append(self._ev(clk, "died", f"Hero died, respawn in {c['respawn']}s"))
        if not p["alive"] and c["alive"]:
            out.append(self._ev(clk, "respawn", "Hero respawned"))
        if c["hp_pct"] < LOW_HP_PCT <= p["hp_pct"]:
            out.append(self._ev(clk, "low_hp", f"HP at {c['hp_pct']}%"))
        if c["mp_pct"] < LOW_MP_PCT <= p["mp_pct"]:
            out.append(self._ev(clk, "low_mp", f"Mana at {c['mp_pct']}%"))
        if not p["aghs"] and c["aghs"]:
            out.append(self._ev(clk, "aghs", "Picked up Aghanim's Scepter"))
        if not p["shard"] and c["shard"]:
            out.append(self._ev(clk, "shard", "Picked up Aghanim's Shard"))
        for t in set(c["talents"]) - set(p["talents"]):
            out.append(self._ev(clk, "talent", f"Picked talent #{t}"))
        return out

    def _diff_items(self, curr):
        out = []
        clk = curr["clock"]
        prev_eq = _equipped_items(self.prev["items"])
        curr_eq = _equipped_items(curr["items"])
        for it in curr_eq - prev_eq:
            out.append(self._ev(clk, "item_added", f"Got item: {it}"))
        for it in prev_eq - curr_eq:
            out.append(self._ev(clk, "item_removed", f"Lost item: {it}"))
        return out

    def _diff_abilities(self, curr):
        out = []
        clk = curr["clock"]
        p_ult = {
            a["name"]: a["cooldown"] for a in self.prev["abilities"] if a["ultimate"]
        }
        c_ult = {a["name"]: a["cooldown"] for a in curr["abilities"] if a["ultimate"]}
        for nm, cd in c_ult.items():
            if cd == 0 and p_ult.get(nm, 0) > 0:
                out.append(self._ev(clk, "ult_ready", f"Ultimate {nm} is ready"))
        return out

    def _diff_buildings(self, curr):
        out = []
        clk = curr["clock"]
        for b in set(self.prev["our_buildings"]) - set(curr["our_buildings"]):
            out.append(self._ev(clk, "our_building_fell", f"We lost: {b}"))
        return out

    def _diff_misc(self, curr):
        out = []
        clk = curr["clock"]
        if self.prev["daytime"] != curr["daytime"]:
            out.append(
                self._ev(
                    clk,
                    "time_of_day",
                    "Night begins" if not curr["daytime"] else "Day begins",
                )
            )
        if self.prev["paused"] != curr["paused"]:
            out.append(
                self._ev(
                    clk,
                    "pause",
                    "Game paused" if curr["paused"] else "Game resumed",
                )
            )
        if json.dumps(self.prev["roshan"], sort_keys=True) != json.dumps(
            curr["roshan"], sort_keys=True
        ):
            out.append(self._ev(clk, "roshan", f"Roshan state: {curr['roshan']}"))
        return out

    def _diff_status_debounced(self, curr):
        return self._debounced_set_diff(
            clk=curr["clock"],
            observed=set(curr["self"]["status"]),
            committed=self.committed_status,
            pending=self.pending_status,
            threshold=STATUS_DEBOUNCE_SEC,
            on_label=lambda flag: ("status_on", f"You are now {flag}"),
            off_label=lambda flag: ("status_off", f"No longer {flag}"),
        )

    def _diff_vision_debounced(self, curr):
        out = []
        clk = curr["clock"]
        enemies_now = {e["hero"]: e for e in curr["enemies"]}
        observed = set(enemies_now.keys())
        committed = self.committed_vision
        pending = self.pending_vision

        all_keys = observed | committed | set(pending.keys())
        for hero in all_keys:
            target = hero in observed
            already = hero in committed
            if target == already:
                pending.pop(hero, None)
                continue
            cur_pending = pending.get(hero)
            if cur_pending is None or cur_pending[0] != target:
                pending[hero] = (target, clk)
                continue
            _, first_clk = cur_pending
            if clk - first_clk >= VISION_DEBOUNCE_SEC:
                if target:
                    pos = enemies_now[hero]["pos"]
                    out.append(
                        self._ev(
                            clk,
                            "enemy_seen",
                            f"Spotted: {hero} at ({pos[0]}, {pos[1]})",
                            hero=hero,
                            pos=pos,
                        )
                    )
                    committed.add(hero)
                else:
                    out.append(
                        self._ev(clk, "enemy_lost", f"Lost vision: {hero}", hero=hero)
                    )
                    committed.discard(hero)
                pending.pop(hero, None)
        return out

    def _debounced_set_diff(
        self, *, clk, observed, committed, pending, threshold, on_label, off_label
    ):
        out = []
        all_keys = observed | committed | set(pending.keys())
        for key in all_keys:
            target = key in observed
            already = key in committed
            if target == already:
                pending.pop(key, None)
                continue
            cur_pending = pending.get(key)
            if cur_pending is None or cur_pending[0] != target:
                pending[key] = (target, clk)
                continue
            _, first_clk = cur_pending
            if clk - first_clk >= threshold:
                kind, msg = on_label(key) if target else off_label(key)
                out.append(self._ev(clk, kind, msg))
                if target:
                    committed.add(key)
                else:
                    committed.discard(key)
                pending.pop(key, None)
        return out

    def _diff_server_events(
        self, curr, prev_ally_set, prev_enemy_set, curr_ally_set, curr_enemy_set
    ):
        out = []
        clk = curr["clock"]
        my_slot = curr["player"].get("player_slot", 0)
        my_team = curr["player"]["team"]
        ally_vanished = prev_ally_set - curr_ally_set
        enemy_vanished = prev_enemy_set - curr_enemy_set
        for e in curr["events"]:
            key = _server_event_key(e)
            if key in self.emitted_server_events:
                continue
            self.emitted_server_events.add(key)
            out.extend(
                self._parse_server_event(
                    e, clk, my_slot, my_team, ally_vanished, enemy_vanished
                )
            )
        if len(self.emitted_server_events) > EMITTED_SERVER_EVENT_CAP:
            self.emitted_server_events = set(
                list(self.emitted_server_events)[-EMITTED_SERVER_EVENT_TRIM:]
            )
        return out

    def _parse_server_event(
        self, e, clk, my_slot, my_team, ally_vanished, enemy_vanished
    ):
        if not isinstance(e, dict):
            return [self._ev(clk, "server_event", str(e), raw=e)]

        event_type = e.get("event_type")

        if event_type == "roshan_killed":
            killer_team = e.get("killed_by_team")
            killer = e.get("killer_player_id")
            side = "Allies" if killer_team == my_team else "Enemies"
            return [
                self._ev(
                    clk,
                    "roshan_killed",
                    f"{side} killed Roshan ({self._format_slot(killer)})",
                    killer_slot=killer,
                    killed_by_team=killer_team,
                )
            ]

        if event_type == "aegis_picked_up":
            pid = e.get("player_id")
            snatched = e.get("snatched", False)
            verb = "snatched" if snatched else "picked up"
            return [
                self._ev(
                    clk,
                    "aegis_picked_up",
                    f"{self._format_slot(pid)} {verb} Aegis",
                    picker_slot=pid,
                    picker_side=slot_side(pid, my_slot, my_team),
                    snatched=snatched,
                )
            ]

        inner = None
        if event_type == "generic_event":
            try:
                inner = json.loads(e.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                inner = None

        if isinstance(inner, dict):
            msg_type = inner.get("type", "")
            if msg_type == "CHAT_MESSAGE_ITEM_PURCHASE":
                slot = inner.get("playerid1")
                side = slot_side(slot, my_slot, my_team)
                if side == "self":
                    return []  # already covered by item_added
                item = item_name(inner.get("value"))
                return [
                    self._ev(
                        clk,
                        "item_purchase",
                        f"{self._format_slot(slot)} bought {item}",
                        slot=slot,
                        side=side,
                        item=item,
                    )
                ]
            if msg_type in ("CHAT_MESSAGE_HERO_KILL", "CHAT_MESSAGE_FIRSTBLOOD"):
                # CHAT_MESSAGE_HERO_KILL uses playerid1=victim, playerid2=killer.
                # CHAT_MESSAGE_FIRSTBLOOD uses playerid1=killer, playerid2=victim.
                if msg_type == "CHAT_MESSAGE_FIRSTBLOOD":
                    killer = inner.get("playerid1")
                    victim = inner.get("playerid2")
                    tag = "first_blood"
                else:
                    victim = inner.get("playerid1")
                    killer = inner.get("playerid2")
                    tag = "hero_kill"
                # Skip non-hero kills (creep waves, towers): killer slot < 0.
                if killer is None or killer < 0:
                    return []
                bounty = inner.get("value")
                killer_side = slot_side(killer, my_slot, my_team)
                victim_side = slot_side(victim, my_slot, my_team)
                # HERO_KILL-driven slot inference: if exactly one hero of victim's
                # side vanished from the minimap this tick, map victim_slot to it.
                vanished = (
                    enemy_vanished if victim_side == "enemy" else ally_vanished
                )
                if len(vanished) == 1:
                    self._try_map_slot(
                        victim, next(iter(vanished)), my_slot, my_team
                    )
                return [
                    self._ev(
                        clk,
                        tag,
                        f"{self._format_slot(killer)} killed "
                        f"{self._format_slot(victim)} for {bounty}g",
                        killer_slot=killer,
                        victim_slot=victim,
                        killer_side=killer_side,
                        victim_side=victim_side,
                        bounty=bounty,
                    )
                ]
            if msg_type in ("CHAT_MESSAGE_TOWER_KILL", "CHAT_MESSAGE_BARRACKS_KILL"):
                # value indicates the team that DID the kill: 2=radiant did it
                # (so dire's building fell), 3=dire did it (radiant's building fell).
                # playerid1 is killer slot (or -1 for creep wave).
                killer_team = inner.get("value")
                killer = inner.get("playerid1")
                my_team_id = 2 if my_team == "radiant" else 3
                building_side = "theirs" if killer_team == my_team_id else "ours"
                killer_side = slot_side(killer, my_slot, my_team)
                noun = "barracks" if msg_type == "CHAT_MESSAGE_BARRACKS_KILL" else "tower"
                tag = (
                    "barracks_kill"
                    if msg_type == "CHAT_MESSAGE_BARRACKS_KILL"
                    else "tower_kill"
                )
                if building_side == "ours":
                    msg = f"Lost a {noun}"
                elif killer is None or killer < 0:
                    msg = f"Took an enemy {noun}"
                else:
                    msg = f"Took an enemy {noun} (killer: {self._format_slot(killer)})"
                return [
                    self._ev(
                        clk,
                        tag,
                        msg,
                        building_side=building_side,
                        killer_slot=killer,
                        killer_side=killer_side,
                    )
                ]
            if msg_type == "CHAT_MESSAGE_OBSERVER_WARD_KILLED":
                # Best-effort: playerid1 = killer slot, playerid2 = ward owner slot.
                killer = inner.get("playerid1")
                owner = inner.get("playerid2")
                return [
                    self._ev(
                        clk,
                        "ward_killed",
                        f"Observer ward killed by {self._format_slot(killer)} "
                        f"(owner: {self._format_slot(owner)})",
                        killer_slot=killer,
                        owner_slot=owner,
                    )
                ]
            if msg_type in (
                "CHAT_MESSAGE_COURIER_LOST",
                "CHAT_MESSAGE_COURIER_RESPAWNED",
            ):
                # Best-effort: courier owner slot appears in playerid1 or playerid2.
                # Schema is murky; pick whichever is a valid 0-9 slot.
                p1 = inner.get("playerid1")
                p2 = inner.get("playerid2")
                owner = p1 if isinstance(p1, int) and p1 >= 0 else p2
                owner_side = slot_side(owner, my_slot, my_team)
                if msg_type == "CHAT_MESSAGE_COURIER_LOST":
                    tag = "courier_lost"
                    msg = f"Courier lost ({self._format_slot(owner)})"
                else:
                    tag = "courier_respawned"
                    msg = f"Courier respawned ({self._format_slot(owner)})"
                return [
                    self._ev(
                        clk,
                        tag,
                        msg,
                        owner_slot=owner,
                        owner_side=owner_side,
                    )
                ]

        return [self._ev(clk, "server_event", str(e), raw=e)]
