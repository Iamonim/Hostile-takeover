"""
HOSTILE TAKEOVER  -  Syndicate Edition  (v2)
A turn-based mafia territory war in pygame.

Controls
  SPACE / ENTER ... roll dice / stop moving / end turn / dismiss popups
  Arrow keys / WASD  step one tile      (or CLICK a glowing tile to walk there)
  B ... buy / upgrade the tile you stand on (or use a special tile)
  T ... hostile takeover of a rival tile you stand on
  1 / 2 / 3 ... play an action card (one per turn)
  M ... sound on/off          R ... rematch after game over
"""
import sys
import os
import json
import random
import math
import colorsys
import datetime
from array import array
from collections import deque

import pygame

try:
    pygame.mixer.pre_init(22050, -16, 1, 512)
except Exception:
    pass
pygame.init()
pygame.font.init()

# --------------------------------------------------------------------------
# Layout / tuning constants
# --------------------------------------------------------------------------
W, H = 980, 720
TILE = 80
GRID = 8
BOARD = TILE * GRID
BX, BY = 20, 20
PX = BX + BOARD + 20          # right panel x
PW = W - PX - 20              # right panel width
FPS = 60

START_CASH = 3_000_000
MOVE_SPEED = 7
MAX_LEVEL = 4
RENT_MULT = [1, 2.5, 4.5, 7]
HAND_MAX = 3
LOAN_AMOUNT, LOAN_REPAY, LOAN_ROUNDS = 500_000, 600_000, 2
MARKET_PRICE = 150_000
FINE = 100_000
SECOND_MOVER_CASH = 150_000

MODES = {
    # goals tuned from 135-game bot simulations: see the notes in the chat
    "tutorial": {"rounds": 10, "goal": 2, "label": "TUTORIAL"},
    "standard": {"rounds": 15, "goal": 2, "label": "STANDARD"},
    "blitz":    {"rounds": 7,  "goal": 2, "label": "BLITZ"},
}

SAVE_PATH = os.environ.get("HT_SAVE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "hostile_takeover_save.json")

# --------------------------------------------------------------------------
# Colours / fonts
# --------------------------------------------------------------------------
BG = (22, 21, 18)
PANEL = (38, 37, 34)
PANEL_DARK = (28, 27, 24)
EDGE = (64, 62, 57)
WHITE = (255, 255, 255)
MUTED = (150, 147, 142)
GREEN = (118, 186, 27)
RED = (214, 64, 64)
GOLD = (250, 205, 70)
YOU_COL = (214, 58, 58)
RIVAL_COL = (52, 134, 214)


def font(size, bold=True):
    return pygame.font.SysFont("arial,helvetica,dejavusans", size, bold=bold)


F_XS, F_S, F_M, F_L, F_XL = font(11), font(12), font(14), font(20), font(34)


def shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


def fmt(n):
    n = int(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        return f"{sign}${n / 1_000_000:.2f}M"
    return f"{sign}${n // 1000}k"


def wrap(text, f, width):
    lines, cur = [], ""
    for word in text.split():
        test = (cur + " " + word).strip()
        if f.size(test)[0] <= width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def blit_center(s, surf, cx, y):
    s.blit(surf, (cx - surf.get_width() // 2, y))


# --------------------------------------------------------------------------
# Districts: 16 blocks of 2x2 tiles. Owning every ownable tile = monopoly.
# --------------------------------------------------------------------------
DISTRICT_NAMES = [
    "The Docks", "Neon Strip", "Chinatown Dens", "Old Port",
    "Casino Row", "Shadow Bank", "Precinct 9", "Harbor Yard",
    "Mayor's Hill", "Syndicate HQ", "Smuggler's Cove", "Rust Quarter",
    "Velvet Club", "Laundry Row", "Skyline Tower", "Back Alley",
]


def build_districts():
    out = []
    for i, name in enumerate(DISTRICT_NAMES):
        br, bc = divmod(i, 4)
        hue = (i * 0.382) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.50, 0.58)
        col = (int(r * 255), int(g * 255), int(b * 255))
        center = br in (1, 2) and bc in (1, 2)
        pair = min(i, 15 - i)    # district i and 15-i are mirror images, so they cost the same
        price = 200_000 + 50_000 * ((pair * 3) % 5) + (200_000 if center else 0)
        out.append({"name": name, "color": col, "dark": shade(col, 0.72), "price": price})
    return out


DISTRICTS = build_districts()

# Special tiles come in rotation-symmetric pairs so neither side is favoured.
SPECIALS = {
    (1, 3): "safe",   (6, 4): "safe",
    (3, 6): "market", (4, 1): "market",
    (3, 1): "bank",   (4, 6): "bank",
    (3, 3): "police", (4, 4): "police",
}
SPECIAL_INFO = {
    "safe":   ("Safehouse", "SAFEHOUSE", (60, 140, 110),
               "Land here: Heat -20."),
    "market": ("Black Market", "BLACK MKT", (130, 70, 150),
               f"Press B: buy a random card for {fmt(MARKET_PRICE)} (hand max {HAND_MAX})."),
    "bank":   ("Bank", "BANK", (170, 140, 50),
               f"Press B: loan of {fmt(LOAN_AMOUNT)}, repay {fmt(LOAN_REPAY)} in {LOAN_ROUNDS} rounds."),
    "police": ("Police Station", "POLICE", (70, 90, 150),
               f"Shakedown: pay {fmt(FINE)} and your next roll is halved."),
}

# Underworld event cards: (title, description, cash, heat, steal-from-rival)
EVENTS = [
    ("Offshore Account Found", "A forgotten shell company pays out.", 300_000, 0, 0),
    ("Federal Audit", "You pay the auditors off quietly.", -200_000, 10, 0),
    ("Black Market Shipment", "The cargo cleared customs.", 400_000, 12, 0),
    ("Informant Payoff", "Buying silence before it spreads.", -150_000, -20, 0),
    ("Rival Stash Raided", "Your crew hits their safehouse.", 0, 10, 200_000),
    ("Warehouse Fire", "Repairs eat your budget.", -250_000, 0, 0),
    ("Police Tip-Off", "Heat is rising across the city.", 0, 30, 0),
    ("Laundering Success", "Dirty money comes out clean.", 150_000, -25, 0),
]

# Action cards. One per turn. You draw one at the start of every round.
CARD_DEFS = {
    "bribe":    {"name": "Bribe", "color": (60, 120, 170), "when": ("ROLL",),
                 "desc": "Cancel the next rent you would owe."},
    "sabotage": {"name": "Sabotage", "color": (190, 70, 60), "when": ("ROLL", "ACTION"),
                 "desc": "The rival's highest-level tile drops one level. Heat +6."},
    "lockdown": {"name": "Lockdown", "color": (90, 90, 170), "when": ("ACTION",),
                 "desc": "Stand on your tile: it cannot be taken over for 2 rounds."},
    "smuggle":  {"name": "Smuggle", "color": (170, 130, 40), "when": ("ROLL", "ACTION"),
                 "desc": "Steal 10% of the rival's cash. Heat +8."},
    "laylow":   {"name": "Lay Low", "color": (70, 150, 110), "when": ("ROLL", "ACTION"),
                 "desc": "Heat -30."},
    "dice":     {"name": "Double Dice", "color": (150, 90, 160), "when": ("ROLL",),
                 "desc": "Roll two dice this turn and keep the higher one."},
}
CARD_KEYS = list(CARD_DEFS)

# Rival personalities.
PERSONAS = {
    "banker": {"name": "THE BANKER", "blurb": "Hoards cash, upgrades steadily, strikes late.",
               "reserve": 600_000, "take_reserve": 800_000, "heat_cap": 55,
               "take": 1.0, "take_after": 0.4, "card_p": 0.5, "up": 1.4},
    "hothead": {"name": "THE HOTHEAD", "blurb": "Attacks early and runs hot. Expect raids.",
                "reserve": 100_000, "take_reserve": 150_000, "heat_cap": 90,
                "take": 1.8, "take_after": 0.0, "card_p": 0.4, "up": 0.8},
    "fixer": {"name": "THE FIXER", "blurb": "Plays a card almost every single turn.",
              "reserve": 350_000, "take_reserve": 500_000, "heat_cap": 70,
              "take": 1.1, "take_after": 0.25, "card_p": 0.95, "up": 1.0},
}
PERSONA_KEYS = list(PERSONAS)

# Crew leaders: one passive each. Unlocked by player level, so XP always buys something.
LEADER_DON_RENT = 1.2             # tuned by simulation, see leaders matrix
LEADER_ACCOUNTANT_DIVIDENDS = 1.25
LEADER_ENFORCER_COST = 0.9
LEADER_GHOST_RENT = 0.8
LEADERS = {
    "don": {"name": "THE DON", "unlock": 1,
            "blurb": "Old school. You collect +20% rent on every tile you own."},
    "accountant": {"name": "THE ACCOUNTANT", "unlock": 2,
                   "blurb": "Dividends +25%. Upgrades cost 15% less."},
    "enforcer": {"name": "THE ENFORCER", "unlock": 3,
                 "blurb": "Takeovers cost 10% less and add only 15 Heat."},
    "ghost": {"name": "THE GHOST", "unlock": 5,
              "blurb": "Heat cools twice as fast. You pay 20% less rent."},
}
LEADER_KEYS = list(LEADERS)
PERSONA_LEADER = {"banker": "accountant", "hothead": "enforcer", "fixer": "ghost"}

# Trophy ranks. Higher rank = the rival starts with more cash (shown on the menu).
RANKS = [(0, "Street Thug"), (100, "Soldier"), (250, "Capo"), (450, "Underboss"), (700, "Godfather")]
TROPHY_RULES = {"standard": (30, -20), "blitz": (18, -12)}     # (win, loss)


def rank_info(trophies):
    """(rank name, this rank's floor, next rank's floor or None)."""
    idx = max(i for i, (floor, _) in enumerate(RANKS) if trophies >= floor)
    nxt = RANKS[idx + 1][0] if idx + 1 < len(RANKS) else None
    return RANKS[idx][1], RANKS[idx][0], nxt


def daily_seed(date_str):
    return int(date_str.replace("-", ""))


def daily_rival(date_str):
    return PERSONA_KEYS[daily_seed(date_str) % len(PERSONA_KEYS)]

# Daily contract pool: id, text, stat key, target, how progress accumulates
CONTRACT_POOL = [
    {"id": "win1", "text": "Win a match", "key": "wins", "target": 1, "mode": "sum"},
    {"id": "take2", "text": "Seize 2 rival tiles", "key": "takeovers", "target": 2, "mode": "sum"},
    {"id": "mono1", "text": "Complete a district", "key": "monopolies", "target": 1, "mode": "sum"},
    {"id": "cards3", "text": "Play 3 action cards", "key": "cards", "target": 3, "mode": "sum"},
    {"id": "play2", "text": "Play 2 matches", "key": "matches", "target": 2, "mode": "sum"},
    {"id": "rent1m", "text": "Collect $1M in rent", "key": "rent", "target": 1_000_000, "mode": "sum"},
    {"id": "tiles6", "text": "Own 6 tiles at once", "key": "max_tiles", "target": 6, "mode": "max"},
    {"id": "blitz", "text": "Win a Blitz match", "key": "blitz_wins", "target": 1, "mode": "sum"},
]


# --------------------------------------------------------------------------
# Sound (synthesised, no asset files needed)
# --------------------------------------------------------------------------
class Sound:
    def __init__(self):
        self.ok = False
        self.muted = False
        self.fx = {}
        try:
            init = pygame.mixer.get_init()
            if not init:
                pygame.mixer.init()
                init = pygame.mixer.get_init()
            if not init:
                return
            self.rate, fmt_, self.ch = init
            if fmt_ != -16:
                return
            self.build()
            self.ok = True
        except Exception:
            self.ok = False

    # -- synthesis helpers --
    def tone(self, f0, dur, vol=0.45, f1=None, wave="sine"):
        n = int(self.rate * dur)
        out, phase = [], 0.0
        for i in range(n):
            t = i / max(1, n)
            f = f0 if f1 is None else f0 + (f1 - f0) * t
            phase += 2 * math.pi * f / self.rate
            if wave == "square":
                v = 1.0 if math.sin(phase) > 0 else -1.0
            elif wave == "saw":
                v = 2 * ((phase / (2 * math.pi)) % 1) - 1
            else:
                v = math.sin(phase)
            env = min(1.0, i / (self.rate * 0.004)) * (1 - t) ** 1.4
            out.append(v * env * vol)
        return out

    def noise(self, dur, vol=0.4):
        n = int(self.rate * dur)
        return [random.uniform(-1, 1) * (1 - i / max(1, n)) ** 2 * vol for i in range(n)]

    def silence(self, dur):
        return [0.0] * int(self.rate * dur)

    @staticmethod
    def seq(*parts):
        out = []
        for p in parts:
            out += p
        return out

    @staticmethod
    def mix(a, b):
        n = max(len(a), len(b))
        a = a + [0.0] * (n - len(a))
        b = b + [0.0] * (n - len(b))
        return [x + y for x, y in zip(a, b)]

    def make(self, samples):
        data = array("h")
        for v in samples:
            v = int(max(-1.0, min(1.0, v)) * 32000)
            data.append(v)
            if self.ch == 2:
                data.append(v)
        return pygame.mixer.Sound(buffer=data.tobytes())

    def build(self):
        t, n, q, s = self.tone, self.noise, self.seq, self.silence
        self.fx = {
            "coin": self.make(q(t(988, 0.06), t(1319, 0.2))),
            "buy": self.make(q(t(520, 0.06), t(780, 0.12))),
            "upgrade": self.make(q(t(523, 0.07), t(659, 0.07), t(784, 0.14))),
            "takeover": self.make(self.mix(t(110, 0.55, 0.8, f1=42), n(0.3, 0.5))),
            "raid": self.make(q(*[t(f, 0.16, 0.3, wave="saw") for f in (700, 900, 700, 900, 700)])),
            "monopoly": self.make(q(t(523, 0.09), t(659, 0.09), t(784, 0.09), t(1047, 0.4))),
            "dice": self.make(q(*[q(n(0.025, 0.5), s(0.045)) for _ in range(6)])),
            "card": self.make(t(380, 0.16, 0.35, f1=900)),
            "error": self.make(t(150, 0.16, 0.35, wave="square")),
            "event": self.make(q(t(660, 0.1), t(880, 0.22))),
            "win": self.make(q(t(523, 0.12), t(659, 0.12), t(784, 0.12), t(1047, 0.12), t(784, 0.1), t(1047, 0.5))),
            "lose": self.make(t(392, 0.8, 0.45, f1=170)),
        }

    def play(self, name):
        if self.ok and not self.muted and name in self.fx:
            try:
                self.fx[name].play()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Profile: XP, level, streak, daily contracts (saved as a small JSON file)
# --------------------------------------------------------------------------
def level_info(xp):
    lvl, need = 1, 100
    while xp >= need:
        xp -= need
        lvl += 1
        need = 100 + 40 * (lvl - 1)
    return lvl, xp, need


class Profile:
    def __init__(self, path):
        self.path = path
        self.d = {"xp": 0, "matches": 0, "wins": 0, "streak": 0, "best_streak": 0,
                  "last_day": "", "freeze": 1, "tutorial_done": False, "rival": "banker",
                  "trophies": 0, "leader": "don",
                  "daily": {"date": "", "best": 0, "plays": 0},
                  "dailies": {"date": "", "progress": {}, "done": []}}
        try:
            with open(path) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.d.update(loaded)
        except Exception:
            pass
        self.touch_day()

    def save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.d, f)
        except Exception:
            pass

    def touch_day(self):
        today = datetime.date.today()
        ts = today.isoformat()
        if self.d["dailies"].get("date") != ts:
            self.d["dailies"] = {"date": ts, "progress": {}, "done": []}
        if self.d["daily"].get("date") != ts:
            self.d["daily"] = {"date": ts, "best": 0, "plays": 0}
        last = self.d.get("last_day", "")
        if last != ts:
            try:
                gap = (today - datetime.date.fromisoformat(last)).days if last else None
            except ValueError:
                gap = None
            if gap == 1:
                self.d["streak"] += 1
            elif gap == 2 and self.d.get("freeze", 0) > 0:
                self.d["streak"] += 1
                self.d["freeze"] -= 1
            else:
                self.d["streak"] = 1
            if self.d["streak"] % 7 == 0:
                self.d["freeze"] = min(1, self.d.get("freeze", 0) + 1)
            self.d["best_streak"] = max(self.d.get("best_streak", 0), self.d["streak"])
            self.d["last_day"] = ts
        self.save()

    def contracts(self):
        rng = random.Random(self.d["dailies"]["date"])
        return rng.sample(CONTRACT_POOL, 3)

    def level(self):
        return level_info(self.d["xp"])[0]

    def unlocked(self, leader):
        return leader in LEADERS and self.level() >= LEADERS[leader]["unlock"]

    def handicap(self):
        """Rank makes the rival richer at the start. Transparent rubber-banding."""
        cash = min(900_000, (self.d["trophies"] // 10) * 15_000)
        return {"cash": cash}

    def record(self, game):
        s = game.stats
        won = game.result[0] == "VICTORY"
        xp = 40 + (60 if won else 0) + 10 * min(s["takeovers"], 5) \
            + 25 * min(s["monopolies"], 3) + 5 * min(s["cards"], 5)
        before = level_info(self.d["xp"])
        done_now = []
        if game.mode == "tutorial":
            self.d["tutorial_done"] = True
            xp += 50
        else:
            vals = {"wins": 1 if won else 0, "matches": 1, "takeovers": s["takeovers"],
                    "monopolies": s["monopolies"], "cards": s["cards"], "rent": s["rent"],
                    "max_tiles": s["max_tiles"],
                    "blitz_wins": 1 if (won and game.mode == "blitz") else 0}
            dl = self.d["dailies"]
            for c in self.contracts():
                cur = dl["progress"].get(c["id"], 0)
                v = vals[c["key"]]
                cur = max(cur, v) if c["mode"] == "max" else cur + v
                dl["progress"][c["id"]] = cur
                if cur >= c["target"] and c["id"] not in dl["done"]:
                    dl["done"].append(c["id"])
                    xp += 60
                    done_now.append(c["text"])
            self.d["matches"] += 1
            if won:
                self.d["wins"] += 1
        # trophies: ranked modes only (not the tutorial, not the daily challenge)
        t_before = self.d["trophies"]
        if game.mode in TROPHY_RULES and not game.daily:
            gain, loss = TROPHY_RULES[game.mode]
            self.d["trophies"] = max(0, t_before + (gain if won else loss))
        t_after = self.d["trophies"]

        # daily challenge: one score per attempt, best one is kept
        daily, share = None, None
        if game.daily:
            ds = self.d["daily"]
            score = game.score()
            new_best = score > ds["best"]
            ds["best"] = max(ds["best"], score)
            ds["plays"] += 1
            share = (f"HOSTILE TAKEOVER daily {ds['date']}: {'VICTORY' if won else 'DEFEAT'}, "
                     f"{fmt(score)} in {min(game.round, game.max_rounds)} rounds. Beat me!")
            daily = {"score": score, "best": ds["best"], "new_best": new_best}

        self.d["xp"] += xp
        self.save()
        return {"xp": xp, "before": before, "after": level_info(self.d["xp"]),
                "contracts": done_now, "won": won,
                "trophies": (t_before, t_after), "rank": (rank_info(t_before)[0], rank_info(t_after)[0]),
                "daily": daily, "share": share}


# --------------------------------------------------------------------------
# Board pieces
# --------------------------------------------------------------------------
def cell_center(r, c):
    return BX + c * TILE + TILE // 2, BY + r * TILE + TILE // 2


def make_path(a, b):
    r, c = a
    path = []
    while r != b[0]:
        r += 1 if b[0] > r else -1
        path.append((r, c))
    while c != b[1]:
        c += 1 if b[1] > c else -1
        path.append((r, c))
    return path


class Tile:
    def __init__(self, r, c):
        self.r, self.c = r, c
        self.did = (r // 2) * 4 + c // 2
        self.kind = SPECIALS.get((r, c), "prop")
        self.price = DISTRICTS[self.did]["price"]
        self.owner = None
        self.level = 1
        self.lock = 0
        self.num = 0

    @property
    def name(self):
        if self.kind != "prop":
            return SPECIAL_INFO[self.kind][0]
        return f"{DISTRICTS[self.did]['name']} #{self.num}"


class Player:
    def __init__(self, name, r, c, color):
        self.name = name
        self.r, self.c = r, c
        self.color = color
        self.cash = START_CASH
        self.heat = 0
        self.px, self.py = cell_center(r, c)
        self.path = deque()
        self.hand = []
        self.played = False      # one card per turn
        self.acted = False       # one build action per turn
        self.shield = False      # Bribe active
        self.double = False      # Double Dice armed
        self.halve = False       # next roll halved (police)
        self.debt = 0
        self.debt_timer = 0
        self.leader = "don"

    @property
    def idle(self):
        return not self.path

    @property
    def dest(self):
        return self.path[-1] if self.path else (self.r, self.c)

    def update(self):
        if not self.path:
            return
        tr, tc = self.path[0]
        tx, ty = cell_center(tr, tc)
        dx, dy = tx - self.px, ty - self.py
        dist = math.hypot(dx, dy)
        if dist <= MOVE_SPEED:
            self.px, self.py = tx, ty
            self.r, self.c = tr, tc
            self.path.popleft()
        else:
            self.px += dx / dist * MOVE_SPEED
            self.py += dy / dist * MOVE_SPEED


# --------------------------------------------------------------------------
# The game
# --------------------------------------------------------------------------
class Game:
    def __init__(self, mode="standard", rival="banker", seed=None, you_persona=None,
                 you_leader="don", rival_leader=None, handicap=None, daily=False):
        cfg = MODES[mode]
        self.mode = mode
        self.daily = daily
        self.max_rounds = cfg["rounds"]
        self.goal = cfg["goal"]
        self.easy = (mode == "tutorial")
        self.seed = seed if seed is not None else random.randrange(1 << 30)
        self.rng = random.Random(self.seed)       # all game logic uses this -> replayable
        self.rival_key = rival if rival in PERSONAS else "banker"
        self.persona = PERSONAS[self.rival_key]
        self.you_persona = you_persona            # None for a human, a persona key for a bot

        self.board = [[Tile(r, c) for c in range(GRID)] for r in range(GRID)]
        self.districts = {i: [] for i in range(16)}
        self.props = {i: [] for i in range(16)}
        for row in self.board:
            for t in row:
                self.districts[t.did].append(t)
                if t.kind == "prop":
                    self.props[t.did].append(t)
                    t.num = len(self.props[t.did])

        self.you = Player("YOU", 7, 3, YOU_COL)
        self.rival = Player("RIVAL", 0, 4, RIVAL_COL)
        self.you.leader = you_leader if you_leader in LEADERS else "don"
        self.rival.leader = rival_leader or PERSONA_LEADER[self.rival_key]
        for p in (self.you, self.rival):
            for _ in range(2):
                self.draw_card(p)
        # You always move first, which is worth a little in a race. The second mover
        # gets a small head start (measured in simulation to even the odds out).
        if not self.easy:
            self.draw_card(self.rival)
            self.rival.cash += SECOND_MOVER_CASH
            if handicap:
                self.rival.cash += handicap.get("cash", 0)
        self.toast_text, self.toast_t = "", 0

        self.state = "ROLL"   # ROLL, MOVE, ACTION, EVENT, RIVAL, GAMEOVER
        self.round = 1
        self.dice = 0
        self.moves_left = 0
        self.dice_timer = 0
        self.want_land = False
        self.event = None
        self.result = None
        self.report = None
        self.r_stage = None
        self.r_timer = 0

        self.stats = {"takeovers": 0, "monopolies": 0, "cards": 0, "rent": 0, "max_tiles": 0}
        self.best_moment = (0, "")
        self.sfx = []
        self.log = []
        self.floaters = []
        self.particles = []
        self.shake = 0
        self.hover = None
        self.mouse = (0, 0)
        self.sound_on = True
        self.tick = 0

        self.say("Empire war begins. Roll the dice!")

    # ---------------- helpers ----------------
    def say(self, msg):
        self.log.append(msg)
        if len(self.log) > 8:
            self.log.pop(0)

    def snd(self, name):
        self.sfx.append(name)

    def drain_sfx(self):
        out, self.sfx = self.sfx, []
        return out

    def moment(self, score, text):
        if score > self.best_moment[0]:
            self.best_moment = (score, f"Round {min(self.round, self.max_rounds)}: {text}")

    @staticmethod
    def who(p):
        return "You" if p.name == "YOU" else "Rival"

    def other(self, p):
        return self.rival if p is self.you else self.you

    def tile_at(self, r, c):
        return self.board[r][c]

    def owned(self, p):
        return [t for row in self.board for t in row if t.owner is p]

    def has_monopoly(self, p, did):
        return all(t.owner is p for t in self.props[did])

    def empire(self, p):
        return sum(1 for d in self.props if self.has_monopoly(p, d))

    def rent_of(self, t, payer=None):
        base = t.price * 0.25 * RENT_MULT[t.level - 1]
        if t.owner and self.has_monopoly(t.owner, t.did):
            base *= 2
        if t.owner is not None and t.owner.leader == "don":
            base *= LEADER_DON_RENT
        if payer is not None and payer.leader == "ghost":
            base *= LEADER_GHOST_RENT
        return int(base)

    def upgrade_cost(self, t):
        base = t.price // 2
        if t.owner is not None and t.owner.leader == "accountant":
            base = int(base * 0.85)
        return base

    def underdog(self, p):
        return self.round >= 3 and self.net_worth(p) < 0.7 * self.net_worth(self.other(p))

    def takeover_cost(self, t, p=None):
        base = t.price * 2 + (t.level - 1) * t.price
        if p is not None and self.underdog(p):
            base = int(base * 0.75)
        if p is not None and p.leader == "enforcer":
            base = int(base * LEADER_ENFORCER_COST)
        return int(base)

    def net_worth(self, p):
        return p.cash - p.debt + sum(t.price + (t.level - 1) * t.price // 2 for t in self.owned(p))

    def float_text(self, text, x, y, color):
        self.floaters.append([text, x, y, color, 75])

    def burst(self, x, y, color, n=18):
        for _ in range(n):
            a = random.random() * math.tau
            sp = random.uniform(1.5, 5)
            self.particles.append([x, y, math.cos(a) * sp, math.sin(a) * sp, random.randint(25, 50), color])

    def fail(self, p, msg):
        if p is self.you:
            self.say(msg)
            self.snd("error")
        return False

    # ---------------- cards ----------------
    def draw_card(self, p):
        if len(p.hand) >= HAND_MAX:
            return False
        fresh = [k for k in CARD_KEYS if k not in p.hand]     # prefer variety in your hand
        p.hand.append(self.rng.choice(fresh or CARD_KEYS))
        return True

    def block_reason(self, p, idx, phase):
        if idx >= len(p.hand):
            return "No card in that slot."
        if p.played:
            return "One card per turn."
        if phase is None:
            return "Play cards before you roll or after you land."
        key = p.hand[idx]
        d = CARD_DEFS[key]
        o = self.other(p)
        if phase not in d["when"]:
            return "Play this one before you roll." if "ROLL" in d["when"] else "Play this one after you land."
        t = self.tile_at(p.r, p.c)
        if key == "sabotage" and not any(x.level > 1 for x in self.owned(o)):
            return "The rival has no upgraded tiles."
        if key == "lockdown":
            if t.kind != "prop" or t.owner is not p:
                return "Stand on one of your own tiles."
            if t.lock > 0:
                return "Already locked down."
        if key == "smuggle" and o.cash < 500_000:
            return "The rival is too broke to rob."
        if key == "laylow" and p.heat < 10:
            return "Your Heat is already low."
        if key == "bribe" and p.shield:
            return "Bribe is already active."
        if key == "dice" and p.double:
            return "Double Dice is already armed."
        return None

    def play_card(self, p, idx, phase):
        why = self.block_reason(p, idx, phase)
        if why:
            return self.fail(p, why)
        key = p.hand.pop(idx)
        d = CARD_DEFS[key]
        o = self.other(p)
        p.played = True
        self.snd("card")
        self.say(f"{p.name} played {d['name']}")
        if key == "bribe":
            p.shield = True
        elif key == "sabotage":
            target = max([t for t in self.owned(o) if t.level > 1], key=lambda t: (t.level, t.price))
            target.level -= 1
            x, y = cell_center(target.r, target.c)
            self.float_text("SABOTAGED", x, y, RED)
            self.burst(x, y, RED, 16)
            self.add_heat(p, 6)
            self.moment(300_000, f"{self.who(p)} sabotaged {target.name}")
        elif key == "lockdown":
            t = self.tile_at(p.r, p.c)
            t.lock = 2
            x, y = cell_center(t.r, t.c)
            self.float_text("LOCKDOWN", x, y, GOLD)
        elif key == "smuggle":
            amt = int(max(0, o.cash) * 0.10)
            o.cash -= amt
            p.cash += amt
            self.float_text("+" + fmt(amt), p.px, p.py - 28, GREEN)
            self.add_heat(p, 8)
            self.moment(amt, f"{self.who(p)} smuggled {fmt(amt)} from the {self.who(o).lower()}")
        elif key == "laylow":
            p.heat = max(0, p.heat - 30)
        elif key == "dice":
            p.double = True
        if p is self.you:
            self.stats["cards"] += 1
        return True

    # ---------------- money / heat ----------------
    def settle(self, p):
        """Auto-sell the cheapest tiles to cover debts."""
        while p.cash < 0:
            mine = self.owned(p)
            if not mine:
                break
            t = min(mine, key=lambda x: x.price * x.level)
            p.cash += t.price // 2
            t.owner = None
            t.level = 1
            t.lock = 0
            self.say(f"{p.name} fire-sold {t.name}")

    def add_heat(self, p, n):
        p.heat = max(0, p.heat + n)
        if p.heat >= 100:
            loss = int(max(0, p.cash) * 0.25)
            p.cash -= loss
            p.heat = 35
            self.say(f"FEDERAL RAID! {p.name} loses {fmt(loss)}")
            self.float_text("FEDERAL RAID!", p.px, p.py - 30, RED)
            self.shake = 22
            self.burst(p.px, p.py, RED, 30)
            self.snd("raid")
            self.moment(loss * 1.2, f"Feds raided {self.who(p).lower()} for {fmt(loss)}")
            self.settle(p)

    # ---------------- build actions ----------------
    def act_buy(self, p):
        t = self.tile_at(p.r, p.c)
        if t.kind != "prop":
            return self.act_special(p, t)
        if p.acted:
            return self.fail(p, "One build action per turn.")
        x, y = cell_center(t.r, t.c)
        newly = False
        if t.owner is None:
            if p.cash < t.price:
                return self.fail(p, "Not enough cash to buy.")
            p.cash -= t.price
            t.owner = p
            newly = True
            self.add_heat(p, 4)
            self.say(f"{p.name} bought {t.name}")
            self.float_text("-" + fmt(t.price), x, y, GOLD)
            self.burst(x, y, p.color, 14)
            self.snd("buy")
        elif t.owner is p:
            cost = self.upgrade_cost(t)
            if t.level >= MAX_LEVEL:
                return self.fail(p, "Already max level.")
            if p.cash < cost:
                return self.fail(p, "Not enough cash to upgrade.")
            p.cash -= cost
            t.level += 1
            self.add_heat(p, 6)
            self.say(f"{p.name} upgraded {t.name} to L{t.level}")
            self.float_text(f"LEVEL {t.level}", x, y, WHITE)
            self.burst(x, y, WHITE, 10)
            self.snd("upgrade")
        else:
            return self.fail(p, "Rival owns this. Use takeover (T).")
        p.acted = True
        if newly:
            self.after_acquire(p, t)
        return True

    def act_takeover(self, p):
        t = self.tile_at(p.r, p.c)
        o = self.other(p)
        x, y = cell_center(t.r, t.c)
        if t.kind != "prop" or t.owner is not o:
            return self.fail(p, "Takeover needs a rival tile.")
        if p.acted:
            return self.fail(p, "One build action per turn.")
        if t.lock > 0:
            return self.fail(p, f"Locked down for {t.lock} more round(s).")
        cost = self.takeover_cost(t, p)
        if p.cash < cost:
            return self.fail(p, f"Takeover needs {fmt(cost)}.")
        p.cash -= cost
        o.cash += cost // 2
        t.owner = p
        t.level = max(1, t.level - 1)
        p.acted = True
        self.add_heat(p, 15 if p.leader == "enforcer" else 25)
        self.say(f"TAKEOVER! {p.name} seized {t.name}")
        self.float_text("TAKEOVER!", x, y, RED)
        self.shake = 14
        self.burst(x, y, p.color, 28)
        self.snd("takeover")
        if p is self.you:
            self.stats["takeovers"] += 1
        self.moment(cost, f"{self.who(p)} seized {t.name} for {fmt(cost)}")
        self.after_acquire(p, t)
        return True

    def act_special(self, p, t):
        x, y = cell_center(t.r, t.c)
        if t.kind == "bank":
            if p.debt:
                return self.fail(p, "You already have a loan.")
            p.cash += LOAN_AMOUNT
            p.debt, p.debt_timer = LOAN_REPAY, LOAN_ROUNDS
            self.say(f"{p.name} took a {fmt(LOAN_AMOUNT)} loan")
            self.float_text("+" + fmt(LOAN_AMOUNT), x, y, GREEN)
            self.snd("coin")
            return True
        if t.kind == "market":
            if len(p.hand) >= HAND_MAX:
                return self.fail(p, "Your hand is full.")
            if p.cash < MARKET_PRICE:
                return self.fail(p, "Not enough cash.")
            p.cash -= MARKET_PRICE
            self.draw_card(p)
            self.add_heat(p, 3)
            self.say(f"{p.name} bought a card")
            self.float_text("-" + fmt(MARKET_PRICE), x, y, GOLD)
            self.snd("card")
            return True
        return self.fail(p, "Nothing to buy here.")

    def after_acquire(self, p, t):
        if p is self.you:
            self.stats["max_tiles"] = max(self.stats["max_tiles"], len(self.owned(p)))
        if t.kind == "prop" and self.has_monopoly(p, t.did):
            d = DISTRICTS[t.did]["name"]
            self.say(f"{p.name} MONOPOLY: {d}!")
            cx = BX + (t.c // 2) * 2 * TILE + TILE
            cy = BY + (t.r // 2) * 2 * TILE + TILE
            self.float_text("MONOPOLY!", cx, cy, GOLD)
            self.burst(cx, cy, GOLD, 40)
            self.shake = max(self.shake, 10)
            self.snd("monopoly")
            if p is self.you:
                self.stats["monopolies"] += 1
            self.moment(1_500_000, f"{self.who(p)} completed {d}")

    def apply_event(self, p, card):
        title, desc, cash, heat, steal = card
        o = self.other(p)
        if steal:
            amt = min(steal, max(0, o.cash))
            cash += amt
            o.cash -= amt
            self.settle(o)
        p.cash += cash
        self.add_heat(p, heat)
        self.settle(p)
        self.say(f"{p.name}: {title}")
        if cash:
            self.float_text(("+" if cash > 0 else "") + fmt(cash), p.px, p.py - 28, GREEN if cash > 0 else RED)
        self.snd("event")
        return {"title": title, "desc": desc, "cash": cash, "heat": heat, "who": p.name}

    def land(self, p):
        t = self.tile_at(p.r, p.c)
        o = self.other(p)
        if t.kind == "prop" and t.owner is o:
            if p.shield:
                p.shield = False
                self.say(f"{p.name}'s Bribe cancelled the rent")
                self.float_text("BRIBED", p.px, p.py - 26, GOLD)
            else:
                rent = self.rent_of(t, p)
                p.cash -= rent
                o.cash += rent
                self.say(f"{p.name} paid {fmt(rent)} rent")
                self.float_text("-" + fmt(rent), p.px, p.py - 26, RED)
                self.float_text("+" + fmt(rent), o.px, o.py - 26, GREEN)
                self.snd("coin")
                if o is self.you:
                    self.stats["rent"] += rent
                self.moment(rent, f"{self.who(o)} collected {fmt(rent)} rent")
                self.settle(p)
        elif t.kind == "safe":
            p.heat = max(0, p.heat - 20)
            self.say(f"{p.name} laid low at the Safehouse (Heat -20)")
        elif t.kind == "police":
            p.cash -= FINE
            p.halve = True
            self.say(f"{p.name} shaken down: -{fmt(FINE)}, next roll halved")
            self.float_text("-" + fmt(FINE), p.px, p.py - 26, RED)
            self.snd("error")
            self.settle(p)
        elif t.kind == "bank" and p is self.you:
            self.say("Bank: press B for a loan.")
        elif t.kind == "market" and p is self.you:
            self.say("Black Market: press B to buy a card.")
        chance = 0.40 if self.underdog(p) else 0.25
        if self.rng.random() < chance:
            return self.apply_event(p, self.rng.choice(EVENTS))
        return None

    # ---------------- turn flow ----------------
    def roll_value(self, p):
        a = self.rng.randint(1, 6)
        v = a
        if p.double:
            b = self.rng.randint(1, 6)
            v = max(a, b)
            p.double = False
            self.say(f"{p.name} Double Dice: {a} and {b}")
        if p.halve:
            v = max(1, v // 2)
            p.halve = False
            self.say(f"{p.name}'s roll was halved to {v}")
        return v

    def roll_click(self):
        if self.state == "ROLL" and self.dice_timer == 0:
            self.dice_timer = 25
            self.snd("dice")
            self.say("Rolling...")

    def end_turn(self):
        if self.state == "ACTION":
            self.state = "RIVAL"
            self.r_stage = "roll"
            self.r_timer = 45
            self.dice = 0
            self.say("Rival's move...")

    def end_round(self):
        for p in (self.you, self.rival):
            income = 0
            for t in self.owned(p):
                m = 2 if self.has_monopoly(p, t.did) else 1
                income += (t.price // 10) * t.level * m
            if p.leader == "accountant":
                income = int(income * LEADER_ACCOUNTANT_DIVIDENDS)
            if income:
                p.cash += income
                if p is self.you:
                    self.say(f"Dividends: +{fmt(income)}")
                    self.float_text("+" + fmt(income), p.px, p.py - 40, GREEN)
            if p.debt:
                p.debt_timer -= 1
                if p.debt_timer <= 0:
                    p.cash -= p.debt
                    self.say(f"{p.name} repaid the {fmt(p.debt)} loan")
                    p.debt = 0
                    self.settle(p)
            p.heat = max(0, p.heat - (16 if p.leader == "ghost" else 8))
            p.played = False
            p.acted = False
            self.draw_card(p)
        for row in self.board:
            for t in row:
                if t.lock > 0:
                    t.lock -= 1
        self.round += 1
        self.state = "ROLL"
        self.r_stage = None
        self.dice = 0
        if self.round <= self.max_rounds:
            self.say(f"Round {self.round}. Your move.")

    # ---------------- input ----------------
    def step(self, dr, dc):
        if self.state != "MOVE" or not self.you.idle or self.moves_left <= 0:
            return
        r, c = self.you.dest
        nr, nc = r + dr, c + dc
        if 0 <= nr < GRID and 0 <= nc < GRID:
            self.you.path.append((nr, nc))
            self.moves_left -= 1

    def try_play(self, idx):
        phase = self.state if self.state in ("ROLL", "ACTION") else None
        self.play_card(self.you, idx, phase)

    def handle_key(self, key):
        if self.state == "EVENT":
            if key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_ESCAPE):
                self.event = None
                self.state = "ACTION"
            return
        if self.state == "RIVAL":
            return
        card_keys = {pygame.K_1: 0, pygame.K_2: 1, pygame.K_3: 2}
        if key in card_keys:
            self.try_play(card_keys[key])
            return
        if self.state == "ROLL":
            if key in (pygame.K_SPACE, pygame.K_RETURN):
                self.roll_click()
        elif self.state == "MOVE":
            if key in (pygame.K_UP, pygame.K_w):
                self.step(-1, 0)
            elif key in (pygame.K_DOWN, pygame.K_s):
                self.step(1, 0)
            elif key in (pygame.K_LEFT, pygame.K_a):
                self.step(0, -1)
            elif key in (pygame.K_RIGHT, pygame.K_d):
                self.step(0, 1)
            elif key in (pygame.K_SPACE, pygame.K_RETURN):
                self.want_land = True
        elif self.state == "ACTION":
            if key == pygame.K_b:
                self.act_buy(self.you)
            elif key == pygame.K_t:
                self.act_takeover(self.you)
            elif key in (pygame.K_SPACE, pygame.K_RETURN):
                self.end_turn()

    def rects(self):
        rc = {
            "dice": pygame.Rect(PX, 430, 56, 56),
            "roll": pygame.Rect(PX + 64, 430, PW - 64, 56),
            "buy": pygame.Rect(PX, 492, 138, 34),
            "take": pygame.Rect(PX + 142, 492, PW - 142, 34),
            "end": pygame.Rect(PX, 532, PW, 34),
        }
        for i in range(HAND_MAX):
            rc[f"card{i}"] = pygame.Rect(PX + i * 95, 326, 90, 66)
        return rc

    def tile_from_pos(self, pos):
        x, y = pos
        if BX <= x < BX + BOARD and BY <= y < BY + BOARD:
            return (y - BY) // TILE, (x - BX) // TILE
        return None

    def click(self, pos):
        rc = self.rects()
        if self.state == "EVENT":
            self.handle_key(pygame.K_RETURN)
            return
        if self.state == "RIVAL":
            return
        for i in range(HAND_MAX):
            if rc[f"card{i}"].collidepoint(pos):
                self.try_play(i)
                return
        if rc["roll"].collidepoint(pos) or rc["dice"].collidepoint(pos):
            if self.state == "ROLL":
                self.roll_click()
            elif self.state == "MOVE":
                self.want_land = True
            return
        if self.state == "ACTION":
            if rc["buy"].collidepoint(pos):
                self.act_buy(self.you)
            elif rc["take"].collidepoint(pos):
                self.act_takeover(self.you)
            elif rc["end"].collidepoint(pos):
                self.end_turn()
            return
        if self.state == "MOVE" and self.you.idle and self.moves_left > 0:
            cell = self.tile_from_pos(pos)
            if cell:
                dist = abs(cell[0] - self.you.r) + abs(cell[1] - self.you.c)
                if dist == 0:
                    self.want_land = True
                elif dist <= self.moves_left:
                    self.you.path.extend(make_path((self.you.r, self.you.c), cell))
                    self.moves_left -= dist

    # ---------------- AI (used by the rival, and by test bots) ----------------
    def ai_params(self, p):
        if p is self.rival:
            return self.persona
        return PERSONAS.get(self.you_persona or "fixer", PERSONAS["fixer"])

    def ai_easy(self, p):
        return self.easy and p is self.rival

    def ai_can_take(self, p, t):
        pr = self.ai_params(p)
        late = self.round >= pr["take_after"] * self.max_rounds
        return (late and t.lock == 0 and p.heat < pr["heat_cap"]
                and p.cash >= self.takeover_cost(t, p) + pr["take_reserve"])

    def ai_score(self, p, t, dist):
        pr = self.ai_params(p)
        o = self.other(p)
        s = self.rng.random() * 25_000
        if t.kind == "prop":
            if t.owner is None:
                n = len(self.props[t.did])
                s += t.price * 0.8
                mine = sum(1 for x in self.props[t.did] if x.owner is p)
                theirs = sum(1 for x in self.props[t.did] if x.owner is o)
                s += mine * t.price * 1.4              # build your own sets
                if mine == n - 1:
                    s += t.price * 1.5                 # this tile completes a district
                if theirs:
                    s += t.price * 0.3
                if theirs == n - 1:
                    s += t.price * 1.5                 # block the rival's last tile
                if p.acted or p.cash < t.price + pr["reserve"]:
                    s -= 10_000_000
            elif t.owner is p:
                if (not p.acted and t.level < MAX_LEVEL
                        and p.cash >= self.upgrade_cost(t) + pr["reserve"]):
                    s += t.price * 0.35 * pr["up"]
                else:
                    s -= t.price * 0.1
            else:
                if not p.acted and self.ai_can_take(p, t):
                    s += t.price * pr["take"]
                else:
                    s -= self.rent_of(t) * 1.6
        elif t.kind == "safe":
            s += max(0, p.heat - 30) * 6000
        elif t.kind == "market":
            if len(p.hand) < HAND_MAX and p.cash >= MARKET_PRICE + pr["reserve"]:
                s += 120_000 * (1.8 if pr["card_p"] > 0.9 else 1.0)
            else:
                s -= 40_000
        elif t.kind == "bank":
            s += 250_000 if (p.debt == 0 and p.cash < 600_000) else -40_000
        elif t.kind == "police":
            s -= 350_000
        s -= dist * 1500
        return s

    def ai_pick(self, p, roll):
        cands = [(r, c) for r in range(GRID) for c in range(GRID)
                 if abs(r - p.r) + abs(c - p.c) <= roll]
        if self.ai_easy(p):
            free = [rc for rc in cands
                    if self.tile_at(*rc).kind == "prop" and self.tile_at(*rc).owner is None]
            pool = free if free and self.rng.random() < 0.6 else cands
            return self.rng.choice(pool)
        best, best_s = (p.r, p.c), -1e18
        for r, c in cands:
            sc = self.ai_score(p, self.tile_at(r, c), abs(r - p.r) + abs(c - p.c))
            if sc > best_s:
                best, best_s = (r, c), sc
        return best

    def ai_act(self, p):
        pr = self.ai_params(p)
        t = self.tile_at(p.r, p.c)
        easy = self.ai_easy(p)
        if t.kind == "prop":
            if p.acted:
                return
            if t.owner is None:
                need = t.price + (1_200_000 if easy else pr["reserve"])
                if p.cash >= need and (not easy or self.rng.random() < 0.65):
                    self.act_buy(p)
            elif t.owner is p:
                if not easy and t.level < MAX_LEVEL and \
                        p.cash >= self.upgrade_cost(t) + pr["reserve"]:
                    self.act_buy(p)
            elif not easy and self.ai_can_take(p, t):
                self.act_takeover(p)
        elif t.kind == "bank":
            if p.debt == 0 and p.cash < 500_000:
                self.act_special(p, t)
        elif t.kind == "market" and not easy:
            while len(p.hand) < HAND_MAX and p.cash >= MARKET_PRICE + pr["reserve"]:
                if not self.act_special(p, t):
                    break

    def ai_cards(self, p, phase):
        if self.ai_easy(p) or p.played or not p.hand:
            return
        pr = self.ai_params(p)
        if self.rng.random() > pr["card_p"]:
            return
        o = self.other(p)
        t = self.tile_at(p.r, p.c)
        for idx, key in enumerate(p.hand):
            if self.block_reason(p, idx, phase) is not None:
                continue
            use = False
            if key == "dice":
                use = True
            elif key == "bribe":
                use = len(self.owned(o)) >= 3
            elif key == "sabotage":
                use = any(x.level >= 2 for x in self.owned(o))
            elif key == "lockdown":
                mine = sum(1 for x in self.props[t.did] if x.owner is p)
                use = t.level >= 2 or mine >= 3
            elif key == "smuggle":
                use = o.cash >= 1_000_000
            elif key == "laylow":
                use = p.heat >= 40
            if use:
                self.play_card(p, idx, phase)
                return

    def update_rival(self):
        rv = self.rival
        if self.r_stage == "roll":
            self.r_timer -= 1
            if self.r_timer <= 0:
                self.ai_cards(rv, "ROLL")
                roll = self.roll_value(rv)
                self.dice = roll
                self.say(f"Rival rolled a {roll}")
                tgt = self.ai_pick(rv, roll)
                rv.path = deque(make_path((rv.r, rv.c), tgt))
                self.r_stage = "move"
        elif self.r_stage == "move":
            if rv.idle:
                self.land(rv)
                self.r_stage = "act"
                self.r_timer = 35
        elif self.r_stage == "act":
            self.r_timer -= 1
            if self.r_timer <= 0:
                self.ai_cards(rv, "ACTION")
                self.ai_act(rv)
                self.r_stage = "end"
                self.r_timer = 40
        elif self.r_stage == "end":
            self.r_timer -= 1
            if self.r_timer <= 0:
                self.end_round()

    # ---------------- win / lose ----------------
    def check_end(self):
        if self.state == "GAMEOVER":
            return
        for p in (self.you, self.rival):
            if p.cash < 0 and not self.owned(p):
                if p is self.you:
                    self.finish("DEFEAT", "You went bankrupt. The syndicate is done.")
                else:
                    self.finish("VICTORY", "Rival bankrupted. The city is yours.")
                return
        for p in (self.you, self.rival):
            if self.empire(p) >= self.goal:
                if p is self.you:
                    self.finish("VICTORY", f"You control {self.goal} districts. Total empire.")
                else:
                    self.finish("DEFEAT", f"Rival controls {self.goal} districts.")
                return
        if self.round > self.max_rounds:
            a, b = self.net_worth(self.you), self.net_worth(self.rival)
            if a >= b:
                self.finish("VICTORY", f"Richest after {self.max_rounds} rounds: {fmt(a)} vs {fmt(b)}")
            else:
                self.finish("DEFEAT", f"Rival is richer: {fmt(b)} vs {fmt(a)}")

    def finish(self, title, sub):
        self.result = (title, sub)
        self.state = "GAMEOVER"
        self.snd("win" if title == "VICTORY" else "lose")

    def score(self):
        """Daily challenge score: wealth + districts + a bonus for winning."""
        won = bool(self.result) and self.result[0] == "VICTORY"
        return int(self.net_worth(self.you) + 1_000_000 * self.empire(self.you)
                   + (2_000_000 if won else 0))

    def copy_share(self):
        text = self.report.get("share") if self.report else None
        if not text:
            return
        try:
            pygame.scrap.init()
            pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8"))
            self.toast_text, self.toast_t = "Result copied to the clipboard", 150
        except Exception:
            self.toast_text, self.toast_t = "Copy is not available here: screenshot it instead", 200

    # ---------------- update ----------------
    def update(self):
        self.tick += 1
        self.you.update()
        self.rival.update()

        if self.state == "ROLL" and self.dice_timer > 0:
            self.dice_timer -= 1
            if self.dice_timer == 0:
                self.dice = self.roll_value(self.you)
                self.moves_left = self.dice
                self.want_land = False
                self.state = "MOVE"
                self.say(f"You rolled a {self.dice}. Move or stop.")

        if self.state == "MOVE" and self.you.idle and (self.moves_left == 0 or self.want_land):
            self.moves_left = 0
            self.want_land = False
            ev = self.land(self.you)
            self.say(f"You landed on {self.tile_at(self.you.r, self.you.c).name}")
            if ev:
                self.event = ev
                self.state = "EVENT"
            else:
                self.state = "ACTION"

        if self.state == "RIVAL":
            self.update_rival()

        for f in self.floaters:
            f[2] -= 0.7
            f[4] -= 1
        self.floaters = [f for f in self.floaters if f[4] > 0]
        for p in self.particles:
            p[0] += p[2]
            p[1] += p[3]
            p[3] += 0.12
            p[4] -= 1
        self.particles = [p for p in self.particles if p[4] > 0]
        if self.shake > 0:
            self.shake -= 1
        if self.toast_t > 0:
            self.toast_t -= 1

        self.check_end()

    # ---------------- coach bar ----------------
    def coach(self):
        st, me = self.state, self.you
        tut = self.mode == "tutorial"
        if st == "GAMEOVER":
            return "", False
        if st == "RIVAL":
            return ("Rival's turn. Watch what they buy: you can take it over later." if tut
                    else "Rival is moving..."), tut
        if st == "EVENT":
            return "Press ENTER to continue.", False
        mine = len(self.owned(me))
        t = self.tile_at(me.r, me.c)
        if tut:
            if st == "ROLL":
                if self.round == 1 and mine == 0:
                    return "1/5  Press SPACE (or click the dice) to roll.", True
                playable = any(self.block_reason(me, i, "ROLL") is None for i in range(len(me.hand)))
                if playable and self.stats["cards"] == 0 and self.round >= 2:
                    return "5/5  Cards: press 1, 2 or 3 to play one before you roll (one per turn).", True
            if st == "MOVE" and mine == 0:
                return "2/5  Click a glowing tile (or use the arrow keys), then press SPACE to stop.", True
            if st == "ACTION":
                if t.kind == "prop" and t.owner is None and not me.acted:
                    return "3/5  Press B to buy the tile you are standing on.", True
                if me.acted and mine <= 2 and self.round <= 3:
                    return "4/5  Own EVERY tile of a district for double rent. SPACE ends your turn.", True
        if st == "ROLL":
            return "SPACE: roll   |   1-3: play a card first (one per turn)", False
        if st == "MOVE":
            return f"Click a glowing tile or use arrows. SPACE: stop here ({self.moves_left} steps left)", False
        tail = "   |   1-3: card   |   SPACE: end turn"
        if t.kind == "prop":
            if t.owner is None:
                head = f"B: buy for {fmt(t.price)}"
            elif t.owner is me:
                head = f"B: upgrade for {fmt(self.upgrade_cost(t))}" if t.level < MAX_LEVEL else "Max level"
            else:
                head = f"T: take over for {fmt(self.takeover_cost(t, me))}"
                if t.lock:
                    head = f"Locked down for {t.lock} more round(s)"
            if me.acted:
                head = "Build action used this turn"
            return head + tail, False
        return SPECIAL_INFO[t.kind][3] + tail, False

    # ---------------- drawing ----------------
    def draw(self, s):
        s.fill(BG)
        self.draw_board(s)
        self.draw_tokens(s)
        self.draw_fx(s)
        self.draw_coach(s)
        self.draw_panel(s)
        if self.state == "EVENT" and self.event:
            self.draw_event(s)
        if self.state == "GAMEOVER":
            self.draw_gameover(s)

    def draw_board(self, s):
        reach = set()
        if self.state == "MOVE" and self.you.idle and self.moves_left > 0:
            for r in range(GRID):
                for c in range(GRID):
                    d = abs(r - self.you.r) + abs(c - self.you.c)
                    if 0 < d <= self.moves_left:
                        reach.add((r, c))
        hi = pygame.Surface((TILE, TILE), pygame.SRCALPHA)
        for r in range(GRID):
            for c in range(GRID):
                t = self.board[r][c]
                x, y = BX + c * TILE, BY + r * TILE
                d = DISTRICTS[t.did]
                pygame.draw.rect(s, d["dark"] if (r + c) % 2 else d["color"], (x, y, TILE, TILE))
                box = (x + 6, y + 6, TILE - 12, TILE - 12)
                if t.kind != "prop":
                    col = SPECIAL_INFO[t.kind][2]
                    pygame.draw.rect(s, col, box, border_radius=8)
                    pygame.draw.rect(s, shade(col, 0.6), box, 2, border_radius=8)
                    label = F_XS.render(SPECIAL_INFO[t.kind][1], True, WHITE)
                elif t.owner:
                    pygame.draw.rect(s, t.owner.color, box, border_radius=8)
                    pygame.draw.rect(s, shade(t.owner.color, 0.6), box, 2, border_radius=8)
                    label = F_XS.render("R " + fmt(self.rent_of(t)), True, WHITE)
                    for i in range(t.level):
                        px = x + TILE / 2 + (i - (t.level - 1) / 2) * 11
                        pygame.draw.circle(s, WHITE, (int(px), y + TILE - 15), 3)
                    if t.lock:
                        lk = F_XS.render("LOCK", True, GOLD)
                        blit_center(s, lk, x + TILE // 2, y + 11)
                else:
                    label = F_XS.render(fmt(t.price), True, (235, 235, 235))
                blit_center(s, label, x + TILE // 2, y + TILE // 2 - 12)
                if (r, c) in reach:
                    hi.fill((255, 230, 80, 70))
                    s.blit(hi, (x, y))
                if self.hover == (r, c) and self.state in ("MOVE", "ACTION", "ROLL"):
                    hi.fill((255, 255, 255, 50))
                    s.blit(hi, (x, y))
        for did in range(16):
            br, bc = divmod(did, 4)
            rect = (BX + bc * 2 * TILE, BY + br * 2 * TILE, 2 * TILE, 2 * TILE)
            first = self.props[did][0].owner
            if first and self.has_monopoly(first, did):
                pygame.draw.rect(s, GOLD, rect, 4)
            else:
                pygame.draw.rect(s, shade(DISTRICTS[did]["color"], 1.4), rect, 2)

    def draw_tokens(self, s):
        same = (self.you.r, self.you.c) == (self.rival.r, self.rival.c) and self.you.idle and self.rival.idle
        for p, off in ((self.you, -10 if same else 0), (self.rival, 10 if same else 0)):
            x, y = int(p.px) + off, int(p.py) + off
            pygame.draw.circle(s, (10, 10, 10), (x + 2, y + 4), 20)
            active = (p is self.you and self.state in ("MOVE", "ACTION", "ROLL")) or \
                     (p is self.rival and self.state == "RIVAL")
            if active:
                pulse = 24 + int(3 * math.sin(self.tick * 0.2))
                pygame.draw.circle(s, GOLD, (x, y), pulse, 2)
            pygame.draw.circle(s, shade(p.color, 0.55), (x, y), 20)
            pygame.draw.circle(s, p.color, (x, y), 17)
            ch = F_M.render("Y" if p is self.you else "R", True, WHITE)
            s.blit(ch, (x - ch.get_width() // 2, y - ch.get_height() // 2))

    def draw_fx(self, s):
        for x, y, vx, vy, life, col in self.particles:
            pygame.draw.rect(s, col, (int(x), int(y), 4, 4))
        for text, x, y, col, life in self.floaters:
            surf = F_M.render(text, True, col)
            surf.set_alpha(min(255, life * 6))
            s.blit(surf, (int(x - surf.get_width() / 2), int(y)))

    def draw_coach(self, s):
        text, tut = self.coach()
        if not text:
            return
        r = pygame.Rect(BX, BY + BOARD + 8, BOARD, 32)
        pygame.draw.rect(s, PANEL, r, border_radius=6)
        if tut:
            pygame.draw.rect(s, GOLD, r, 2, border_radius=6)
        while F_S.size(text)[0] > r.w - 20 and len(text) > 4:
            text = text[:-4] + ".."
        surf = F_S.render(text, True, GOLD if tut else MUTED)
        s.blit(surf, (r.x + 10, r.centery - surf.get_height() // 2))

    def bar(self, s, x, y, w, h, frac, col):
        pygame.draw.rect(s, PANEL_DARK, (x, y, w, h), border_radius=3)
        pygame.draw.rect(s, col, (x, y, int(w * max(0, min(1, frac))), h), border_radius=3)

    def draw_player_card(self, s, p, y):
        pygame.draw.rect(s, PANEL, (PX, y, PW, 80), border_radius=6)
        pygame.draw.rect(s, p.color, (PX, y, 5, 80), border_radius=3)
        leader = LEADERS[p.leader]["name"].replace("THE ", "")
        title = f"YOU - {leader}" if p is self.you else f"RIVAL - {self.persona['name']}"
        s.blit(F_M.render(title, True, p.color), (PX + 14, y + 7))
        cash = F_L.render(fmt(p.cash), True, GREEN if p.cash >= 0 else RED)
        s.blit(cash, (PX + PW - cash.get_width() - 10, y + 5))
        info = f"Worth {fmt(self.net_worth(p))}  Tiles {len(self.owned(p))}  Dist {self.empire(p)}/{self.goal}"
        s.blit(F_XS.render(info, True, MUTED), (PX + 14, y + 32))
        x = PX + 14
        tags = []
        if p is self.rival:
            tags.append((leader.title(), MUTED))
        if self.underdog(p):
            tags.append(("UNDERDOG", GOLD))
        if p.debt:
            tags.append((f"DEBT {fmt(p.debt)}", RED))
        if p.shield:
            tags.append(("BRIBE", GREEN))
        if p.double:
            tags.append(("2xDICE", GREEN))
        if p.halve:
            tags.append(("HALF ROLL", RED))
        for text, col in tags:
            surf = F_XS.render(text, True, col)
            s.blit(surf, (x, y + 47))
            x += surf.get_width() + 10
        s.blit(F_XS.render("HEAT", True, MUTED), (PX + 14, y + 62))
        hc = (240, 200, 60) if p.heat < 60 else RED
        self.bar(s, PX + 52, y + 63, PW - 66, 10, p.heat / 100, hc)

    def draw_hand(self, s):
        rc = self.rects()
        phase = self.state if self.state in ("ROLL", "ACTION") else None
        hovered = None
        for i in range(HAND_MAX):
            r = rc[f"card{i}"]
            over = r.collidepoint(self.mouse)
            if i < len(self.you.hand):
                d = CARD_DEFS[self.you.hand[i]]
                ok = self.block_reason(self.you, i, phase) is None
                col = d["color"] if ok else shade(d["color"], 0.35)
                pygame.draw.rect(s, col, r, border_radius=6)
                pygame.draw.rect(s, WHITE if (ok and over) else shade(col, 1.5), r, 2 if over else 1, border_radius=6)
                s.blit(F_XS.render(str(i + 1), True, WHITE if ok else MUTED), (r.x + 7, r.y + 5))
                nm = F_S.render(d["name"], True, WHITE if ok else MUTED)
                blit_center(s, nm, r.centerx, r.y + 24)
                st = F_XS.render("READY" if ok else "locked", True, (235, 235, 235) if ok else MUTED)
                blit_center(s, st, r.centerx, r.y + 44)
                if over:
                    hovered = i
            else:
                pygame.draw.rect(s, PANEL_DARK, r, border_radius=6)
                pygame.draw.rect(s, EDGE, r, 1, border_radius=6)
                blit_center(s, F_XS.render("empty", True, EDGE), r.centerx, r.centery - 6)
        if hovered is not None:
            d = CARD_DEFS[self.you.hand[hovered]]
            why = self.block_reason(self.you, hovered, phase)
            text = f"{d['name']}: {d['desc']}" if why is None else why
        else:
            text = "Cards: one per turn. You draw a new one each round (hand max 3)."
        for i, ln in enumerate(wrap(text, F_XS, PW - 4)[:2]):
            s.blit(F_XS.render(ln, True, MUTED), (PX + 2, 397 + i * 13))

    def draw_panel(self, s):
        pygame.draw.rect(s, PANEL, (PX, 20, PW, 42), border_radius=6)
        blit_center(s, F_M.render("HOSTILE TAKEOVER", True, WHITE), PX + PW // 2, 24)
        label = "DAILY" if self.daily else MODES[self.mode]["label"]
        sub = (f"ROUND {min(self.round, self.max_rounds)}/{self.max_rounds}  -  "
               f"{label}  -  [M] sound {'ON' if self.sound_on else 'OFF'}")
        blit_center(s, F_XS.render(sub, True, MUTED), PX + PW // 2, 44)

        self.draw_player_card(s, self.you, 68)
        self.draw_player_card(s, self.rival, 154)

        tile = self.tile_at(*self.hover) if self.hover else self.tile_at(self.you.r, self.you.c)
        pygame.draw.rect(s, PANEL, (PX, 240, PW, 80), border_radius=6)
        pygame.draw.rect(s, DISTRICTS[tile.did]["color"], (PX, 240, 5, 80), border_radius=3)
        s.blit(F_M.render(tile.name, True, WHITE), (PX + 14, 246))
        if tile.kind != "prop":
            lines = wrap(SPECIAL_INFO[tile.kind][3], F_XS, PW - 28)[:4]
        elif tile.owner:
            n = len(self.props[tile.did])
            own = sum(1 for x in self.props[tile.did] if x.owner is tile.owner)
            payer = self.you if tile.owner is self.rival else None
            lines = [f"Owner: {tile.owner.name}   Level {tile.level}" + (f"   LOCK {tile.lock}" if tile.lock else ""),
                     f"Rent {fmt(self.rent_of(tile, payer))}   Takeover {fmt(self.takeover_cost(tile, self.you))}",
                     f"District set: {own}/{n}" + ("  (x2 MONOPOLY)" if own == n else "")]
        else:
            n = len(self.props[tile.did])
            lines = [f"Unowned   Price {fmt(tile.price)}",
                     f"Base rent {fmt(tile.price * 0.25)}",
                     f"Own all {n} tiles = double rent + income"]
        for i, ln in enumerate(lines):
            s.blit(F_XS.render(ln, True, MUTED), (PX + 14, 268 + i * 15))

        self.draw_hand(s)

        rc = self.rects()
        rolling = self.dice_timer > 0 or self.r_stage == "roll"
        val = random.randint(1, 6) if rolling else self.dice
        pygame.draw.rect(s, (10, 10, 10), rc["dice"].move(0, 3), border_radius=10)
        pygame.draw.rect(s, (245, 243, 239), rc["dice"], border_radius=10)
        dv = F_XL.render(str(val) if val else "-", True, (30, 29, 27))
        s.blit(dv, (rc["dice"].centerx - dv.get_width() // 2, rc["dice"].centery - dv.get_height() // 2))

        if self.state == "ROLL":
            label = "ROLL DICE  [SPACE]" + ("  x2" if self.you.double else "")
            self.button(s, rc["roll"], label, GREEN, self.dice_timer == 0)
        elif self.state == "MOVE":
            self.button(s, rc["roll"], f"STOP HERE  ({self.moves_left} left)", GOLD, True)
        elif self.state == "RIVAL":
            self.button(s, rc["roll"], "RIVAL'S TURN...", EDGE, False)
        else:
            self.button(s, rc["roll"], "CHOOSE AN ACTION", EDGE, False)

        mine = self.tile_at(self.you.r, self.you.c)
        act = self.state == "ACTION"
        me = self.you
        if mine.kind == "bank":
            buy_label, can_buy = f"LOAN +{fmt(LOAN_AMOUNT)} [B]", act and me.debt == 0
        elif mine.kind == "market":
            buy_label, can_buy = f"BUY CARD {fmt(MARKET_PRICE)} [B]", act and len(me.hand) < HAND_MAX
        elif mine.kind != "prop":
            buy_label, can_buy = "BUY [B]", False
        elif mine.owner is me:
            buy_label, can_buy = f"UPGRADE {fmt(self.upgrade_cost(mine))} [B]", act and not me.acted and mine.level < MAX_LEVEL
        else:
            buy_label, can_buy = f"BUY {fmt(mine.price)} [B]", act and not me.acted and mine.owner is None
        self.button(s, rc["buy"], buy_label, GREEN, can_buy)
        on_rival = mine.kind == "prop" and mine.owner is self.rival
        take_label = f"TAKEOVER {fmt(self.takeover_cost(mine, me))} [T]" if on_rival else "TAKEOVER [T]"
        self.button(s, rc["take"], take_label, RED, act and on_rival and not me.acted and mine.lock == 0)
        self.button(s, rc["end"], "END TURN  [SPACE]", (90, 90, 200), act)

        pygame.draw.rect(s, PANEL_DARK, (PX, 572, PW, 128), border_radius=6)
        shown = self.log[-8:]
        for i, msg in enumerate(shown):
            while F_XS.size(msg)[0] > PW - 18 and len(msg) > 4:
                msg = msg[:-4] + ".."
            col = WHITE if i == len(shown) - 1 else MUTED
            s.blit(F_XS.render(msg, True, col), (PX + 9, 578 + i * 15))

    def button(self, s, rect, label, col, enabled):
        c = col if enabled else (58, 56, 52)
        pygame.draw.rect(s, c, rect, border_radius=6)
        txt = F_S.render(label, True, WHITE if enabled else (110, 108, 103))
        s.blit(txt, (rect.centerx - txt.get_width() // 2, rect.centery - txt.get_height() // 2))

    def draw_overlay(self, s, alpha):
        o = pygame.Surface((W, H))
        o.set_alpha(alpha)
        o.fill((0, 0, 0))
        s.blit(o, (0, 0))

    def draw_event(self, s):
        self.draw_overlay(s, 170)
        r = pygame.Rect(W // 2 - 190, H // 2 - 100, 380, 200)
        pygame.draw.rect(s, (48, 46, 42), r, border_radius=8)
        pygame.draw.rect(s, (96, 90, 78), r, 2, border_radius=8)
        e = self.event
        blit_center(s, F_S.render("UNDERWORLD ENCOUNTER", True, MUTED), r.centerx, r.y + 18)
        blit_center(s, F_L.render(e["title"], True, WHITE), r.centerx, r.y + 46)
        blit_center(s, F_S.render(e["desc"], True, MUTED), r.centerx, r.y + 78)
        y = r.y + 108
        if e["cash"]:
            col = GREEN if e["cash"] > 0 else RED
            blit_center(s, F_L.render(("+" if e["cash"] > 0 else "") + fmt(e["cash"]), True, col), r.centerx, y)
            y += 28
        if e["heat"]:
            col = RED if e["heat"] > 0 else GREEN
            blit_center(s, F_S.render(f"Heat {'+' if e['heat'] > 0 else ''}{e['heat']}", True, col), r.centerx, y)
        b = pygame.Rect(r.centerx - 80, r.bottom - 38, 160, 28)
        pygame.draw.rect(s, GREEN, b, border_radius=5)
        bt = F_S.render("CONTINUE [ENTER]", True, WHITE)
        s.blit(bt, (b.centerx - bt.get_width() // 2, b.centery - bt.get_height() // 2))

    def draw_gameover(self, s):
        self.draw_overlay(s, 205)
        title, sub = self.result
        win = title == "VICTORY"
        col = GREEN if win else RED
        r = pygame.Rect(W // 2 - 300, H // 2 - 215, 600, 430)
        pygame.draw.rect(s, PANEL, r, border_radius=10)
        pygame.draw.rect(s, col, r, 3, border_radius=10)
        blit_center(s, F_XL.render(title, True, col), r.centerx, r.y + 14)
        blit_center(s, F_S.render(sub, True, MUTED), r.centerx, r.y + 58)

        pygame.draw.rect(s, PANEL_DARK, (r.x + 24, r.y + 82, r.w - 48, 58), border_radius=6)
        s.blit(F_XS.render("BIGGEST MOMENT", True, GOLD), (r.x + 38, r.y + 90))
        moment = self.best_moment[1] or "A quiet game. Next time, make some noise."
        for i, ln in enumerate(wrap(moment, F_M, r.w - 80)[:2]):
            s.blit(F_M.render(ln, True, WHITE), (r.x + 38, r.y + 107 + i * 17))

        st = self.stats
        line = (f"Your tiles {len(self.owned(self.you))}   Takeovers {st['takeovers']}   "
                f"Districts {st['monopolies']}   Cards {st['cards']}   Rent {fmt(st['rent'])}")
        blit_center(s, F_S.render(line, True, MUTED), r.centerx, r.y + 150)

        rep = self.report
        if rep:
            lb, ib, nb = rep["before"]
            la, ia, na = rep["after"]
            lvl_line = f"+{rep['xp']} XP   -   LEVEL {la}" + ("   LEVEL UP!" if la > lb else "")
            blit_center(s, F_M.render(lvl_line, True, GREEN if la > lb else GOLD), r.centerx, r.y + 176)
            self.bar(s, r.x + 100, r.y + 198, r.w - 200, 10, ia / na, GOLD)
            blit_center(s, F_XS.render(f"{ia}/{na} XP to next level", True, MUTED), r.centerx, r.y + 212)
            unlocked = [LEADERS[k]["name"] for k in LEADER_KEYS if lb < LEADERS[k]["unlock"] <= la]
            y = r.y + 232
            if unlocked:
                blit_center(s, F_S.render("NEW CREW LEADER: " + ", ".join(unlocked), True, GOLD), r.centerx, y)
                y += 18
            t0, t1 = rep["trophies"]
            if t0 != t1 or (self.mode in TROPHY_RULES and not self.daily):
                rank0, rank1 = rep["rank"]
                tl = f"Trophies {t0} -> {t1} ({t1 - t0:+d})   Rank: {rank1}"
                col_t = GREEN if t1 > t0 else (RED if t1 < t0 else MUTED)
                blit_center(s, F_S.render(tl, True, col_t), r.centerx, y)
                y += 18
                if rank0 != rank1:
                    word = "RANK UP" if t1 > t0 else "RANK DOWN"
                    blit_center(s, F_M.render(f"{word}: {rank1.upper()}", True, GREEN if t1 > t0 else RED),
                                r.centerx, y)
                    y += 20
            if rep["daily"]:
                dd = rep["daily"]
                tag = "  NEW BEST!" if dd["new_best"] else f"  (best {fmt(dd['best'])})"
                blit_center(s, F_M.render(f"Daily score {fmt(dd['score'])}{tag}", True, GOLD), r.centerx, y)
                y += 18
                blit_center(s, F_XS.render("[C] copy your result to share it", True, MUTED), r.centerx, y)
                y += 16
            for text in rep["contracts"][:3]:
                blit_center(s, F_S.render(f"Contract complete: {text}  (+60 XP)", True, GREEN), r.centerx, y)
                y += 16
        if self.toast_t > 0:
            blit_center(s, F_S.render(self.toast_text, True, GOLD), r.centerx, r.bottom - 54)
        blit_center(s, F_S.render("[R] rematch      [ENTER] menu      [ESC] quit", True, WHITE), r.centerx, r.bottom - 30)


# --------------------------------------------------------------------------
# Menu
# --------------------------------------------------------------------------
def how_to_play():
    goal = MODES["standard"]["goal"]
    return [
        "ROLL the dice, then walk up to that many tiles (click a glowing tile or use the arrows).",
        "BUY tiles. Own every tile of a district: double rent and double dividends.",
        "One BUILD action per turn: buy, upgrade or take over a rival tile.",
        "CARDS: one per turn (keys 1-3). You draw one each round.",
        "HEAT: reckless moves raise it. At 100 the Feds raid you for 25% of your cash.",
        "Special tiles: Safehouse, Bank, Black Market, Police Station.",
        f"WIN: own {goal} full districts, bankrupt the rival, or lead after the last round.",
    ]


RIVAL_CHOICES = PERSONA_KEYS + ["random"]


def menu_layout():
    return {
        "tutorial": pygame.Rect(40, 568, 215, 58),
        "standard": pygame.Rect(270, 568, 215, 58),
        "blitz": pygame.Rect(500, 568, 215, 58),
        "daily": pygame.Rect(730, 568, 210, 58),
        "leaders": [pygame.Rect(150 + i * 200, 414, 190, 32) for i in range(4)],
        "rivals": [pygame.Rect(150 + i * 200, 480, 190, 32) for i in range(4)],
    }


def draw_menu(s, profile, riv_choice, leader_choice, tick):
    s.fill(BG)
    blit_center(s, F_XL.render("HOSTILE TAKEOVER", True, WHITE), W // 2, 14)
    blit_center(s, F_M.render("Syndicate Edition  -  a mafia territory war", True, MUTED), W // 2, 58)
    lay = menu_layout()
    d = profile.d

    # ---- how to play ----
    lp = pygame.Rect(40, 90, 430, 310)
    pygame.draw.rect(s, PANEL, lp, border_radius=8)
    s.blit(F_M.render("HOW TO PLAY", True, GOLD), (lp.x + 18, lp.y + 12))
    y = lp.y + 40
    for line in how_to_play():
        for ln in wrap(line, F_S, lp.w - 36):
            s.blit(F_S.render(ln, True, WHITE), (lp.x + 18, y))
            y += 16
        y += 6

    # ---- profile ----
    rp = pygame.Rect(490, 90, 450, 310)
    pygame.draw.rect(s, PANEL, rp, border_radius=8)
    lvl, into, need = level_info(d["xp"])
    s.blit(F_M.render(f"LEVEL {lvl}", True, GOLD), (rp.x + 18, rp.y + 12))
    xp_t = F_XS.render(f"{into}/{need} XP", True, MUTED)
    s.blit(xp_t, (rp.right - 18 - xp_t.get_width(), rp.y + 15))
    pygame.draw.rect(s, PANEL_DARK, (rp.x + 18, rp.y + 34, rp.w - 36, 10), border_radius=4)
    pygame.draw.rect(s, GOLD, (rp.x + 18, rp.y + 34, int((rp.w - 36) * into / need), 10), border_radius=4)

    trophies = d["trophies"]
    rank, floor, nxt = rank_info(trophies)
    s.blit(F_M.render(f"RANK  {rank}", True, WHITE), (rp.x + 18, rp.y + 54))
    right = f"{trophies} trophies" + (f"  -  next rank at {nxt}" if nxt else "  -  top rank")
    rt = F_XS.render(right, True, MUTED)
    s.blit(rt, (rp.right - 18 - rt.get_width(), rp.y + 58))
    frac = 1.0 if nxt is None else (trophies - floor) / (nxt - floor)
    pygame.draw.rect(s, PANEL_DARK, (rp.x + 18, rp.y + 76, rp.w - 36, 8), border_radius=3)
    pygame.draw.rect(s, GREEN, (rp.x + 18, rp.y + 76, int((rp.w - 36) * frac), 8), border_radius=3)

    stat = (f"Matches {d['matches']}   Wins {d['wins']}   "
            f"Daily streak {d['streak']} day(s)   Best {d['best_streak']}")
    s.blit(F_XS.render(stat, True, MUTED), (rp.x + 18, rp.y + 94))
    edge = profile.handicap()["cash"]
    edge_txt = (f"Rival starts with +{fmt(edge)} cash at your rank. Climb to face tougher rivals."
                if edge else "Rival edge: none yet. Win ranked matches to climb.")
    s.blit(F_XS.render(edge_txt, True, MUTED), (rp.x + 18, rp.y + 110))

    s.blit(F_M.render("DAILY CONTRACTS", True, GOLD), (rp.x + 18, rp.y + 132))
    dl = d["dailies"]
    y = rp.y + 158
    for c in profile.contracts():
        done = c["id"] in dl["done"]
        cur = min(dl["progress"].get(c["id"], 0), c["target"])
        mark = "[x] " if done else "[ ] "
        s.blit(F_S.render(mark + c["text"], True, GREEN if done else WHITE), (rp.x + 18, y))
        pt = f"{fmt(cur)}/{fmt(c['target'])}" if c["key"] == "rent" else f"{cur}/{c['target']}"
        ptr = F_XS.render(pt, True, MUTED)
        s.blit(ptr, (rp.right - 18 - ptr.get_width(), y + 2))
        pygame.draw.rect(s, PANEL_DARK, (rp.x + 18, y + 19, rp.w - 36, 7), border_radius=3)
        pygame.draw.rect(s, GREEN if done else GOLD,
                         (rp.x + 18, y + 19, int((rp.w - 36) * cur / c["target"]), 7), border_radius=3)
        y += 40
    s.blit(F_XS.render("Each contract is +60 XP. New contracts every day. The tutorial does not count.",
                       True, MUTED), (rp.x + 18, rp.bottom - 20))

    # ---- crew leader ----
    s.blit(F_S.render("LEADER", True, GOLD), (50, 423))
    for i, key in enumerate(LEADER_KEYS):
        r = lay["leaders"][i]
        locked = not profile.unlocked(key)
        sel = key == leader_choice and not locked
        pygame.draw.rect(s, GREEN if sel else (PANEL_DARK if locked else PANEL), r, border_radius=6)
        pygame.draw.rect(s, GREEN if sel else EDGE, r, 1, border_radius=6)
        label = f"LOCKED - LEVEL {LEADERS[key]['unlock']}" if locked else LEADERS[key]["name"]
        blit_center(s, F_S.render(label, True, WHITE if sel else MUTED), r.centerx, r.centery - 7)
    blit_center(s, F_S.render(LEADERS[leader_choice]["blurb"], True, MUTED), W // 2, 454)

    # ---- rival ----
    s.blit(F_S.render("RIVAL", True, GOLD), (50, 489))
    for i, key in enumerate(RIVAL_CHOICES):
        r = lay["rivals"][i]
        sel = key == riv_choice
        pygame.draw.rect(s, GREEN if sel else PANEL, r, border_radius=6)
        pygame.draw.rect(s, GREEN if sel else EDGE, r, 1, border_radius=6)
        name = PERSONAS[key]["name"] if key in PERSONAS else "RANDOM"
        blit_center(s, F_S.render(name, True, WHITE if sel else MUTED), r.centerx, r.centery - 7)
    if riv_choice in PERSONAS:
        lname = LEADERS[PERSONA_LEADER[riv_choice]]["name"].title()
        blurb = f"{PERSONAS[riv_choice]['blurb']}  Leader: {lname}."
    else:
        blurb = "A different rival every match."
    blit_center(s, F_S.render(blurb, True, MUTED), W // 2, 520)

    # ---- start buttons ----
    new = not d["tutorial_done"]
    dd = d["daily"]
    daily_sub = f"today's best: {fmt(dd['best'])}" if dd["plays"] else "same game for everyone today"
    specs = [("tutorial", "TUTORIAL", "easy rival, learn the basics"),
             ("standard", "STANDARD", f"{MODES['standard']['rounds']} rounds, {MODES['standard']['goal']} districts to win"),
             ("blitz", "BLITZ", f"{MODES['blitz']['rounds']} rounds, {MODES['blitz']['goal']} districts to win"),
             ("daily", "DAILY CHALLENGE", daily_sub)]
    for key, name, subtitle in specs:
        r = lay[key]
        col = {"tutorial": GREEN if new else (60, 110, 80), "standard": (70, 70, 140),
               "blitz": (150, 90, 40), "daily": (40, 120, 130)}[key]
        pygame.draw.rect(s, col, r, border_radius=8)
        if key == "tutorial" and new:
            pulse = 3 + int(2 * math.sin(tick * 0.1))
            pygame.draw.rect(s, GOLD, r.inflate(pulse * 2, pulse * 2), 2, border_radius=10)
        blit_center(s, F_L.render(name, True, WHITE), r.centerx, r.y + 9)
        blit_center(s, F_XS.render(subtitle, True, (235, 235, 235)), r.centerx, r.y + 38)
    if new:
        blit_center(s, F_S.render("New here? Start with the tutorial.", True, GOLD), 148, 638)
    elif not dd["plays"]:
        blit_center(s, F_S.render("Today's daily challenge is waiting.", True, GOLD), 835, 638)
    blit_center(s, F_XS.render("[1] tutorial   [2] standard   [3] blitz   [4] daily   [M] sound   [ESC] quit",
                               True, MUTED), W // 2, 692)


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def main():
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Hostile Takeover - Syndicate Edition")
    canvas = pygame.Surface((W, H))
    clock = pygame.time.Clock()
    snd = Sound()
    profile = Profile(SAVE_PATH)

    app = {"game": None, "mode": "standard", "tick": 0,
           "riv": profile.d.get("rival", "banker"), "leader": profile.d.get("leader", "don")}
    if app["riv"] not in RIVAL_CHOICES:
        app["riv"] = "banker"
    if not profile.unlocked(app["leader"]):
        app["leader"] = "don"

    def start(mode):
        profile.touch_day()                   # in case midnight passed while the menu was open
        if mode == "daily":
            ds = profile.d["daily"]["date"]
            app["game"] = Game("standard", daily_rival(ds), seed=daily_seed(ds), daily=True)
        else:
            key = app["riv"]
            if key == "random":
                key = random.choice(PERSONA_KEYS)
            leader = app["leader"] if profile.unlocked(app["leader"]) else "don"
            hc = None if mode == "tutorial" else profile.handicap()
            app["game"] = Game(mode, key, you_leader=leader, handicap=hc)
        app["mode"] = mode
        profile.d["rival"], profile.d["leader"] = app["riv"], app["leader"]
        profile.save()

    def quit_game():
        pygame.quit()
        sys.exit()

    while True:
        clock.tick(FPS)
        app["tick"] += 1
        game = app["game"]
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                quit_game()
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_m:
                snd.muted = not snd.muted
                continue
            game = app["game"]
            if game is None:
                lay = menu_layout()
                if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    for i, r in enumerate(lay["rivals"]):
                        if r.collidepoint(ev.pos):
                            app["riv"] = RIVAL_CHOICES[i]
                    for i, r in enumerate(lay["leaders"]):
                        if r.collidepoint(ev.pos) and profile.unlocked(LEADER_KEYS[i]):
                            app["leader"] = LEADER_KEYS[i]
                    for key in ("tutorial", "standard", "blitz", "daily"):
                        if lay[key].collidepoint(ev.pos):
                            start(key)
                            break
                elif ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_ESCAPE:
                        quit_game()
                    elif ev.key == pygame.K_1:
                        start("tutorial")
                    elif ev.key in (pygame.K_2, pygame.K_RETURN, pygame.K_SPACE):
                        start("tutorial" if not profile.d["tutorial_done"] else "standard")
                    elif ev.key == pygame.K_3:
                        start("blitz")
                    elif ev.key == pygame.K_4:
                        start("daily")
                continue
            if ev.type == pygame.KEYDOWN:
                if game.state == "GAMEOVER":
                    if ev.key == pygame.K_r:
                        start(app["mode"])
                    elif ev.key == pygame.K_c:
                        game.copy_share()
                    elif ev.key == pygame.K_RETURN:
                        app["game"] = None
                    elif ev.key == pygame.K_ESCAPE:
                        quit_game()
                else:
                    game.handle_key(ev.key)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if game.state != "GAMEOVER":
                    game.click(ev.pos)
            elif ev.type == pygame.MOUSEMOTION:
                game.hover = game.tile_from_pos(ev.pos)
                game.mouse = ev.pos

        game = app["game"]
        if game is None:
            draw_menu(canvas, profile, app["riv"], app["leader"], app["tick"])
            screen.blit(canvas, (0, 0))
        else:
            game.sound_on = snd.ok and not snd.muted
            game.update()
            for name in game.drain_sfx():
                snd.play(name)
            if game.state == "GAMEOVER" and game.report is None:
                game.report = profile.record(game)
            game.draw(canvas)
            ox = oy = 0
            if game.shake > 0:
                ox, oy = random.randint(-4, 4), random.randint(-4, 4)
            screen.fill(BG)
            screen.blit(canvas, (ox, oy))
        pygame.display.flip()


if __name__ == "__main__":
    main()