#!/usr/bin/env python3
"""motion-effects engine: 18 named on-screen effects rendered onto a 9:16 talking head from ONE JSON spec.

Commands (run with a Python that has numpy + Pillow):
  fx_engine.py transcribe <video> <words.json>      word timings via whisper-cli (CPU) -> [{w,s,e}]
  fx_engine.py face <video> <out.png> [t]           gridded frame (100px grid) to find the face before layout
  fx_engine.py stills <spec.json> t1 t2 ...         single frames -> <spec dir>/_qa/stills/ + sheet.jpg
  fx_engine.py render <spec.json> [--clips]         video + synthesized SFX mix + mux (+ one clip per effect)
  fx_engine.py remix <spec.json>                    audio-only change: new mix onto the last rendered picture

The spec format and every effect's parameters are documented in ../references/effects.md.
All times in a spec are seconds on the SOURCE timeline (the source is already cut).
"""
import json, math, os, random, shutil, subprocess, sys
from collections import deque
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
RED = (222, 62, 52); GREEN = (58, 157, 107); BLUE = (79, 134, 198); WHITE = (255, 255, 255)
GREY = (120, 128, 138); CORAL = (214, 110, 90); SLATE = (110, 125, 140)
NAMED = {"red": RED, "green": GREEN, "blue": BLUE, "white": WHITE, "grey": GREY, "coral": CORAL, "slate": SLATE}

# ---------------------------------------------------------------- spec + brand
S = {}          # the loaded spec
B = {}          # resolved brand colours
L = {}          # resolved layout
FPS = 30
DUR = 0.0


def hexrgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def col(name, default="light"):
    """'primary' | 'accent' | 'light' | 'dark' | named | '#hex' -> rgb"""
    name = name or default
    if name in B: return B[name]
    if name in NAMED: return NAMED[name]
    return hexrgb(name)


def first_existing(*paths):
    for p in paths:
        if p and os.path.exists(os.path.expanduser(p)): return os.path.expanduser(p)
    return None


FONT_PATHS = {}


def setup_fonts(spec_dir):
    br = S.get("brand", {})
    bold = br.get("font_bold")
    if bold and not os.path.isabs(os.path.expanduser(bold)): bold = os.path.join(spec_dir, bold)
    sysf = "/System/Library/Fonts"; sup = sysf + "/Supplemental"; dj = "/usr/share/fonts/truetype/dejavu"
    FONT_PATHS.update({
        "pop": (first_existing(bold, sysf + "/Helvetica.ttc", dj + "/DejaVuSans-Bold.ttf"), 1 if not first_existing(bold) and os.path.exists(sysf + "/Helvetica.ttc") else 0),
        "helv": (first_existing(sysf + "/Helvetica.ttc", dj + "/DejaVuSans.ttf"), 0),
        "helvb": (first_existing(sysf + "/Helvetica.ttc", dj + "/DejaVuSans-Bold.ttf"), 1 if os.path.exists(sysf + "/Helvetica.ttc") else 0),
        "mono": (first_existing(sysf + "/Menlo.ttc", dj + "/DejaVuSansMono.ttf"), 0),
        "monob": (first_existing(sysf + "/Menlo.ttc", dj + "/DejaVuSansMono-Bold.ttf"), 1 if os.path.exists(sysf + "/Menlo.ttc") else 0),
        "it": (first_existing(br.get("font_serif_italic"), sup + "/Georgia Italic.ttf", dj + "/DejaVuSerif-Italic.ttf"), 0),
        "didot": (first_existing(br.get("font_serif"), sup + "/Didot.ttc", dj + "/DejaVuSerif.ttf"), 0),
    })


@lru_cache(None)
def F(name, size):
    p, i = FONT_PATHS[name]
    return ImageFont.truetype(p, size, index=i)


def load_spec(path):
    global FPS, DUR
    path = os.path.abspath(path); d = os.path.dirname(path)
    S.clear(); S.update(json.load(open(path)))
    S["_dir"] = d
    for k in ("source", "audio", "words", "output"):
        if S.get(k) and not os.path.isabs(os.path.expanduser(S[k])): S[k] = os.path.join(d, S[k])
        elif S.get(k): S[k] = os.path.expanduser(S[k])
    br = S.get("brand", {})
    B.update({"primary": hexrgb(br.get("primary", "#2b4c67")), "accent": hexrgb(br.get("accent", "#e8b446")),
              "light": hexrgb(br.get("light", "#f4efe6")), "dark": hexrgb(br.get("dark", "#181e26"))})
    lay = S.get("layout", {})
    L.update({"caption_cy": lay.get("caption_cy", 1270), "zone_cy": lay.get("zone_cy", 1560),
              "top_y": lay.get("top_y", 70), "checklist_y": lay.get("checklist_y", 300)})
    pr = probe(S["source"])
    if (pr["w"], pr["h"]) != (W, H): sys.exit(f"source must be 1080x1920, got {pr['w']}x{pr['h']} (scale/crop it first)")
    FPS = pr["fps"]; DUR = pr["dur"]
    setup_fonts(d)
    for e in S["effects"]:
        if e.get("end") is None: e["end"] = DUR + 1
    S["effects"].sort(key=lambda e: e["start"])
    load_words()


def probe(p):
    o = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=width,height,r_frame_rate:format=duration", "-of", "json", p],
                                  capture_output=True, check=True).stdout)
    st = o["streams"][0]; n, dd = st["r_frame_rate"].split("/"); fps = int(n) / int(dd)
    if abs(fps - round(fps)) > 0.01: sys.exit(f"source fps {fps:.3f} is not an integer: re-encode at 30fps first")
    return {"w": st["width"], "h": st["height"], "fps": int(round(fps)), "dur": float(o["format"]["duration"])}


# ---------------------------------------------------------------- easing + drawing helpers
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def prog(t, a, d): return clamp((t - a) / d) if d > 0 else float(t >= a)
def eo(x): x = clamp(x); return 1 - (1 - x) ** 3
def eob(x, s=1.70158): x = clamp(x) - 1; return x * x * ((s + 1) * x + s) + 1


def bounce(x):
    x = clamp(x); n, d = 7.5625, 2.75
    if x < 1 / d: return n * x * x
    if x < 2 / d: x -= 1.5 / d; return n * x * x + .75
    if x < 2.5 / d: x -= 2.25 / d; return n * x * x + .9375
    x -= 2.625 / d; return n * x * x + .984375


@lru_cache(4096)
def T(text, font, size, fill, stroke=0, sfill=None):
    f = F(font, size); b = f.getbbox(text, stroke_width=stroke); pad = 3
    im = Image.new("RGBA", (max(1, b[2] - b[0] + 2 * pad), max(1, b[3] - b[1] + 2 * pad)), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((pad - b[0], pad - b[1]), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=sfill)
    return im


@lru_cache(4096)
def TL(text, font, size, fill, stroke=0, sfill=None):
    f = F(font, size); b = f.getbbox(text, stroke_width=stroke, anchor="ls"); pad = 3
    im = Image.new("RGBA", (b[2] - b[0] + 2 * pad, b[3] - b[1] + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((pad - b[0], pad - b[1]), text, font=f, fill=fill, anchor="ls", stroke_width=stroke, stroke_fill=sfill)
    return im, b[0] - pad, b[1] - pad


def fade(im, a):
    if a >= 0.999: return im
    im = im.copy(); im.putalpha(im.getchannel("A").point([int(v * a) for v in range(256)])); return im


def blit(dst, im, x, y):
    x, y = int(round(x)), int(round(y))
    x0, y0 = max(x, 0), max(y, 0); x1, y1 = min(x + im.width, dst.width), min(y + im.height, dst.height)
    if x1 <= x0 or y1 <= y0: return
    dst.alpha_composite(im, (x0, y0), (x0 - x, y0 - y, x1 - x, y1 - y))


def put(dst, im, x, y, alpha=1.0):
    if alpha > 0.01: blit(dst, fade(im, alpha), x, y)


def place(dst, im, cx, cy, scale=1.0, alpha=1.0, rot=0.0):
    if alpha <= 0.01 or scale <= 0.02: return
    if abs(scale - 1) > 1e-3:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.BICUBIC)
    if abs(rot) > 0.05: im = im.rotate(rot, Image.BICUBIC, expand=True)
    blit(dst, fade(im, alpha), cx - im.width / 2, cy - im.height / 2)


@lru_cache(256)
def card(w, h, r, fill, shadow=24, outline=None, ow=0):
    m = shadow
    im = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("L", im.size, 0)
        ImageDraw.Draw(sh).rounded_rectangle((m, m + 10, m + w, m + h + 10), r, fill=110)
        im.putalpha(sh.filter(ImageFilter.GaussianBlur(shadow / 2)))
    ImageDraw.Draw(im).rounded_rectangle((m, m, m + w, m + h), r, fill=fill, outline=outline, width=ow)
    return im


@lru_cache(256)
def text_card(text, font, size, fg, bg, padx=30, pady=18, r=22, shadow=18):
    t = T(text, font, size, fg)
    c = card(t.width + 2 * padx, t.height + 2 * pady, r, bg, shadow).copy()
    c.alpha_composite(t, (shadow + padx, shadow + pady))
    return c


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255); return m


def heart(d, cx, cy, s, fill):
    d.ellipse((cx - s, cy - s * 0.8, cx, cy + s * 0.2), fill=fill)
    d.ellipse((cx, cy - s * 0.8, cx + s, cy + s * 0.2), fill=fill)
    d.polygon([(cx - s * 0.97, cy - 0.1 * s), (cx + s * 0.97, cy - 0.1 * s), (cx, cy + s)], fill=fill)


def grab(ts):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(ts), "-i", S["source"], "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return Image.frombytes("RGB", (W, H), raw)


def active(t, typ):
    for e in S["effects"]:
        if e["type"] == typ and e["start"] <= t < e["end"]: return e
    return None


def exit_p(t, e, d=0.2): return prog(t, e["end"] - d, d)


# ---------------------------------------------------------------- captions
WORDS = []; GROUPS = []


def load_words():
    WORDS.clear(); GROUPS.clear()
    if not S.get("words"): return
    WORDS.extend(json.load(open(S["words"])))
    cur = []
    for w in WORDS:
        if cur and (len(cur) >= 3 or w["s"] - cur[-1]["e"] > 0.35 or cur[-1]["w"][-1] in ".?,!"
                    or (w["w"][:1].isupper() and w["w"] not in ("I", "I'm", "I'll", "I've"))):
            GROUPS.append(cur); cur = []
        cur.append(w)
    if cur: GROUPS.append(cur)


def clean(w):
    return S.get("captions", {}).get("fix", {}).get(w, w).rstrip(".,?!")


@lru_cache(512)
def cap_img(gi, hi):
    ws = [clean(w["w"]) for w in GROUPS[gi]]
    f = F("pop", 56); sp = f.getlength(" ")
    widths = [f.getlength(x) for x in ws]; tw = sum(widths) + sp * (len(ws) - 1)
    asc, desc = f.getmetrics(); padx, pady, m = 28, 10, 16
    c = card(int(tw + 2 * padx), asc + desc + 2 * pady - 14, 20, (*B["primary"], 242), m).copy()
    d = ImageDraw.Draw(c); x = m + padx
    for k, (wd, wi) in enumerate(zip(ws, widths)):
        d.text((x, m + pady - 4), wd, font=f, fill=B["accent"] if k == hi else WHITE); x += wi + sp
    return c


CAPTION_OWNERS = {"kinetic_hook", "gravity_drop", "giant_captions"}


def captions(img, t):
    cfg = S.get("captions", {})
    if not cfg.get("enabled", True) or not GROUPS: return
    if any(a <= t < b for a, b in cfg.get("off", [])): return
    for e in S["effects"]:
        if e["start"] <= t < e["end"]:
            if e["type"] in CAPTION_OWNERS: return
            if e["type"] == "title_card" and t < e.get("phone_at", e["start"] + 0.5 * (e["end"] - e["start"])): return
    for gi, g in enumerate(GROUPS):
        end = GROUPS[gi + 1][0]["s"] if gi + 1 < len(GROUPS) else DUR
        end = min(end, g[-1]["e"] + 0.6)
        if g[0]["s"] <= t < end:
            hi = max(k for k, w in enumerate(g) if w["s"] <= t + 0.02)
            place(img, cap_img(gi, hi), W / 2, L["caption_cy"]); return


# ---------------------------------------------------------------- checklist sidebar
LABELS = {"kinetic_hook": "Kinetic hook", "gravity_drop": "Gravity drop", "giant_captions": "Giant captions",
          "prompt_bar": "Prompt bar", "file_drop": "File drop", "storyboard": "Storyboard deck",
          "jiggle_delete": "Jiggle delete", "icon_strike": "Icon strike", "title_card": "Title card",
          "nle_timeline": "NLE timeline", "playing_grid": "Playing grid", "film_look": "Film look",
          "reel_feed": "Reel feed", "eyedropper_grade": "Eyedropper grade", "grade_slider": "Grade slider",
          "video_card": "Video card", "comment_cta": "Comment CTA", "comment_drop": "Comment drop"}


@lru_cache(None)
def sidebar_img(idx):
    cl = S["checklist"]; items = [e.get("label", LABELS[e["type"]]) for e in S["effects"]]; n = len(items)
    w, rh = 246, 25; h = 46 + n * rh + (40 if cl.get("footer") else 14); m = 18
    im = card(w, h, 18, (*B["primary"], 236), m).copy(); d = ImageDraw.Draw(im)
    d.text((m + 14, m + 14), cl.get("title", "EFFECTS IN THIS VIDEO"), font=F("monob", 13), fill=(*B["light"], 210))
    d.text((m + w - 14, m + 14), f"{idx + 1:02d}/{n:02d}", font=F("monob", 13), fill=B["accent"], anchor="ra")
    y = m + 44
    for i, name in enumerate(items):
        if i == idx:
            d.rounded_rectangle((m + 8, y - 3, m + w - 8, y + rh - 4), 7, fill=B["light"])
            d.text((m + 18, y), "› " + name, font=F("helvb", 17), fill=B["primary"])
        else:
            d.text((m + 18, y), ("• " if i < idx else "  ") + name, font=F("helv", 17),
                   fill=B["accent"] if i < idx else (*B["light"], 140))
        y += rh
    if cl.get("footer"): d.text((m + 14, y + 10), cl["footer"], font=F("helvb", 15), fill=B["accent"])
    return im


def sidebar(img, t):
    cl = S.get("checklist")
    if not cl or not cl.get("enabled", True): return
    idx = 0
    for i, e in enumerate(S["effects"]):
        if e["start"] <= t: idx = i
    e = S["effects"][idx]
    if e["type"] == "title_card" and t >= e.get("phone_at", 1e9): return
    if e["type"] == "reel_feed" and e["start"] + 0.25 <= t < e.get("back_at", e["end"]) + 0.3: return
    sb = sidebar_img(idx); p = eo(prog(t, S["effects"][0]["start"] + 0.1, 0.4))
    blit(img, sb, W - sb.width + 8 + 260 * (1 - p), L["checklist_y"])


# ================================================================ THE 18 EFFECTS
# Each overlay effect: fn(img, t, e). Each base-stage effect: fn(base_rgba, t, e) -> rgba.

def fx_kinetic_hook(img, t, e):
    out = exit_p(t, e, 0.25); a_out = 1 - out; dx = -160 * eo(out); y0 = L["zone_cy"] - 160
    for k, s in enumerate(e.get("intro", [])):
        p = prog(t, e["start"] + 0.05 + 0.15 * k, 0.3)
        put(img, T(s, "it", 58, (*B["dark"], 255)), 44 + dx, L["top_y"] + 66 * k + 20 * (1 - eo(p)), eo(p) * a_out)
    y = y0
    for ln in e["lines"]:
        sz = ln.get("size", 140); c = col(ln.get("color", "white"))
        p = prog(t, ln["at"], 0.2); im = T(ln["text"], "pop", sz, (*c, 255), 7, (*B["dark"], 255))
        cy = y + im.height / 2; y += im.height - 8
        if p <= 0: continue
        place(img, im, 60 + im.width / 2 + dx + 7 * (1 - p) * math.sin(t * 95), cy, 1 + 0.55 * (1 - eo(p)), min(1, p * 3) * a_out)


def _drop_text(img, t, text, size, c, x0, base, start, stagger, dur, exit_t):
    f = F("didot", size)
    for k, ch in enumerate(text):
        if ch == " ": continue
        p = prog(t, start + k * stagger, dur)
        if p <= 0: continue
        im, ox, oy = TL(ch, "didot", size, (*c, 255), 3, (*B["dark"], 255))
        ex = max(0.0, t - exit_t - k * 0.02)
        put(img, im, x0 + f.getlength(text[:k]) + ox, base + oy - 620 * (1 - bounce(p)) + 2600 * ex * ex,
            min(1, p * 6) * (1 - prog(t, exit_t + 0.1, 0.2)))


def fx_gravity_drop(img, t, e):
    gs = e["groups"]
    for i, g in enumerate(gs):
        ex = g.get("exit", (gs[i + 1]["at"] - 0.45) if i + 1 < len(gs) else e["end"] - 0.25)
        _drop_text(img, t, g["text"], g.get("size", 150), col(g.get("color", "white")), 60,
                   L["zone_cy"] + 50, g["at"], g.get("stagger", 0.045), g.get("dur", 0.5), ex)
    if e.get("lead"):
        ld = e["lead"]; p = prog(t, ld["at"], 0.25)
        put(img, T(ld["text"], "it", 70, (*B["accent"], 255), 2, (*B["dark"], 255)), 70,
            L["zone_cy"] - 240 - 20 * (1 - eo(p)), eo(p) * (1 - exit_p(t, e, 0.25)))


def fx_giant_captions(img, t, e):
    items = [it for it in e["items"] if it["at"] <= t] or e["items"][:1]
    cur = items[-1]; p = prog(t, cur["at"], 0.18); out = exit_p(t, e)
    plate = text_card(cur["word"], "pop", cur.get("size", 170), (*B["light"], 255), (*B["primary"], 250), 36, 24, 18, 22)
    place(img, plate, W / 2, L["zone_cy"] - 90, (0.84 + 0.16 * eob(p)) * (1 - 0.1 * out), (1 - out) * (p > 0 or t >= cur["at"]))
    if cur.get("sub"):
        p2 = prog(t, cur["at"] + 0.08, 0.25)
        place(img, T(cur["sub"], "it", 88, (*B["accent"], 255), 3, (*B["dark"], 255)), W / 2,
              L["zone_cy"] + 80 + 24 * (1 - eo(p2)), 1, eo(p2) * (1 - out))


def fx_prompt_bar(img, t, e):
    st = e["start"]; txt_full = e["text"]
    type_at = e.get("type_at", st + 0.1); type_dur = e.get("type_dur", min(1.6, 0.04 * len(txt_full)))
    send_at = e.get("send_at", type_at + type_dur + 0.15); ans_at = e.get("answer_at", send_at + 0.2)
    pin = eo(prog(t, st, 0.3)); out = exit_p(t, e); a = pin * (1 - out)
    m = 24; bw, bh = 960, 136
    bar = card(bw, bh, 68, (*B["light"], 255), m).copy(); d = ImageDraw.Draw(bar)
    d.text((m + 48, m + 22), e.get("label", "ASK"), font=F("monob", 18), fill=GREY)
    n = int(len(txt_full) * prog(t, type_at, type_dur)); txt = txt_full[:n]; f = F("helv", 44)
    d.text((m + 48, m + 52), txt, font=f, fill=B["dark"])
    if n < len(txt_full) or (t * 2.2) % 1 < 0.55:
        x = m + 48 + f.getlength(txt) + 4; d.rectangle((x, m + 54, x + 3, m + 104), fill=B["primary"])
    press = prog(t, send_at, 0.16); r = 44 * (1 - 0.18 * math.sin(math.pi * press)); cx, cy = m + bw - 74, m + bh / 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=B["accent"] if t >= send_at else B["primary"])
    d.polygon([(cx, cy - 22), (cx + 18, cy - 2), (cx + 7, cy - 2), (cx + 7, cy + 22), (cx - 7, cy + 22), (cx - 7, cy - 2), (cx - 18, cy - 2)], fill=WHITE)
    place(img, bar, W / 2, L["zone_cy"] - 130 + 70 * (1 - pin), 1, a)
    if e.get("answer"):
        pa = prog(t, ans_at, 0.25)
        if pa > 0:
            ans = text_card(e["answer"], "helvb", 46, (*WHITE, 255), (*B["primary"], 250), 34, 22, 30, 18)
            place(img, ans, 60 + ans.width / 2, L["zone_cy"] + 40, 0.8 + 0.2 * eob(pa), min(1, pa * 3) * (1 - out))


@lru_cache(8)
def _zone_img(hot, msg, arrow, primary, accent, light):
    w, h, m = 660, 420, 24
    im = card(w, h, 30, (*primary, 228), m).copy(); d = ImageDraw.Draw(im)
    c = accent if hot else (*light, 170); i = 18
    x0, y0, x1, y1 = m + i, m + i, m + w - i, m + h - i
    for x in range(x0 + 20, x1 - 20, 34):
        d.line((x, y0, x + 18, y0), fill=c, width=4); d.line((x, y1, x + 18, y1), fill=c, width=4)
    for y in range(y0 + 20, y1 - 20, 34):
        d.line((x0, y, x0, y + 18), fill=c, width=4); d.line((x1, y, x1, y + 18), fill=c, width=4)
    d.text((m + w / 2, m + 74), msg, font=F("helvb", 38), fill=light, anchor="mm")
    if arrow:
        cx, cy = m + w / 2, m + 150
        d.polygon([(cx - 26, cy - 10), (cx + 26, cy - 10), (cx, cy + 22)], fill=c); d.rectangle((cx - 8, cy - 40, cx + 8, cy - 10), fill=c)
    return im


@lru_cache(64)
def _chip_img(q, name, sub, sub_after, tag):
    frac = q / 40; w, h, m = 560, 136, 18
    im = card(w, h, 22, (*WHITE, 255), m).copy(); d = ImageDraw.Draw(im)
    d.rounded_rectangle((m + 22, m + 24, m + 108, m + 110), 14, fill=B["accent"])
    d.text((m + 65, m + 67), tag, font=F("monob", 24), fill=B["dark"], anchor="mm")
    d.text((m + 128, m + 22), name, font=F("helvb", 30), fill=B["dark"])
    d.text((m + 128, m + 62), sub if frac > 0.99 else sub_after, font=F("helv", 24), fill=GREY)
    bx, by, bwid = m + 128, m + 102, 400
    d.rounded_rectangle((bx, by, bx + bwid, by + 12), 6, fill=(225, 228, 232))
    c = tuple(int(GREEN[k] * frac + RED[k] * (1 - frac)) for k in range(3))
    d.rounded_rectangle((bx, by, bx + max(12, int(bwid * frac)), by + 12), 6, fill=c)
    return im


def fx_file_drop(img, t, e):
    st = e["start"]; drop_at = e.get("drop_at", st + 0.75); after_at = e.get("after_at", drop_at + 1.0)
    drain_at = e.get("drain_at", after_at + 0.1)
    pin = prog(t, st, 0.3); out = exit_p(t, e); a = min(1, pin * 2) * (1 - out)
    msg = e.get("zone_text_after", e["zone_text"]) if t >= after_at else e["zone_text"]
    place(img, _zone_img(t >= drop_at + 0.3, msg, t < after_at, B["primary"], B["accent"], B["light"]), W / 2, L["zone_cy"], 0.9 + 0.1 * eob(pin), a)
    pc = prog(t, drop_at, 0.45)
    if pc > 0:
        y = L["zone_cy"] - 310 + 380 * eob(pc)
        frac = 1 - e.get("drain_to", 0.78) * eo(prog(t, drain_at, 1.2)) if e.get("drain", True) else 1.0
        chip = _chip_img(int(round(frac * 40)), e["filename"], e.get("sub", ""), e.get("sub_after", e.get("sub", "")), e.get("tag", "CSV"))
        place(img, chip, W / 2, y, 1, a * min(1, pc * 4))


THUMBS = {}


@lru_cache(16)
def _sb_card(label, ts, k):
    fr = THUMBS.setdefault(ts, grab(ts))
    th = fr.resize((196, 348), Image.BICUBIC).crop((0, 40, 196, 300))
    w, h, m = 216, 330, 16
    im = card(w, h, 12, (*B["light"], 255), m).copy(); im.paste(th, (m + 10, m + 10)); d = ImageDraw.Draw(im)
    d.text((m + w / 2, m + 296), label, font=F("pop", 30 if len(label) < 10 else 24), fill=B["primary"], anchor="mm")
    d.text((m + 16, m + 18), f"{k + 1:02d}", font=F("monob", 18), fill=WHITE)
    return im


def fx_storyboard(img, t, e):
    out = exit_p(t, e); cards = e["cards"]; n = len(cards); gap = min(260, 960 / n)
    if e.get("header"):
        place(img, T(e["header"], "monob", 26, (*B["light"], 255)), W / 2, L["zone_cy"] - 190, 1, eo(prog(t, e["start"], 0.3)) * (1 - out))
    rots = [-6, 3, -3, 5, -4, 2]
    for k, c in enumerate(cards):
        p = prog(t, c["at"], 0.35)
        if p <= 0: continue
        rot = c.get("rot", rots[k % 6])
        place(img, _sb_card(c["label"], c.get("frame_t", c["at"]), k), W / 2 + (k - (n - 1) / 2) * gap,
              L["zone_cy"] + 40 + 320 * (1 - eob(p)) + 40 * eo(out), 1, min(1, p * 3) * (1 - out), rot * (1 + 2 * (1 - eo(p))))


@lru_cache(16)
def _jg_icon(label, glyph, c):
    im = Image.new("RGBA", (240, 270), (0, 0, 0, 0)); im.alpha_composite(card(170, 170, 42, (*c, 255), 16), (19, 4))
    d = ImageDraw.Draw(im)
    d.text((120, 104), glyph, font=F("pop", 96 if len(glyph) == 1 else 60), fill=WHITE, anchor="mm")
    d.text((120, 236), label, font=F("helvb", 24), fill=B["light"], anchor="mm")
    d.ellipse((22, 6, 64, 48), fill=RED); d.rectangle((33, 25, 53, 29), fill=WHITE)
    return im


def fx_jiggle_delete(img, t, e):
    icons = e["icons"]; n = len(icons); gap = min(260, 960 / n)
    pin = prog(t, e["start"], 0.25); d0 = e.get("delete_at", e["start"] + 1.0); dg = e.get("delete_gap", 0.2)
    defaults = ["accent", "blue", "green", "coral", "primary", "red"]
    for k, ic in enumerate(icons):
        pd = prog(t, d0 + dg * k, 0.16)
        if pd >= 1: continue
        sc = (0.7 + 0.3 * eob(pin)) * (1 + 0.25 * math.sin(math.pi * pd) - eo(pd))
        place(img, _jg_icon(ic["label"], ic.get("glyph", ic["label"][:1]), col(ic.get("color", defaults[k % 6]))),
              W / 2 + (k - (n - 1) / 2) * gap, L["zone_cy"] - 20, max(0.03, sc), min(1, pin * 3) * (1 - pd),
              3.5 * math.sin(2 * math.pi * 6 * t + k * 1.7))


@lru_cache(8)
def _strike_icon(label, up, c):
    im = Image.new("RGBA", (300, 340), (0, 0, 0, 0)); im.alpha_composite(card(240, 240, 56, (*c, 255), 18), (12, 0))
    d = ImageDraw.Draw(im); cx, cy = 150, 138; s = 1 if up else -1
    d.polygon([(cx, cy - 70 * s), (cx + 56, cy - 6 * s), (cx + 20, cy - 6 * s), (cx + 20, cy + 64 * s), (cx - 20, cy + 64 * s),
               (cx - 20, cy - 6 * s), (cx - 56, cy - 6 * s)], fill=WHITE)
    d.text((150, 310), label, font=F("pop", 32 if len(label) < 12 else 26), fill=B["light"], anchor="mm")
    return im


def fx_icon_strike(img, t, e):
    out = exit_p(t, e); a = 1 - out; icons = e["icons"]; n = len(icons); gap = 480 if n == 2 else min(320, 960 / n)
    for k, ic in enumerate(icons):
        cx = W / 2 + (k - (n - 1) / 2) * gap; cy = L["zone_cy"]
        p = prog(t, ic["at"], 0.3)
        place(img, _strike_icon(ic["label"], ic.get("dir", "up") == "up", col(ic.get("color", "green" if ic.get("dir", "up") == "up" else "slate"))),
              cx, cy, 0.6 + 0.4 * eob(p), min(1, p * 3) * a)
        if ic.get("strike_at") is not None:
            ps = eo(prog(t, ic["strike_at"], 0.2))
            if ps > 0 and a > 0.05:
                x0, y0, x1, y1 = cx - 120, cy - 120, cx + 120, cy + 90
                lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ImageDraw.Draw(lay).line((x0, y0, x0 + (x1 - x0) * ps, y0 + (y1 - y0) * ps), fill=(*RED, int(255 * a)), width=20)
                img.alpha_composite(lay)
    if e.get("word"):
        w = e["word"]; pf = prog(t, w["at"], 0.3)
        place(img, T(w["text"], "it", 110, (*B["dark"], 255)), W / 2 - 120, L["top_y"] + 150 + 20 * (1 - eo(pf)), 1, eo(pf) * a)


def fx_title_card(base, t, e):
    st = e["start"]; en = e["end"]; phone_at = e.get("phone_at", st + 0.53 * (en - st)); grow_at = e.get("expand_at", en - 0.4)
    can = Image.new("RGBA", (W, H), (*B["primary"], 255)); d = ImageDraw.Draw(can)
    if t < phone_at: s = None
    elif t < grow_at: s = 0.48 * (0.75 + 0.25 * eob(prog(t, phone_at, 0.4)))
    else: s = 0.48 + 0.52 * eo(prog(t, grow_at, en - grow_at - 0.02))
    q = 0.0 if s is None else clamp((s - 0.48) / 0.52); ta = 1 - q
    tc = f"00:00:{int(t):02d}:{int(t * FPS) % FPS:02d}"
    lines = e["lines"]
    if s is None:
        d.text((W / 2, 640), tc, font=F("monob", 30), fill=B["accent"], anchor="mm")
        f = F("didot", e.get("size", 160)); y = 860
        for k, word in enumerate(lines):
            n = int(len(word) * prog(t, st + 0.15 + 0.35 * k, 0.35)); d.text((W / 2 - f.getlength(word) / 2, y), word[:n], font=f, fill=B["light"], anchor="lm"); y += 170
        rl = 380 * eo(prog(t, st + 0.9, 0.4)); ry = y - 60; d.line((W / 2 - rl / 2, ry, W / 2 + rl / 2, ry), fill=B["light"], width=3)
        if e.get("sub"):
            pa = prog(t, e["sub"]["at"], 0.3)
            if pa > 0:
                im = T(e["sub"]["text"], "it", 64, (*B["accent"], 255)); put(can, im, W / 2 - im.width / 2, ry + 60 + 20 * (1 - eo(pa)), eo(pa))
    else:
        a_small = ta * eo(prog(t, phone_at, 0.3))
        put(can, T(tc, "monob", 28, (*B["accent"], 255)), W / 2 - 110, 300, a_small)
        tt = T(e.get("small_title", " · ".join(lines)), "didot", 56, (*B["light"], 255)); put(can, tt, W / 2 - tt.width / 2, 380, a_small)
        pw, ph = max(2, int(W * s)), max(2, int(H * s)); r = int(64 * (1 - q) + 2); cy = 1010 * (1 - q) + 960 * q
        ph_img = base.resize((pw, ph), Image.BILINEAR)
        if q < 0.99:
            pd = ImageDraw.Draw(ph_img); by = ph - 60
            pd.rounded_rectangle((40, by, pw - 40, by + 8), 4, fill=(255, 255, 255, int(110 * ta)))
            pd.rounded_rectangle((40, by, 40 + (pw - 80) * t / DUR, by + 8), 4, fill=(*B["accent"], int(255 * ta)))
            pd.text((40, by - 34), f"0:{int(t):02d} / 0:{int(DUR):02d}", font=F("monob", 22), fill=(255, 255, 255, int(230 * ta)))
        bz = int(14 * (1 - q)); x, y = int(W / 2 - pw / 2), int(cy - ph / 2)
        if bz > 0: ImageDraw.Draw(can).rounded_rectangle((x - bz, y - bz, x + pw + bz, y + ph + bz), r + bz, fill=B["light"])
        can.paste(ph_img, (x, y), rounded_mask(pw, ph, r))
    pin = eo(prog(t, st, 0.22))
    if pin < 1:
        ytop = int(H * (1 - pin)); res = base.copy(); res.paste(can.crop((0, ytop, W, H)), (0, ytop)); return res
    return can


_rng = random.Random(7)
WAVE = [[_rng.uniform(0.2, 1.0) for _ in range(120)] for _ in range(6)]


def fx_nle_timeline(img, t, e):
    tracks = e["tracks"]; n = len(tracks); w, m = 1020, 20; h = 120 + n * 86; ax = m + 190
    pin = prog(t, e["start"], 0.25); out = exit_p(t, e, 0.18)
    im = card(w, h, 22, (18, 22, 27, 242), m).copy(); d = ImageDraw.Draw(im)
    d.text((m + 24, m + 18), e.get("title", "Timeline"), font=F("helvb", 28), fill=B["light"])
    span = e["end"] - e["start"]
    for k in range(0, 801, 40):
        d.line((ax + k, m + 66, ax + k, m + 66 + (14 if k % 160 == 0 else 7)), fill=GREY, width=2)
        if k % 160 == 0: d.text((ax + k + 4, m + 64), f"00:{int(e['start'] + k * span / 800):02d}", font=F("mono", 14), fill=GREY)
    d.text((m + w - 30, m + 18), f"00:00:{int(t):02d}:{int(t * FPS) % FPS:02d}", font=F("monob", 24), fill=B["accent"], anchor="ra")
    defaults = ["accent", "blue", "green", "coral", "red", "primary"]
    for i, tr in enumerate(tracks):
        ry = m + 100 + i * 86
        d.rounded_rectangle((m + 14, ry, m + w - 14, ry + 70), 10, fill=(32, 37, 45))
        d.text((m + 30, ry + 35), tr["label"], font=F("helvb", 22), fill=B["light"], anchor="lm")
        p = eo(prog(t, tr["at"], 0.35))
        if p <= 0: continue
        c = col(tr.get("color", defaults[i % 6])); x0 = ax + 800 * tr.get("from", 0.0); x1 = ax + 800 * tr.get("to", 1.0); xe = x0 + (x1 - x0) * p
        d.rounded_rectangle((x0, ry + 6, xe, ry + 64), 8, fill=c); dark = tuple(int(v * 0.55) for v in c)
        for j, amp in enumerate(WAVE[i % 6]):
            x = x0 + 8 + j * 7
            if x > xe - 8: break
            d.line((x, ry + 35 - 24 * amp, x, ry + 35 + 24 * amp), fill=dark, width=3)
    px = ax + 800 * prog(t, e["start"], span)
    d.line((px, m + 60, px, m + h - 18), fill=RED, width=3); d.polygon([(px - 10, m + 56), (px + 10, m + 56), (px, m + 72)], fill=RED)
    place(img, im, W / 2, L["zone_cy"] + 80 * (1 - eo(pin)), 1, min(1, pin * 2) * (1 - out))


def _grid_tile(fr, lab, look):
    th = fr.resize((260, 462), Image.BILINEAR)
    if look == "bw": th = th.convert("L").convert("RGB")
    elif look == "warm":
        th = Image.fromarray(np.clip(np.asarray(th).astype(np.float32) * np.array([1.08, 1.0, 0.85], np.float32), 0, 255).astype(np.uint8))
    m = 14; im = card(276, 478, 14, (*B["light"], 255), m).copy(); im.paste(th, (m + 8, m + 8))
    if lab: im.alpha_composite(text_card(lab, "pop", 28, (*B["light"], 255), (*B["primary"], 245), 16, 6, 12, 0), (m + 18, m + 20))
    return im


def fx_playing_grid(img, t, e, buf):
    out = exit_p(t, e); tiles = e["tiles"]; n = len(tiles); gap = min(350, 980 / n); rots = [-5, 0, 5, -3, 3]
    looks = ["norm", "bw", "warm"]
    for k, tl in enumerate(tiles):
        p = prog(t, tl["at"], 0.3)
        if p <= 0: continue
        lag = tl.get("lag", 0.7 * k); fr = None
        if buf: want = t - lag; fr = min(buf, key=lambda x: abs(x[0] - want))[1]
        if fr is None: fr = img.convert("RGB")
        rot = tl.get("rot", rots[k % 5] if n > 1 else 0)
        place(img, _grid_tile(fr, tl.get("label", ""), tl.get("look", looks[k % 3])), W / 2 + (k - (n - 1) / 2) * gap,
              L["zone_cy"] + 420 * (1 - eob(p)), 1, min(1, p * 3) * (1 - out), rot * (1 + 2 * (1 - eo(p))))


_VIG = None; _GRAIN = None


def _film_assets():
    global _VIG, _GRAIN
    if _VIG is None:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        _VIG = np.clip(1 - 0.55 * (((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.62)) ** 2), 0.35, 1)[..., None]
        r = np.random.default_rng(3)
        _GRAIN = [r.normal(0, 9, (H // 2, W // 2, 1)).astype(np.float32).repeat(2, 0).repeat(2, 1) for _ in range(6)]


def arr_film_look(a, t, i, e):
    _film_assets()
    k = eo(prog(t, e["start"], 0.12)) * (1 - exit_p(t, e))
    dy = int(round(2.5 * math.sin(i * 1.9))); src = np.roll(a, dy, axis=0) if dy else a
    g = src * np.array([1.07, 1.0, 0.86], np.float32) + np.array([12, 7, 0], np.float32)
    lum = g.mean(axis=2, keepdims=True); g = lum + (g - lum) * 0.75; g = (g - 128) * 1.1 + 128
    g = g * _VIG * (1 + 0.04 * math.sin(i * 2.7)) + _GRAIN[i % 6]
    leak = 1 - prog(t, e["start"], 0.45)
    if leak > 0: g = g + np.linspace(1, 0, W, dtype=np.float32)[None, :, None] * np.array([255, 140, 40], np.float32) * 0.55 * leak
    if (i // 3) % 4 == 0: x = 120 + (i * 137) % 840; g[:, x:x + 2] += 70
    return a * (1 - k) + g * k


def fx_film_look(img, t, e):
    a = prog(t, e["start"] + 0.1, 0.2) * (1 - exit_p(t, e))
    if e.get("label"): put(img, T(e["label"], "monob", 40, (*B["dark"], 255)), 44, L["top_y"] + 50, a)
    put(img, T(f"{e.get('sublabel', 'REEL 01')} · {int((t - e['start']) * 24):03d}", "mono", 26, (*B["dark"], 255)), 44, L["top_y"] + 108, a)


@lru_cache(4)
def _chrome(handle, caption, likes, comments):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im); sh = (0, 0, 0, 120)
    d.text((W / 2 - 110, 120), "Following", font=F("helvb", 34), fill=(255, 255, 255, 170), anchor="mm")
    d.text((W / 2 + 110, 120), "For You", font=F("helvb", 34), fill=WHITE, anchor="mm"); d.rectangle((W / 2 + 80, 148, W / 2 + 140, 152), fill=WHITE)
    x = 990
    heart(d, x, 1080, 34, (255, 60, 90)); d.text((x, 1140), likes, font=F("helvb", 24), fill=WHITE, anchor="mm", stroke_width=2, stroke_fill=sh)
    d.ellipse((x - 32, 1200, x + 32, 1256), fill=WHITE); d.polygon([(x - 18, 1246), (x - 28, 1270), (x, 1252)], fill=WHITE)
    d.text((x, 1292), comments, font=F("helvb", 24), fill=WHITE, anchor="mm", stroke_width=2, stroke_fill=sh)
    d.polygon([(x - 30, 1380), (x + 6, 1350), (x + 6, 1366), (x + 32, 1366), (x + 32, 1394), (x + 6, 1394), (x + 6, 1410)], fill=WHITE)
    d.text((x, 1440), "Share", font=F("helvb", 24), fill=WHITE, anchor="mm", stroke_width=2, stroke_fill=sh)
    d.text((44, 1700), handle, font=F("helvb", 36), fill=WHITE, stroke_width=2, stroke_fill=sh)
    d.text((44, 1752), caption, font=F("helv", 30), fill=WHITE, stroke_width=2, stroke_fill=sh)
    d.rectangle((0, H - 6, W, H), fill=(255, 255, 255, 90)); d.rectangle((0, H - 6, W * 0.35, H), fill=WHITE)
    return im


@lru_cache(2)
def _feed_post(title1, title2, sub, handle, caption, likes):
    g = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    top = np.array([60, 30, 90], np.float32); bot = np.array([15, 20, 40], np.float32)
    im = Image.fromarray((top * (1 - g) + bot * g).repeat(W, 1).astype(np.uint8)).convert("RGBA")
    d = ImageDraw.Draw(im); rng = random.Random(11)
    for _ in range(140):
        x, y = rng.uniform(0, W), rng.uniform(80, 1350); c = rng.choice([B["accent"], RED, BLUE, GREEN, B["light"], CORAL])
        w, h, a = rng.uniform(10, 26), rng.uniform(18, 40), rng.uniform(0, 3.14); dx, dy = math.cos(a), math.sin(a)
        d.polygon([(x + dx * w - dy * h, y + dy * w + dx * h), (x - dx * w - dy * h, y - dy * w + dx * h),
                   (x - dx * w + dy * h, y - dy * w - dx * h), (x + dx * w + dy * h, y + dy * w - dx * h)], fill=c)
    d.text((W / 2, 760), title1, font=F("pop", 130), fill=B["light"], anchor="mm")
    d.text((W / 2, 920), title2, font=F("pop", 170), fill=B["accent"], anchor="mm")
    if sub: d.text((W / 2, 1060), sub, font=F("it", 56), fill=B["light"], anchor="mm")
    im.alpha_composite(_chrome(handle, caption, likes, "1,204"))
    return im


def _smear(img, horiz=True):
    return img.resize((28, H) if horiz else (W, 40), Image.BILINEAR).resize((W, H), Image.BILINEAR)


def _post(e):
    p = e["post"]
    return _feed_post(p["title1"], p.get("title2", ""), p.get("sub", ""), p.get("handle", "@someone"), p.get("caption", ""), p.get("likes", "48.2K"))


def base_reel_feed(base, t, e):
    st = e["start"]; back = e.get("back_at", st + 0.5 * (e["end"] - st))
    if t >= back + 0.3: return base
    if t < st + 0.25: return Image.blend(base, _smear(base), eo(prog(t, st, 0.25)))
    if t < back:
        x = int(W * (1 - eo(prog(t, st + 0.25, 0.16)))); res = (_smear(base) if x > 0 else Image.new("RGBA", (W, H))).copy()
        res.paste(_post(e), (x, 0)); return res
    q = eo(prog(t, back, 0.3)); y = int(-H * q); res = Image.new("RGBA", (W, H)); res.paste(_post(e), (0, y))
    res.paste(Image.blend(_smear(base, False), base, q) if q < 1 else base, (0, y + H)); return res


def fx_reel_feed(img, t, e):
    back = e.get("back_at", e["start"] + 0.5 * (e["end"] - e["start"]))
    if t >= back:
        a = prog(t, back + 0.15, 0.15) * (1 - exit_p(t, e, 0.15)); own = e.get("own", {})
        img.alpha_composite(fade(_chrome(own.get("handle", S.get("brand", {}).get("handle", "@you")), own.get("caption", ""), own.get("likes", "3,118"), "212"), a))


def _cash_grade(a, k, tint):
    if k <= 0: return a
    g = a * np.array(tint, np.float32); lum = g.mean(axis=2, keepdims=True); g = lum + (g - lum) * 1.1
    return a * (1 - k) + g * k


def _eyedrop_times(e):
    st = e["start"]; pick = e.get("pick_at", st + 0.65); return st, pick, e.get("grade_at", pick + 0.25)


def arr_eyedropper_grade(a, t, i, e):
    st, pick, grade = _eyedrop_times(e)
    tc = hexrgb(e.get("target", "#5f9e6e")); base = np.array([0.82, 1.03, 0.9])
    k = eo(prog(t, grade, 0.8)) * (1 - exit_p(t, e))
    lum = sum(tc) / 3 or 1; tint = (base * 0.5 + np.array(tc) / lum * 0.5).tolist() if e.get("target") else base.tolist()
    return _cash_grade(a, k, tint)


_PICKED = {}


def _pipette():
    im = Image.new("RGBA", (130, 130), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.line((14, 116, 84, 46), fill=B["dark"], width=22); d.line((14, 116, 84, 46), fill=B["light"], width=14)
    d.ellipse((76, 14, 120, 58), fill=B["dark"]); d.ellipse((82, 20, 114, 52), fill=B["accent"]); return im


def fx_eyedropper_grade(img, t, e, arr):
    st, pick, grade = _eyedrop_times(e)
    pts = [tuple(p) for p in e.get("sample_points", [[90, 300], [450, 760], [300, 1500], [540, 1350], [700, 260]])]
    key = id(e)
    if key not in _PICKED: _PICKED[key] = [tuple(int(v) for v in arr[y, x]) for x, y in pts]
    cols = _PICKED[key][:5]; target = hexrgb(e.get("target", "#5f9e6e"))
    pin = prog(t, st, 0.3); out = exit_p(t, e); a = min(1, pin * 2) * (1 - out)
    m = 18; pw, ph = 940, 150; pan = card(pw, ph, 28, (*B["light"], 250), m).copy(); d = ImageDraw.Draw(pan)
    d.text((m + 30, m + 16), "PALETTE", font=F("monob", 18), fill=GREY)
    d.text((m + pw - 30, m + 16), "TARGET: " + e.get("target_label", "NEW LOOK"), font=F("monob", 18), fill=target, anchor="ra")
    for j, c in enumerate(cols + [target]):
        x = m + 30 + j * 148; filled = j > 0 or t >= pick
        d.rounded_rectangle((x, m + 50, x + 130, m + 128), 14, fill=c if filled else (220, 220, 220),
                            outline=B["accent"] if (j == 0 and t >= pick) or (j == len(cols) and t >= grade) else None, width=5)
    zy = L["zone_cy"] + 40
    place(img, pan, W / 2, zy + 60 * (1 - eo(pin)), 1, a)
    sx, sy = pts[0]; p1 = eo(prog(t, st + 0.1, pick - st - 0.1)); p2 = eo(prog(t, grade - 0.05, 0.35))
    x = 900 + (sx - 900) * p1; y = 1500 + (sy - 1500) * p1
    tx = W / 2 - pw / 2 + 30 + len(cols) * 148 + 65; x += (tx - x) * p2; y += (zy - y) * p2
    put(img, _pipette(), x - 14, y - 116, a)
    pc = prog(t, pick - 0.05, 0.3)
    if 0 < pc < 1:
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0)); r = 20 + 60 * pc
        ImageDraw.Draw(lay).ellipse((sx - r, sy - r, sx + r, sy + r), outline=(*B["accent"], int(255 * (1 - pc))), width=6); img.alpha_composite(lay)


def _pop_grade(a):
    g = (a - 128) * 1.16 + 128; lum = g.mean(axis=2, keepdims=True); g = lum + (g - lum) * 1.22
    return g + np.array([8, 2, -10], np.float32)


def _slider_x(t, e):
    st = e["start"]; span = e["end"] - st; x = W + 10
    x = x + (200 - x) * eo(prog(t, st + 0.05, 0.3 * span))
    x = x + (780 - x) * eo(prog(t, st + 0.37 * span, 0.26 * span))
    return x + (540 - x) * eo(prog(t, st + 0.67 * span, 0.17 * span))


def arr_grade_slider(a, t, i, e):
    xi = int(clamp(_slider_x(t, e), 0, W)); a = a.copy(); a[:, xi:] = _pop_grade(a[:, xi:]); return a


def fx_grade_slider(img, t, e):
    x = _slider_x(t, e); a = 1 - exit_p(t, e, 0.15); cy = L["zone_cy"]
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    d.rectangle((x - 2, 0, x + 2, H), fill=(255, 255, 255, int(235 * a))); d.ellipse((x - 38, cy - 38, x + 38, cy + 38), fill=(255, 255, 255, int(250 * a)))
    d.polygon([(x - 8, cy - 12), (x - 22, cy), (x - 8, cy + 12)], fill=(*B["primary"], int(255 * a)))
    d.polygon([(x + 8, cy - 12), (x + 22, cy), (x + 8, cy + 12)], fill=(*B["primary"], int(255 * a))); img.alpha_composite(lay)
    put(img, text_card(e.get("before", "BEFORE"), "monob", 24, (*WHITE, 255), (*B["dark"], 210), 14, 6, 8, 0), x - 210, cy - 22, a)
    put(img, text_card(e.get("after", "AFTER"), "monob", 24, (*B["dark"], 255), (*B["light"], 235), 14, 6, 8, 0), x + 56, cy - 22, a)


def fx_video_card(img, t, e, base_rgb):
    pin = prog(t, e["start"], 0.35); out = exit_p(t, e); w, h, m = 620, 470, 22
    c = card(w, h, 30, (*B["primary"], 250), m).copy(); d = ImageDraw.Draw(c)
    cy0 = e.get("thumb_y", 330)
    c.paste(base_rgb.crop((0, cy0, 1080, cy0 + 608)).resize((580, 326), Image.BILINEAR), (m + 20, m + 20), rounded_mask(580, 326, 18))
    cx, cy = m + 310, m + 183
    d.ellipse((cx - 40, cy - 40, cx + 40, cy + 40), fill=(0, 0, 0, 120)); d.polygon([(cx - 12, cy - 20), (cx - 12, cy + 20), (cx + 22, cy)], fill=WHITE)
    d.rounded_rectangle((m + 20, m + 332, m + 600, m + 338), 3, fill=(255, 255, 255, 70))
    d.rounded_rectangle((m + 20, m + 332, m + 20 + 580 * prog(t, e["start"], e["end"] - e["start"]), m + 338), 3, fill=B["accent"])
    d.text((m + 22, m + 360), e["title"], font=F("didot", 60), fill=B["light"])
    if e.get("meta"): d.text((m + 22, m + 432), e["meta"], font=F("helv", 26), fill=(*B["light"], 190))
    place(img, c, W / 2, L["zone_cy"] + 10 + 120 * (1 - eob(pin)), 1, min(1, pin * 3) * (1 - out))


def _comment_pill(typed, active, placeholder):
    m = 18; w, h = 940, 116; c = card(w, h, 58, (*WHITE, 255), m).copy(); d = ImageDraw.Draw(c)
    d.ellipse((m + 22, m + 22, m + 94, m + 94), fill=(205, 210, 216))
    if typed: d.text((m + 116, m + h / 2), typed, font=F("helvb", 44), fill=B["dark"], anchor="lm")
    else: d.text((m + 116, m + h / 2), placeholder, font=F("helv", 40), fill=GREY, anchor="lm")
    d.text((m + w - 40, m + h / 2), "Post", font=F("helvb", 38), fill=B["primary"] if active else (190, 196, 204), anchor="rm"); return c


@lru_cache(4)
def _comment_bubble(word, liked, who):
    m = 18; w, h = 940, 150; c = card(w, h, 32, (*WHITE, 255), m).copy(); d = ImageDraw.Draw(c)
    d.ellipse((m + 22, m + 24, m + 94, m + 96), fill=(205, 210, 216))
    d.text((m + 116, m + 30), who, font=F("helv", 26), fill=GREY); d.text((m + 116, m + 66), word, font=F("helvb", 48), fill=B["dark"])
    if liked: heart(d, m + w - 60, m + h / 2 - 6, 26, RED)
    else: heart(d, m + w - 60, m + h / 2 - 6, 26, (200, 204, 210)); heart(d, m + w - 60, m + h / 2 - 4, 19, WHITE)
    return c


@lru_cache(2)
def _reply_bubble(name, text):
    m = 18; w, h = 860, 124; c = card(w, h, 30, (*B["light"], 255), m).copy(); d = ImageDraw.Draw(c)
    d.ellipse((m + 20, m + 20, m + 84, m + 84), fill=B["primary"])
    d.text((m + 52, m + 52), name.lstrip("@")[:1].upper(), font=F("pop", 36), fill=B["light"], anchor="mm")
    d.text((m + 104, m + 22), name + " · creator", font=F("helvb", 26), fill=B["primary"]); d.text((m + 104, m + 58), text, font=F("helv", 34), fill=B["dark"])
    return c


def _cta_line(img, t, e, y):
    if e.get("line"):
        place(img, T(e["line"], "pop", 52, (*WHITE, 255), 6, (*B["dark"], 255)), W / 2, y, 1, eo(prog(t, e["start"] + 0.15, 0.3)))


def fx_comment_cta(img, t, e):
    word = e["keyword"]; type_at = e.get("type_at", e["start"] + 0.35)
    pin = eo(prog(t, e["start"], 0.3)); out = exit_p(t, e, 0.15)
    n = int(len(word) * prog(t, type_at, 0.5))
    place(img, _comment_pill(word[:n], n == len(word), e.get("placeholder", "Add a comment...")), W / 2, L["zone_cy"] - 110 + 60 * (1 - pin), 1, pin * (1 - out))
    if e.get("slam_at") is not None:
        ps = prog(t, e["slam_at"], 0.2)
        if ps > 0: place(img, text_card(word, "pop", 120, (*B["light"], 255), (*B["primary"], 250), 30, 12, 16, 18), 330, L["top_y"] + 160, 1 + 0.5 * (1 - eo(ps)), min(1, ps * 3))
    _cta_line(img, t, e, L["zone_cy"] + 40)


def fx_comment_drop(img, t, e):
    word = e["keyword"]; like_at = e.get("like_at", e["start"] + 0.6)
    pd = prog(t, e["start"], 0.5); zy = L["zone_cy"] - 110
    place(img, _comment_bubble(word, t >= like_at, e.get("who", "you · now")), W / 2, zy - 300 + 300 * bounce(pd),
          1 + 0.08 * math.sin(math.pi * prog(t, like_at, 0.18)), min(1, pd * 4))
    if e.get("reply"):
        r = e["reply"]; pr = prog(t, r.get("at", e["start"] + 1.0), 0.3)
        if pr > 0: place(img, _reply_bubble(r.get("name", S.get("brand", {}).get("handle", "@you")), r["text"]), W / 2 + 40, zy + 160 + 40 * (1 - eo(pr)), 1, eo(pr))
    if e.get("keep_slam", True):
        prev = [x for x in S["effects"] if x["type"] == "comment_cta" and x.get("slam_at") is not None]
        if prev: place(img, text_card(prev[-1]["keyword"], "pop", 120, (*B["light"], 255), (*B["primary"], 250), 30, 12, 16, 18), 330, L["top_y"] + 160)
    _cta_line(img, t, e, L["zone_cy"] + 230)


OVERLAY = {"kinetic_hook": fx_kinetic_hook, "gravity_drop": fx_gravity_drop, "giant_captions": fx_giant_captions,
           "prompt_bar": fx_prompt_bar, "file_drop": fx_file_drop, "storyboard": fx_storyboard,
           "jiggle_delete": fx_jiggle_delete, "icon_strike": fx_icon_strike, "nle_timeline": fx_nle_timeline,
           "film_look": fx_film_look, "reel_feed": fx_reel_feed, "grade_slider": fx_grade_slider,
           "comment_cta": fx_comment_cta, "comment_drop": fx_comment_drop}
ARRAY = {"film_look": arr_film_look, "eyedropper_grade": arr_eyedropper_grade, "grade_slider": arr_grade_slider}
BASE = {"title_card": fx_title_card, "reel_feed": base_reel_feed}
KNOWN = set(LABELS)


# ---------------------------------------------------------------- frame assembly
def render_frame(arr, t, i, buf=None):
    live = [e for e in S["effects"] if e["start"] <= t < e["end"]]
    a = None
    for e in live:
        if e["type"] in ARRAY:
            a = ARRAY[e["type"]](arr.astype(np.float32) if a is None else a, t, i, e)
    arr2 = np.clip(a, 0, 255).astype(np.uint8) if a is not None else arr
    base = Image.fromarray(arr2).convert("RGBA")
    for e in live:
        if e["type"] in BASE: base = BASE[e["type"]](base, t, e)
    img = base.copy()
    base_rgb = None
    for e in live:
        ty = e["type"]
        if ty in OVERLAY: OVERLAY[ty](img, t, e)
        elif ty == "playing_grid": fx_playing_grid(img, t, e, buf)
        elif ty == "eyedropper_grade": fx_eyedropper_grade(img, t, e, arr)
        elif ty == "video_card":
            base_rgb = base_rgb or Image.fromarray(arr2); fx_video_card(img, t, e, base_rgb)
    captions(img, t); sidebar(img, t)
    return img.convert("RGB")


def validate():
    bad = [e["type"] for e in S["effects"] if e["type"] not in KNOWN]
    if bad: sys.exit(f"unknown effect type(s): {bad}. Known: {sorted(KNOWN)}")


# ---------------------------------------------------------------- SFX (synthesized: no API spend)
SR = 48000
_srng = np.random.default_rng(5)


def _env(n, attack=0.005, decay=0.1):
    tt = np.arange(n) / SR; return np.clip(tt / attack, 0, 1) * np.exp(-tt / decay)


def _band(x, lo, hi):
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / SR); X[(f < lo) | (f > hi)] = 0; return np.fft.irfft(X, len(x))


def s_whoosh(d=0.35):
    n = int(d * SR); x = _band(_srng.normal(0, 1, n), 300, 4000); return x / np.abs(x).max() * np.sin(np.linspace(0, np.pi, n)) ** 2 * 0.5


def s_pop(f0=700, f1=280, d=0.08):
    n = int(d * SR); return np.sin(2 * np.pi * np.cumsum(np.linspace(f0, f1, n)) / SR) * _env(n, 0.002, 0.03) * 0.6


def s_click():
    n = int(0.03 * SR); return _band(_srng.normal(0, 1, n), 1500, 8000) * _env(n, 0.0005, 0.006) * 0.35


def s_thud():
    n = int(0.25 * SR); x = np.sin(2 * np.pi * np.cumsum(np.linspace(110, 45, n)) / SR) * _env(n, 0.002, 0.08) * 0.8
    x[: int(0.03 * SR)] += s_click(); return x


def s_ding():
    n = int(0.7 * SR); tt = np.arange(n) / SR
    return (np.sin(2 * np.pi * 1320 * tt) + 0.5 * np.sin(2 * np.pi * 1980 * tt)) * _env(n, 0.003, 0.18) * 0.3


def s_scratch():
    n = int(0.22 * SR); return _band(_srng.normal(0, 1, n), 1200, 6000) * _env(n, 0.01, 0.08) * 0.35


def sfx_events():
    ev = []; at = lambda t, s, g=1.0: ev.append((t, s, g))
    for e in S["effects"]:
        ty, st = e["type"], e["start"]
        if ty == "kinetic_hook":
            for ln in e["lines"]: at(ln["at"], s_thud(), 0.9)
        elif ty == "gravity_drop":
            for g in e["groups"]:
                for k in range(len(g["text"].replace(" ", ""))): at(g["at"] + k * g.get("stagger", 0.045) + g.get("dur", 0.5) * 0.36, s_click(), 0.6)
        elif ty == "giant_captions":
            for it in e["items"]: at(it["at"], s_pop(), 0.8)
        elif ty == "prompt_bar":
            ta = e.get("type_at", st + 0.1); td = e.get("type_dur", min(1.6, 0.04 * len(e["text"]))); sa = e.get("send_at", ta + td + 0.15)
            for k in range(len(e["text"])): at(ta + k * td / len(e["text"]), s_click(), 0.5)
            at(sa, s_pop(900, 500)); at(e.get("answer_at", sa + 0.2), s_pop(600, 400))
        elif ty == "file_drop":
            da = e.get("drop_at", st + 0.75); at(st, s_whoosh(0.3), 0.6); at(da, s_whoosh(0.4)); at(da + 0.35, s_thud(), 0.7)
        elif ty == "storyboard":
            for c in e["cards"]: at(c["at"], s_whoosh(0.25), 0.7)
        elif ty == "jiggle_delete":
            for k in range(len(e["icons"])): at(e.get("delete_at", st + 1.0) + e.get("delete_gap", 0.2) * k, s_pop(500, 900, 0.07))
        elif ty == "icon_strike":
            for ic in e["icons"]:
                at(ic["at"], s_pop())
                if ic.get("strike_at") is not None: at(ic["strike_at"], s_scratch())
        elif ty == "title_card":
            at(st, s_whoosh(0.35)); at(e.get("phone_at", st + 0.53 * (e["end"] - st)), s_whoosh(0.3), 0.7); at(e.get("expand_at", e["end"] - 0.4), s_whoosh(0.35))
        elif ty == "nle_timeline":
            for tr in e["tracks"]: at(tr["at"], s_click(), 1.0)
        elif ty == "playing_grid":
            for tl in e["tiles"]: at(tl["at"], s_whoosh(0.25), 0.7)
        elif ty == "film_look":
            at(st, s_whoosh(0.5), 0.6)
            for k in range(int((e["end"] - st) * 24)): at(st + 0.05 + k / 24, s_click(), 0.12)
        elif ty == "reel_feed":
            at(st, s_whoosh(0.3)); at(e.get("back_at", st + 0.5 * (e["end"] - st)), s_whoosh(0.3))
        elif ty == "eyedropper_grade":
            _, pk, gr = _eyedrop_times(e); at(pk - 0.05, s_click(), 1.2); at(gr, s_pop(400, 800))
        elif ty == "grade_slider": at(st + 0.05, s_whoosh(0.6), 0.5)
        elif ty == "video_card": at(st, s_whoosh(0.35))
        elif ty == "comment_cta":
            ta = e.get("type_at", st + 0.35)
            for k in range(len(e["keyword"])): at(ta + k * 0.5 / len(e["keyword"]), s_click(), 0.5)
            if e.get("slam_at") is not None: at(e["slam_at"], s_thud(), 0.8)
        elif ty == "comment_drop":
            at(st, s_pop()); at(e.get("like_at", st + 0.6), s_pop(800, 1200))
            if e.get("reply"): at(e["reply"].get("at", st + 1.0), s_ding())
    return ev


def build_mix(out_wav):
    src = S.get("audio") or S["source"]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    voice = np.frombuffer(raw, np.float32)
    snd = S.get("sound", {}); bus = np.zeros(len(voice) + SR, np.float32)
    if snd.get("sfx", True):
        for t, s, g in sfx_events():
            i = int(t * SR)
            if 0 <= i < len(bus): bus[i:i + len(s)] += (s * g)[: len(bus) - i]
    mix = voice + bus[: len(voice)] * 10 ** (snd.get("sfx_db", -12) / 20)
    target = snd.get("lufs", -15); tmp = out_wav + ".pre.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-", "-af",
                    f"loudnorm=I={target}:TP=-1.5:LRA=11", "-ar", str(SR), "-ac", "2", tmp],
                   input=mix.astype(np.float32).tobytes(), check=True)
    # single-pass loudnorm lands ~2 LU low on speech: measure, then trim the gain (never past -1 dBTP)
    i_lufs, peak = measure(tmp); gain = min(target - i_lufs, -1.0 - peak)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-af", f"volume={gain:.2f}dB", out_wav], check=True)
    os.remove(tmp); i2, p2 = measure(out_wav); print(f"mix: {i2:.1f} LUFS, peak {p2:.1f} dBTP (target {target})")


def measure(path):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr.split("Summary:")[-1]
    i = float(err.split("I:")[1].split("LUFS")[0]); p = float(err.split("Peak:")[1].split("dBFS")[0])
    return i, p


def cmd_remix(spec):
    """Audio-only change (sfx_db, lufs, effect timings' SFX): rebuild the mix onto the last rendered picture."""
    load_spec(spec); qa = os.path.join(S["_dir"], "_qa"); vid = os.path.join(qa, "video_only.mp4"); wav = os.path.join(qa, "mix.wav")
    out = S["output"]
    if os.path.exists(out): sys.exit(f"{out} exists: new versions are new files (bump the output name)")
    build_mix(wav)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", vid, "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], check=True); print("wrote", out)


# ---------------------------------------------------------------- commands
def cmd_render(spec, clips):
    load_spec(spec); validate(); d = S["_dir"]; qa = os.path.join(d, "_qa"); os.makedirs(qa, exist_ok=True)
    out = S.get("output") or os.path.join(d, "out", "fx-v1.mp4"); os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out): sys.exit(f"{out} exists: new versions are new files (bump the output name)")
    vid = os.path.join(qa, "video_only.mp4"); wav = os.path.join(qa, "mix.wav")
    N = int(round(DUR * FPS))
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", S["source"], "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", vid], stdin=subprocess.PIPE)
    grids = [e for e in S["effects"] if e["type"] == "playing_grid"]
    buf = deque(maxlen=90); fsz = W * H * 3; i = 0
    while True:
        raw = dec.stdout.read(fsz)
        if len(raw) < fsz: break
        t = i / FPS; arr = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
        if any(g["start"] - 2.5 <= t < g["end"] for g in grids): buf.append((t, Image.fromarray(arr).resize((270, 480), Image.BILINEAR)))
        enc.stdin.write(render_frame(arr, t, i, list(buf) if buf else None).tobytes())
        if i % 150 == 0: print(f"frame {i}/{N}", flush=True)
        i += 1
    enc.stdin.close(); enc.wait(); dec.wait()
    build_mix(wav)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", vid, "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], check=True)
    print("wrote", out)
    if clips:
        cd = os.path.join(os.path.dirname(out), "clips"); os.makedirs(cd, exist_ok=True); stem = os.path.splitext(os.path.basename(out))[0]
        for k, e in enumerate(S["effects"], 1):
            slug = e.get("label", LABELS[e["type"]]).lower().replace(" ", "-")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(max(0, e["start"] - 0.3)), "-to", str(min(DUR, e["end"] + 0.3)), "-i", out,
                            "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                            os.path.join(cd, f"{stem}_{k:02d}_{slug}.mp4")], check=True)
        print("clips ->", cd)


def cmd_stills(spec, times):
    load_spec(spec); validate(); od = os.path.join(S["_dir"], "_qa", "stills"); os.makedirs(od, exist_ok=True)
    paths = []
    for ts in times:
        t = float(ts); p = os.path.join(od, f"s_{t:06.2f}.png")
        render_frame(np.asarray(grab(t)), t, int(t * FPS)).save(p); paths.append(p)
    cols = min(10, len(paths)); rows = (len(paths) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 270, rows * 480))
    for k, p in enumerate(paths): sheet.paste(Image.open(p).resize((270, 480)), ((k % cols) * 270, (k // cols) * 480))
    sheet.save(os.path.join(S["_dir"], "_qa", "stills-sheet.jpg")); print("sheet:", os.path.join(S["_dir"], "_qa", "stills-sheet.jpg"))


def cmd_transcribe(video, out):
    model = os.path.expanduser(os.environ.get("WHISPER_MODEL", "~/.cache/whisper/ggml-small.en.bin"))
    tmp = out + ".16k.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-ar", "16000", "-ac", "1", tmp], check=True)
    subprocess.run(["whisper-cli", "-ng", "-m", model, "-f", tmp, "-ml", "1", "-sow", "-ojf", "-of", out + ".raw"], check=True, capture_output=True)
    d = json.load(open(out + ".raw.json")); words = []
    for s in d["transcription"]:
        tx = s["text"].strip()
        if tx and not tx.startswith("["): words.append({"w": tx, "s": s["offsets"]["from"] / 1000, "e": s["offsets"]["to"] / 1000})
    json.dump(words, open(out, "w")); os.remove(tmp); os.remove(out + ".raw.json")
    print(" ".join(f"{w['s']:.1f}:{w['w']}" for w in words))


def cmd_face(video, out, t="5"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", video, "-frames:v", "1", "-vf",
                    "scale=540:-1,drawgrid=w=50:h=50:t=1:c=red@0.5", out], check=True)
    print(out, "(grid = 100px at full 1080x1920 scale)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    if a[0] == "render": cmd_render(a[1], "--clips" in a)
    elif a[0] == "stills": cmd_stills(a[1], a[2:])
    elif a[0] == "transcribe": cmd_transcribe(a[1], a[2])
    elif a[0] == "face": cmd_face(*a[1:])
    elif a[0] == "remix": cmd_remix(a[1])
    else: sys.exit(__doc__)
