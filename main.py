"""
HOSTILE TAKEOVER  -  Syndicate Edition
A turn-based mafia territory war in pygame.

Controls
  SPACE / ENTER ... roll dice / stop moving / end turn / dismiss popups
  Arrow keys / WASD  step one tile      (or CLICK a highlighted tile to walk there)
  B ... buy or upgrade the tile you stand on
  T ... hostile takeover of a rival tile you stand on
  R ... restart after game over
"""
import sys
import random
import math
import colorsys
from collections import deque

import pygame

pygame.init()
pygame.font.init()

# --------------------------------------------------------------------------
# Layout / tuning constants
# --------------------------------------------------------------------------
W, H = 940, 700
TILE = 80
GRID = 8
BOARD = TILE * GRID
BX, BY = 20, 20
PX = BX + BOARD + 20
PW = W - PX - 20
FPS = 60

START_CASH = 3_000_000
MAX_ROUNDS = 30
EMPIRE_GOAL = 5          # own this many full districts to win instantly
MOVE_SPEED = 7
MAX_LEVEL = 4
RENT_MULT = [1, 2.5, 4.5, 7]

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


# --------------------------------------------------------------------------
# Districts: 16 blocks of 2x2 tiles. Owning a whole block = monopoly.
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
        price = 200_000 + 50_000 * ((br * 3 + bc * 5) % 5) + (200_000 if center else 0)
        out.append({"name": name, "color": col, "dark": shade(col, 0.72), "price": price})
    return out


DISTRICTS = build_districts()

# (title, description, cash, heat, steal-from-rival)
CARDS = [
    ("Offshore Account Found", "A forgotten shell company pays out.", 300_000, 0, 0),
    ("Federal Audit", "You pay the auditors off quietly.", -200_000, 10, 0),
    ("Black Market Shipment", "The cargo cleared customs.", 400_000, 12, 0),
    ("Informant Payoff", "Buying silence before it spreads.", -150_000, -20, 0),
    ("Rival Stash Raided", "Your crew hits their safehouse.", 0, 10, 200_000),
    ("Warehouse Fire", "Repairs eat your budget.", -250_000, 0, 0),
    ("Police Tip-Off", "Heat is rising across the city.", 0, 30, 0),
    ("Laundering Success", "Dirty money comes out clean.", 150_000, -25, 0),
]


# --------------------------------------------------------------------------
# Core classes
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
        self.price = DISTRICTS[self.did]["price"]
        self.owner = None
        self.level = 1

    @property
    def name(self):
        return f"{DISTRICTS[self.did]['name']} #{(self.r % 2) * 2 + (self.c % 2) + 1}"


class Player:
    def __init__(self, name, r, c, color):
        self.name = name
        self.r, self.c = r, c
        self.color = color
        self.cash = START_CASH
        self.heat = 0
        self.px, self.py = cell_center(r, c)
        self.path = deque()

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
    def __init__(self):
        self.board = [[Tile(r, c) for c in range(GRID)] for r in range(GRID)]
        self.districts = {i: [] for i in range(16)}
        for row in self.board:
            for t in row:
                self.districts[t.did].append(t)

        self.you = Player("YOU", 7, 3, YOU_COL)
        self.rival = Player("RIVAL", 0, 4, RIVAL_COL)

        self.state = "ROLL"   # ROLL, MOVE, ACTION, EVENT, RIVAL, GAMEOVER
        self.round = 1
        self.dice = 0
        self.moves_left = 0
        self.dice_timer = 0
        self.want_land = False
        self.event = None
        self.result = None

        self.r_stage = None
        self.r_timer = 0

        self.log = []
        self.floaters = []
        self.particles = []
        self.shake = 0
        self.hover = None
        self.tick = 0

        self.say("Empire war begins. Roll the dice!")

    # ---------------- helpers ----------------
    def say(self, msg):
        self.log.append(msg)
        if len(self.log) > 8:
            self.log.pop(0)

    def other(self, p):
        return self.rival if p is self.you else self.you

    def tile_at(self, r, c):
        return self.board[r][c]

    def owned(self, p):
        return [t for row in self.board for t in row if t.owner is p]

    def has_monopoly(self, p, did):
        return all(t.owner is p for t in self.districts[did])

    def empire(self, p):
        return sum(1 for d in self.districts if self.has_monopoly(p, d))

    def rent_of(self, t):
        base = t.price * 0.25 * RENT_MULT[t.level - 1]
        if t.owner and self.has_monopoly(t.owner, t.did):
            base *= 2
        return int(base)

    def upgrade_cost(self, t):
        return t.price // 2

    def takeover_cost(self, t):
        return t.price * 2 + (t.level - 1) * t.price

    def net_worth(self, p):
        return p.cash + sum(t.price + (t.level - 1) * t.price // 2 for t in self.owned(p))

    def float_text(self, text, x, y, color):
        self.floaters.append([text, x, y, color, 75])

    def burst(self, x, y, color, n=18):
        for _ in range(n):
            a = random.random() * math.tau
            sp = random.uniform(1.5, 5)
            self.particles.append([x, y, math.cos(a) * sp, math.sin(a) * sp, random.randint(25, 50), color])

    # ---------------- money / heat ----------------
    def settle(self, p):
        """Auto-liquidate cheapest tiles to cover debts."""
        while p.cash < 0:
            mine = self.owned(p)
            if not mine:
                break
            t = min(mine, key=lambda x: x.price * x.level)
            p.cash += t.price // 2
            t.owner = None
            t.level = 1
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
            self.settle(p)

    # ---------------- actions ----------------
    def act_buy(self, p):
        t = self.tile_at(p.r, p.c)
        x, y = cell_center(t.r, t.c)
        if t.owner is None:
            if p.cash < t.price:
                self.say("Not enough cash to buy.")
                return False
            p.cash -= t.price
            t.owner = p
            self.add_heat(p, 4)
            self.say(f"{p.name} bought {t.name}")
            self.float_text("-" + fmt(t.price), x, y, GOLD)
            self.burst(x, y, p.color, 14)
        elif t.owner is p:
            cost = self.upgrade_cost(t)
            if t.level >= MAX_LEVEL:
                self.say("Already max level.")
                return False
            if p.cash < cost:
                self.say("Not enough cash to upgrade.")
                return False
            p.cash -= cost
            t.level += 1
            self.add_heat(p, 6)
            self.say(f"{p.name} upgraded {t.name} to L{t.level}")
            self.float_text(f"LEVEL {t.level}", x, y, WHITE)
            self.burst(x, y, WHITE, 10)
        else:
            self.say("Rival owns this. Use takeover (T).")
            return False
        self.after_acquire(p, t)
        return True

    def act_takeover(self, p):
        t = self.tile_at(p.r, p.c)
        o = self.other(p)
        x, y = cell_center(t.r, t.c)
        if t.owner is not o:
            self.say("Takeover needs a rival tile.")
            return False
        cost = self.takeover_cost(t)
        if p.cash < cost:
            self.say(f"Takeover needs {fmt(cost)}.")
            return False
        p.cash -= cost
        o.cash += cost // 2
        t.owner = p
        t.level = max(1, t.level - 1)
        self.add_heat(p, 25)
        self.say(f"TAKEOVER! {p.name} seized {t.name}")
        self.float_text("TAKEOVER!", x, y, RED)
        self.shake = 14
        self.burst(x, y, p.color, 28)
        self.after_acquire(p, t)
        return True

    def after_acquire(self, p, t):
        if self.has_monopoly(p, t.did):
            d = DISTRICTS[t.did]["name"]
            self.say(f"{p.name} MONOPOLY: {d}!")
            cx = BX + (t.c // 2) * 2 * TILE + TILE
            cy = BY + (t.r // 2) * 2 * TILE + TILE
            self.float_text("MONOPOLY!", cx, cy, GOLD)
            self.burst(cx, cy, GOLD, 40)
            self.shake = max(self.shake, 10)

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
        sign = "+" if cash >= 0 else ""
        if cash:
            self.float_text(f"{sign}{fmt(cash)}", p.px, p.py - 28, GREEN if cash > 0 else RED)
        return {"title": title, "desc": desc, "cash": cash, "heat": heat, "who": p.name}

    def land(self, p):
        t = self.tile_at(p.r, p.c)
        o = self.other(p)
        if t.owner is o:
            rent = self.rent_of(t)
            p.cash -= rent
            o.cash += rent
            self.say(f"{p.name} paid {fmt(rent)} rent")
            self.float_text("-" + fmt(rent), p.px, p.py - 26, RED)
            self.float_text("+" + fmt(rent), o.px, o.py - 26, GREEN)
            self.settle(p)
        if random.random() < 0.25:
            return self.apply_event(p, random.choice(CARDS))
        return None

    # ---------------- turn flow ----------------
    def roll_click(self):
        if self.state == "ROLL" and self.dice_timer == 0:
            self.dice_timer = 25
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
            if income:
                p.cash += income
                if p is self.you:
                    self.say(f"Dividends: +{fmt(income)}")
                    self.float_text("+" + fmt(income), p.px, p.py - 40, GREEN)
            p.heat = max(0, p.heat - 8)
        self.round += 1
        self.state = "ROLL"
        self.r_stage = None
        self.dice = 0
        if self.round <= MAX_ROUNDS:
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

    def handle_key(self, key):
        if self.state == "EVENT":
            if key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_ESCAPE):
                self.event = None
                self.state = "ACTION"
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
        return {
            "dice": pygame.Rect(PX, 394, 56, 56),
            "roll": pygame.Rect(PX + 64, 394, PW - 64, 56),
            "buy": pygame.Rect(PX, 458, 118, 34),
            "take": pygame.Rect(PX + 122, 458, PW - 122, 34),
            "end": pygame.Rect(PX, 498, PW, 34),
        }

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
            rcell = self.tile_from_pos(pos)
            if rcell:
                dist = abs(rcell[0] - self.you.r) + abs(rcell[1] - self.you.c)
                if dist == 0:
                    self.want_land = True
                elif dist <= self.moves_left:
                    self.you.path.extend(make_path((self.you.r, self.you.c), rcell))
                    self.moves_left -= dist

    # ---------------- rival AI ----------------
    def ai_score(self, t, dist):
        rv, pl = self.rival, self.you
        s = random.random() * 25_000
        if t.owner is None:
            s += t.price * 0.8
            mine = sum(1 for x in self.districts[t.did] if x.owner is rv)
            theirs = sum(1 for x in self.districts[t.did] if x.owner is pl)
            s += mine * t.price * 0.55
            if theirs:
                s += t.price * 0.3
            if rv.cash < t.price + 150_000:
                s -= 10_000_000
        elif t.owner is rv:
            cost = self.upgrade_cost(t)
            if t.level < MAX_LEVEL and rv.cash >= cost + 300_000:
                s += t.price * 0.35
            else:
                s -= t.price * 0.1
        else:
            cost = self.takeover_cost(t)
            if rv.cash >= cost + 400_000 and rv.heat < 70:
                s += t.price * 1.0
            else:
                s -= self.rent_of(t) * 1.6
        s -= dist * 1500
        return s

    def ai_pick(self, roll):
        rv = self.rival
        best, best_s = (rv.r, rv.c), -1e18
        for r in range(GRID):
            for c in range(GRID):
                d = abs(r - rv.r) + abs(c - rv.c)
                if d <= roll:
                    sc = self.ai_score(self.tile_at(r, c), d)
                    if sc > best_s:
                        best, best_s = (r, c), sc
        return best

    def ai_act(self):
        rv = self.rival
        t = self.tile_at(rv.r, rv.c)
        if t.owner is None:
            if rv.cash >= t.price + 250_000:
                self.act_buy(rv)
        elif t.owner is rv:
            if t.level < MAX_LEVEL and rv.cash >= self.upgrade_cost(t) + 400_000:
                self.act_buy(rv)
        else:
            if rv.cash >= self.takeover_cost(t) + 400_000 and rv.heat < 70:
                self.act_takeover(rv)

    def update_rival(self):
        rv = self.rival
        if self.r_stage == "roll":
            self.r_timer -= 1
            if self.r_timer <= 0:
                roll = random.randint(1, 6)
                self.dice = roll
                self.say(f"Rival rolled a {roll}")
                tgt = self.ai_pick(roll)
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
                self.ai_act()
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
            if self.empire(p) >= EMPIRE_GOAL:
                if p is self.you:
                    self.finish("VICTORY", f"You control {EMPIRE_GOAL} districts. Total empire.")
                else:
                    self.finish("DEFEAT", f"Rival controls {EMPIRE_GOAL} districts.")
                return
        if self.round > MAX_ROUNDS:
            a, b = self.net_worth(self.you), self.net_worth(self.rival)
            if a >= b:
                self.finish("VICTORY", f"Richest at round {MAX_ROUNDS}: {fmt(a)} vs {fmt(b)}")
            else:
                self.finish("DEFEAT", f"Rival is richer: {fmt(b)} vs {fmt(a)}")

    def finish(self, title, sub):
        self.result = (title, sub)
        self.state = "GAMEOVER"

    # ---------------- update ----------------
    def update(self):
        self.tick += 1
        self.you.update()
        self.rival.update()

        if self.state == "ROLL" and self.dice_timer > 0:
            self.dice_timer -= 1
            if self.dice_timer == 0:
                self.dice = random.randint(1, 6)
                self.moves_left = self.dice
                self.want_land = False
                self.state = "MOVE"
                self.say(f"You rolled a {self.dice}. Move or stop.")

        if self.state == "MOVE" and self.you.idle and (self.moves_left == 0 or self.want_land):
            self.moves_left = 0
            self.want_land = False
            ev = self.land(self.you)
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

        self.check_end()

    # ---------------- drawing ----------------
    def draw(self, s):
        s.fill(BG)
        self.draw_board(s)
        self.draw_tokens(s)
        self.draw_fx(s)
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
                if t.owner:
                    box = (x + 6, y + 6, TILE - 12, TILE - 12)
                    pygame.draw.rect(s, t.owner.color, box, border_radius=8)
                    pygame.draw.rect(s, shade(t.owner.color, 0.6), box, 2, border_radius=8)
                    label = F_XS.render("R " + fmt(self.rent_of(t)), True, WHITE)
                    for i in range(t.level):
                        px = x + TILE / 2 + (i - (t.level - 1) / 2) * 11
                        pygame.draw.circle(s, WHITE, (int(px), y + TILE - 15), 3)
                else:
                    label = F_XS.render(fmt(t.price), True, (235, 235, 235))
                s.blit(label, (x + TILE // 2 - label.get_width() // 2, y + TILE // 2 - 12))
                if (r, c) in reach:
                    hi.fill((255, 230, 80, 70))
                    s.blit(hi, (x, y))
                if self.hover == (r, c) and self.state in ("MOVE", "ACTION", "ROLL"):
                    hi.fill((255, 255, 255, 50))
                    s.blit(hi, (x, y))
        # district outlines
        for did in range(16):
            br, bc = divmod(did, 4)
            rect = (BX + bc * 2 * TILE, BY + br * 2 * TILE, 2 * TILE, 2 * TILE)
            owner = self.districts[did][0].owner
            if owner and self.has_monopoly(owner, did):
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

    def bar(self, s, x, y, w, h, frac, col):
        pygame.draw.rect(s, PANEL_DARK, (x, y, w, h), border_radius=3)
        pygame.draw.rect(s, col, (x, y, int(w * max(0, min(1, frac))), h), border_radius=3)

    def draw_card(self, s, p, y):
        pygame.draw.rect(s, PANEL, (PX, y, PW, 90), border_radius=6)
        pygame.draw.rect(s, p.color, (PX, y, 5, 90), border_radius=3)
        s.blit(F_M.render(p.name, True, p.color), (PX + 14, y + 8))
        cash = F_L.render(fmt(p.cash), True, GREEN if p.cash >= 0 else RED)
        s.blit(cash, (PX + PW - cash.get_width() - 10, y + 6))
        info = f"Worth {fmt(self.net_worth(p))}  Tiles {len(self.owned(p))}  Dist {self.empire(p)}/{EMPIRE_GOAL}"
        s.blit(F_XS.render(info, True, MUTED), (PX + 14, y + 36))
        s.blit(F_XS.render("HEAT", True, MUTED), (PX + 14, y + 62))
        hc = (240, 200, 60) if p.heat < 60 else RED
        self.bar(s, PX + 52, y + 63, PW - 66, 10, p.heat / 100, hc)

    def draw_panel(self, s):
        pygame.draw.rect(s, PANEL, (PX, 20, PW, 50), border_radius=6)
        t = F_M.render("HOSTILE TAKEOVER", True, WHITE)
        s.blit(t, (PX + PW // 2 - t.get_width() // 2, 26))
        rd = F_XS.render(f"ROUND {min(self.round, MAX_ROUNDS)}/{MAX_ROUNDS}", True, MUTED)
        s.blit(rd, (PX + PW // 2 - rd.get_width() // 2, 48))

        self.draw_card(s, self.you, 80)
        self.draw_card(s, self.rival, 178)

        # tile info
        tile = None
        if self.hover:
            tile = self.tile_at(*self.hover)
        else:
            tile = self.tile_at(self.you.r, self.you.c)
        pygame.draw.rect(s, PANEL, (PX, 276, PW, 108), border_radius=6)
        pygame.draw.rect(s, DISTRICTS[tile.did]["color"], (PX, 276, 5, 108), border_radius=3)
        s.blit(F_M.render(tile.name, True, WHITE), (PX + 14, 282))
        own = sum(1 for x in self.districts[tile.did] if x.owner is tile.owner) if tile.owner else 0
        if tile.owner:
            lines = [f"Owner: {tile.owner.name}   Level {tile.level}",
                     f"Rent {fmt(self.rent_of(tile))}   Takeover {fmt(self.takeover_cost(tile))}",
                     f"District set: {own}/4" + ("  (x2 MONOPOLY)" if own == 4 else "")]
        else:
            lines = [f"Unowned   Price {fmt(tile.price)}",
                     f"Base rent {fmt(tile.price * 0.25)}",
                     "Own all 4 tiles = double rent+income"]
        for i, ln in enumerate(lines):
            s.blit(F_XS.render(ln, True, MUTED), (PX + 14, 308 + i * 17))

        # dice + buttons
        rc = self.rects()
        rolling = self.dice_timer > 0 or self.r_stage == "roll"
        val = random.randint(1, 6) if rolling else self.dice
        pygame.draw.rect(s, (10, 10, 10), rc["dice"].move(0, 3), border_radius=10)
        pygame.draw.rect(s, (245, 243, 239), rc["dice"], border_radius=10)
        dv = F_XL.render(str(val) if val else "-", True, (30, 29, 27))
        s.blit(dv, (rc["dice"].centerx - dv.get_width() // 2, rc["dice"].centery - dv.get_height() // 2))

        if self.state == "ROLL":
            self.button(s, rc["roll"], "ROLL DICE  [SPACE]", GREEN, self.dice_timer == 0)
        elif self.state == "MOVE":
            self.button(s, rc["roll"], f"STOP HERE  ({self.moves_left} left)", GOLD, True)
        elif self.state == "RIVAL":
            self.button(s, rc["roll"], "RIVAL'S TURN...", EDGE, False)
        else:
            self.button(s, rc["roll"], "CHOOSE AN ACTION", EDGE, False)

        mine = self.tile_at(self.you.r, self.you.c)
        act = self.state == "ACTION"
        if mine.owner is self.you:
            buy_label = f"UPGRADE {fmt(self.upgrade_cost(mine))} [B]"
            can_buy = act and mine.level < MAX_LEVEL
        else:
            buy_label = f"BUY {fmt(mine.price)} [B]"
            can_buy = act and mine.owner is None
        self.button(s, rc["buy"], buy_label, GREEN, can_buy)
        take_label = f"TAKEOVER {fmt(self.takeover_cost(mine))} [T]" if mine.owner is self.rival else "TAKEOVER [T]"
        self.button(s, rc["take"], take_label, RED, act and mine.owner is self.rival)
        self.button(s, rc["end"], "END TURN  [SPACE]", (90, 90, 200), act)

        # log
        pygame.draw.rect(s, PANEL_DARK, (PX, 542, PW, 138), border_radius=6)
        for i, msg in enumerate(self.log[-8:]):
            while F_XS.size(msg)[0] > PW - 18 and len(msg) > 4:
                msg = msg[:-4] + ".."
            col = WHITE if i == len(self.log[-8:]) - 1 else MUTED
            s.blit(F_XS.render(msg, True, col), (PX + 9, 548 + i * 16))

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
        h = F_S.render("UNDERWORLD ENCOUNTER", True, MUTED)
        t = F_L.render(e["title"], True, WHITE)
        d = F_S.render(e["desc"], True, MUTED)
        s.blit(h, (r.centerx - h.get_width() // 2, r.y + 18))
        s.blit(t, (r.centerx - t.get_width() // 2, r.y + 46))
        s.blit(d, (r.centerx - d.get_width() // 2, r.y + 78))
        y = r.y + 108
        if e["cash"]:
            v = F_L.render(("+" if e["cash"] > 0 else "") + fmt(e["cash"]), True, GREEN if e["cash"] > 0 else RED)
            s.blit(v, (r.centerx - v.get_width() // 2, y))
            y += 28
        if e["heat"]:
            v = F_S.render(f"Heat {'+' if e['heat'] > 0 else ''}{e['heat']}", True, RED if e["heat"] > 0 else GREEN)
            s.blit(v, (r.centerx - v.get_width() // 2, y))
        b = pygame.Rect(r.centerx - 80, r.bottom - 38, 160, 28)
        pygame.draw.rect(s, GREEN, b, border_radius=5)
        bt = F_S.render("CONTINUE [ENTER]", True, WHITE)
        s.blit(bt, (b.centerx - bt.get_width() // 2, b.centery - bt.get_height() // 2))

    def draw_gameover(self, s):
        self.draw_overlay(s, 200)
        title, sub = self.result
        r = pygame.Rect(W // 2 - 230, H // 2 - 100, 460, 200)
        pygame.draw.rect(s, PANEL, r, border_radius=8)
        pygame.draw.rect(s, GREEN if title == "VICTORY" else RED, r, 3, border_radius=8)
        t = F_XL.render(title, True, GREEN if title == "VICTORY" else RED)
        u = F_S.render(sub, True, MUTED)
        k = F_S.render("[R] play again     [ESC] quit", True, WHITE)
        s.blit(t, (r.centerx - t.get_width() // 2, r.y + 30))
        s.blit(u, (r.centerx - u.get_width() // 2, r.y + 90))
        s.blit(k, (r.centerx - k.get_width() // 2, r.y + 140))


# --------------------------------------------------------------------------
# Menu
# --------------------------------------------------------------------------
PLAY_BTN = pygame.Rect(W // 2 - 120, 560, 240, 50)


def draw_menu(s):
    s.fill(BG)
    t = F_XL.render("HOSTILE TAKEOVER", True, WHITE)
    s.blit(t, (W // 2 - t.get_width() // 2, 70))
    sub = F_M.render("Syndicate Edition - a mafia territory war", True, MUTED)
    s.blit(sub, (W // 2 - sub.get_width() // 2, 118))
    rules = [
        ("GOAL", f"Own {EMPIRE_GOAL} full districts, bankrupt the rival, or be richest after {MAX_ROUNDS} rounds."),
        ("ROLL", "Roll the dice, then walk up to that many tiles (click a tile or use arrows/WASD)."),
        ("BUY", "Buy tiles, upgrade them to level 4 for higher rent. Every 4 tiles in a block = a district."),
        ("SET", "Own a whole district: double rent and double dividends. Rivals will try to break it."),
        ("TAKEOVER", "Stand on a rival tile and pay to seize it. Half your payment goes to the victim."),
        ("HEAT", "Aggressive moves raise your Heat. At 100 the Feds raid you and take 25% of your cash."),
        ("EVENTS", "Landing spots can trigger underworld cards: windfalls, bribes, rival heists."),
    ]
    y = 175
    for k, v in rules:
        pygame.draw.rect(s, PANEL, (80, y, W - 160, 44), border_radius=6)
        s.blit(F_S.render(k, True, GOLD), (96, y + 14))
        s.blit(F_S.render(v, True, WHITE), (180, y + 14))
        y += 52
    pygame.draw.rect(s, GREEN, PLAY_BTN, border_radius=8)
    pt = F_L.render("PLAY MATCH", True, WHITE)
    s.blit(pt, (PLAY_BTN.centerx - pt.get_width() // 2, PLAY_BTN.centery - pt.get_height() // 2))


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def main():
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Hostile Takeover - Syndicate Edition")
    canvas = pygame.Surface((W, H))
    clock = pygame.time.Clock()
    game = None

    while True:
        clock.tick(FPS)
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if game is None:
                if ev.type == pygame.MOUSEBUTTONDOWN and PLAY_BTN.collidepoint(ev.pos):
                    game = Game()
                elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    game = Game()
                continue
            if ev.type == pygame.KEYDOWN:
                if game.state == "GAMEOVER":
                    if ev.key == pygame.K_r:
                        game = Game()
                    elif ev.key == pygame.K_ESCAPE:
                        pygame.quit()
                        sys.exit()
                else:
                    game.handle_key(ev.key)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if game.state != "GAMEOVER":
                    game.click(ev.pos)
            elif ev.type == pygame.MOUSEMOTION:
                game.hover = game.tile_from_pos(ev.pos)

        if game is None:
            draw_menu(canvas)
            screen.blit(canvas, (0, 0))
        else:
            game.update()
            game.draw(canvas)
            ox = oy = 0
            if game.shake > 0:
                ox = random.randint(-4, 4)
                oy = random.randint(-4, 4)
            screen.fill(BG)
            screen.blit(canvas, (ox, oy))
        pygame.display.flip()


if __name__ == "__main__":
    main()