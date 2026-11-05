#!/usr/bin/env python3
"""Draw the expert-team avatars: fuzzy, big-eyed animal characters.

Everything is drawn procedurally (fur is thousands of short strokes), so the
images are original work with no third-party source or license to track.
Each character is seeded, so re-running reproduces the same picture.

Usage:
    python3 scripts/gen_team_avatars.py [out_dir]     # default: workbuddy/astra-studio-team/avatars

Needs Pillow.
"""

import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "workbuddy" / "astra-studio-team" / "avatars"
S = 1024          # working canvas; downsampled to 512
CX, CY = 512, 470  # head centre


def clamp(v):
    return max(0, min(255, int(v)))


def shade(color, k):
    return tuple(clamp(c * k) for c in color)


def mix(a, b, t):
    return tuple(clamp(a[i] + (b[i] - a[i]) * t) for i in range(3))


def fur(draw, rng, cx, cy, rx, ry, color_fn, n, inner=14, outer=46, width=3,
        rot=0.0, jitter=0.55, droop=0.0):
    """Fill an ellipse with strokes pointing outward; longer toward the rim."""
    cr, sr = math.cos(rot), math.sin(rot)
    strokes = []
    for _ in range(n):
        r = math.sqrt(rng.random())
        a = rng.uniform(0, 2 * math.pi)
        ex, ey = r * rx * math.cos(a), r * ry * math.sin(a)
        x, y = cx + ex * cr - ey * sr, cy + ex * sr + ey * cr
        ang = math.atan2(y - cy, x - cx) + rng.gauss(0, jitter)
        length = (inner + (outer - inner) * r ** 3) * rng.uniform(0.6, 1.5)
        dx, dy = math.cos(ang) * length, math.sin(ang) * length + droop * length
        nx, ny = (x - cx) / rx, (y - cy) / ry
        base = color_fn(nx, ny)
        # light from the upper left, a little noise per hair
        light = 1.0 - 0.16 * ny - 0.10 * nx + rng.uniform(-0.13, 0.13)
        strokes.append((r, (x, y, x + dx, y + dy), shade(base, light)))
    strokes.sort(key=lambda s: -s[0])  # rim first, so the centre reads as dense
    for _, line, col in strokes:
        draw.line(line, fill=col, width=width)


def stray_hairs(draw, rng, cx, cy, rx, ry, color, n, lo=50, hi=130, top_bias=True):
    """Long thin hairs escaping the silhouette."""
    for _ in range(n):
        a = rng.uniform(-math.pi, 0) if top_bias and rng.random() < 0.7 else rng.uniform(0, 2 * math.pi)
        x, y = cx + rx * 0.96 * math.cos(a), cy + ry * 0.96 * math.sin(a)
        ang = a + rng.gauss(0, 0.35)
        length = rng.uniform(lo, hi)
        bend = rng.gauss(0, 0.35)
        pts = [(x, y)]
        for i in range(1, 5):
            ang += bend / 4
            x += math.cos(ang) * length / 4
            y += math.sin(ang) * length / 4
            pts.append((x, y))
        draw.line(pts, fill=shade(color, rng.uniform(0.7, 1.15)), width=2)


def rot_ellipse(cx, cy, rx, ry, rot, steps=48):
    cr, sr = math.cos(rot), math.sin(rot)
    return [(cx + rx * math.cos(t) * cr - ry * math.sin(t) * sr,
             cy + rx * math.cos(t) * sr + ry * math.sin(t) * cr)
            for t in (2 * math.pi * i / steps for i in range(steps))]


def round_eye(draw, x, y, r, look=(0.0, 0.0), iris=None, pupil=0.42, rim=(40, 32, 30)):
    draw.ellipse((x - r - 9, y - r - 7, x + r + 9, y + r + 11), fill=shade(rim, 1.0))
    draw.ellipse((x - r, y - r, x + r, y + r), fill=(252, 251, 247))
    px, py = x + look[0] * r * 0.38, y + look[1] * r * 0.38
    if iris:
        ir = r * 0.66
        draw.ellipse((px - ir, py - ir, px + ir, py + ir), fill=iris)
    pr = r * pupil
    draw.ellipse((px - pr, py - pr, px + pr, py + pr), fill=(22, 18, 18))
    hr = pr * 0.34
    draw.ellipse((px - pr * 0.45 - hr, py - pr * 0.5 - hr, px - pr * 0.45 + hr, py - pr * 0.5 + hr),
                 fill=(255, 255, 255))


def stern_eye(draw, x, y, side, color=(226, 206, 60)):
    """Narrowed, slanted eye with a heavy brow."""
    rot = side * 0.30
    draw.polygon(rot_ellipse(x, y, 74, 30, rot), fill=(20, 18, 16))
    draw.polygon(rot_ellipse(x, y + 3, 64, 21, rot), fill=color)
    draw.polygon(rot_ellipse(x - side * 6, y + 3, 9, 19, rot), fill=(18, 16, 14))
    bx, by = math.cos(rot), math.sin(rot)
    draw.line((x - bx * 86, y - by * 86 - 30, x + bx * 80, y + by * 80 - 22), fill=(14, 12, 12), width=20)


def nose(draw, x, y, w, h, color=(34, 28, 28)):
    draw.rounded_rectangle((x - w, y - h, x + w, y + h), radius=h, fill=color)
    draw.ellipse((x - w * 0.55, y - h * 0.62, x - w * 0.05, y - h * 0.12), fill=mix(color, (255, 255, 255), 0.45))


def smile(draw, x, y, w, depth, color=(40, 30, 28), width=8):
    draw.arc((x - w, y - depth, x + w, y + depth), start=18, end=162, fill=color, width=width)


def frown(draw, x, y, w, depth, color=(20, 16, 16), width=8):
    draw.arc((x - w, y, x + w, y + depth * 2), start=200, end=340, fill=color, width=width)


def tongue(draw, x, y, w, h):
    draw.rounded_rectangle((x - w, y, x + w, y + h), radius=w, fill=(236, 128, 140))
    draw.line((x, y + 6, x, y + h * 0.6), fill=(206, 96, 112), width=4)


def shoulders(draw, rng, color_fn, y=955, rx=300, ry=190, n=5200):
    fur(draw, rng, CX, y, rx, ry, color_fn, n, inner=16, outer=52)


# ---------------------------------------------------------------- characters

def collie(d, rng):  # lead: border collie, alert and friendly
    black, white = (38, 36, 40), (244, 240, 230)
    blaze = lambda nx, ny: white if abs(nx) < 0.17 + 0.30 * max(0.0, ny + 0.15) else black
    shoulders(d, rng, lambda nx, ny: white if abs(nx) < 0.55 else black)
    for side in (-1, 1):  # pricked ears, tips leaning outward
        d.polygon([(CX + side * 110, CY - 210), (CX + side * 322, CY - 452), (CX + side * 326, CY - 150)],
                  fill=black)
        fur(d, rng, CX + side * 250, CY - 285, 62, 138, lambda nx, ny: black, 2400,
            inner=14, outer=44, rot=side * 0.5)
        fur(d, rng, CX + side * 246, CY - 256, 24, 74, lambda nx, ny: (190, 146, 148), 420,
            inner=8, outer=16, rot=side * 0.5)
    fur(d, rng, CX, CY, 318, 292, blaze, 15000, inner=16, outer=62)
    stray_hairs(d, rng, CX, CY, 318, 292, black, 70)
    fur(d, rng, CX, CY + 128, 150, 118, lambda nx, ny: white, 3600, inner=12, outer=34)
    round_eye(d, CX - 128, CY - 42, 62, look=(0.25, -0.1), iris=(150, 96, 40))
    round_eye(d, CX + 128, CY - 42, 62, look=(-0.1, -0.1), iris=(150, 96, 40))
    smile(d, CX, CY + 150, 96, 58)
    tongue(d, CX + 10, CY + 196, 34, 78)
    nose(d, CX, CY + 92, 62, 42)


def hamster(d, rng):  # product manager: warm, curious
    orange, cream = (236, 152, 62), (250, 236, 208)
    coat = lambda nx, ny: cream if ny > 0.30 and abs(nx) < 0.62 else orange
    shoulders(d, rng, lambda nx, ny: cream if abs(nx) < 0.5 else orange)
    for side in (-1, 1):
        fur(d, rng, CX + side * 232, CY - 262, 70, 70, lambda nx, ny: (120, 96, 84), 1400, inner=10, outer=26)
        fur(d, rng, CX + side * 232, CY - 256, 36, 36, lambda nx, ny: (226, 170, 160), 400, inner=6, outer=14)
    fur(d, rng, CX, CY + 20, 372, 300, coat, 17000, inner=16, outer=56)
    stray_hairs(d, rng, CX, CY + 20, 372, 300, orange, 50, lo=40, hi=100)
    for side in (-1, 1):  # cheek blush + whiskers
        fur(d, rng, CX + side * 214, CY + 96, 62, 44, lambda nx, ny: (244, 170, 150), 700, inner=8, outer=18)
        for k in range(3):
            d.line((CX + side * 150, CY + 120 + k * 16, CX + side * (330 + rng.randint(0, 60)),
                    CY + 96 + k * 30), fill=(250, 246, 238), width=3)
    round_eye(d, CX - 96, CY - 50, 50, look=(0.1, 0.15), pupil=0.5)
    round_eye(d, CX + 96, CY - 50, 50, look=(-0.1, 0.15), pupil=0.5)
    nose(d, CX, CY + 52, 26, 18, color=(214, 118, 118))
    d.line((CX, CY + 70, CX, CY + 104), fill=(70, 50, 44), width=6)
    smile(d, CX - 34, CY + 78, 36, 30, width=6)
    smile(d, CX + 34, CY + 78, 36, 30, width=6)


def schnauzer(d, rng):  # architect: composed, bearded
    dark, grey, light = (58, 60, 68), (118, 122, 132), (206, 208, 212)
    shoulders(d, rng, lambda nx, ny: dark)
    for side in (-1, 1):
        fur(d, rng, CX + side * 196, CY - 300, 78, 112, lambda nx, ny: dark, 2000,
            inner=14, outer=40, rot=side * 0.22)
    fur(d, rng, CX, CY - 30, 292, 286, lambda nx, ny: dark if ny < 0.1 else grey, 13000, inner=14, outer=48)
    stray_hairs(d, rng, CX, CY - 30, 292, 286, dark, 40, lo=30, hi=80)
    # beard: long drooping hair around the muzzle
    fur(d, rng, CX, CY + 190, 232, 190, lambda nx, ny: mix(grey, light, 0.5 + 0.5 * ny), 9000,
        inner=30, outer=92, width=3, jitter=0.35, droop=0.55)
    for side in (-1, 1):  # eyebrows
        fur(d, rng, CX + side * 124, CY - 150, 92, 40, lambda nx, ny: light, 1300,
            inner=18, outer=44, rot=side * 0.25, droop=0.3)
    round_eye(d, CX - 116, CY - 70, 56, look=(0.2, 0.05), pupil=0.4)
    round_eye(d, CX + 116, CY - 70, 56, look=(-0.2, 0.05), pupil=0.4)
    nose(d, CX, CY + 70, 72, 46)


def siamese(d, rng):  # UX researcher: bright-eyed, empathetic
    cream, point = (232, 214, 190), (92, 70, 62)
    mask = lambda nx, ny: mix(point, cream, min(1.0, (nx * nx * 2.2 + (ny - 0.28) ** 2 * 2.6) * 1.6))
    shoulders(d, rng, lambda nx, ny: cream)
    for side in (-1, 1):
        d.polygon([(CX + side * 120, CY - 220), (CX + side * 318, CY - 470), (CX + side * 330, CY - 130)],
                  fill=point)
        fur(d, rng, CX + side * 258, CY - 280, 76, 150, lambda nx, ny: point, 2600,
            inner=12, outer=34, rot=side * 0.42)
        fur(d, rng, CX + side * 250, CY - 250, 30, 86, lambda nx, ny: (206, 160, 156), 500,
            inner=8, outer=18, rot=side * 0.42)
    fur(d, rng, CX, CY + 10, 330, 286, mask, 16000, inner=14, outer=52)
    stray_hairs(d, rng, CX, CY + 10, 330, 286, cream, 46, lo=36, hi=96)
    round_eye(d, CX - 120, CY - 30, 66, look=(0.2, 0.1), iris=(86, 176, 214), pupil=0.36)
    round_eye(d, CX + 120, CY - 30, 66, look=(-0.2, 0.1), iris=(86, 176, 214), pupil=0.36)
    nose(d, CX, CY + 84, 24, 17, color=(206, 130, 136))
    d.line((CX, CY + 100, CX, CY + 128), fill=(48, 36, 34), width=6)
    smile(d, CX - 44, CY + 98, 46, 40, width=7)
    smile(d, CX + 44, CY + 98, 46, 40, width=7)
    for side in (-1, 1):
        for k in range(3):
            d.line((CX + side * 96, CY + 124 + k * 14, CX + side * (300 + rng.randint(0, 70)),
                    CY + 100 + k * 34), fill=(250, 246, 240), width=3)


def orangutan(d, rng):  # domain expert: wise, a bit wild-haired
    rust, face = (176, 84, 36), (150, 122, 104)
    shoulders(d, rng, lambda nx, ny: rust)
    fur(d, rng, CX, CY - 30, 340, 320, lambda nx, ny: rust, 15000, inner=20, outer=96, jitter=0.4)
    stray_hairs(d, rng, CX, CY - 30, 340, 320, rust, 150, lo=70, hi=190)
    for side in (-1, 1):
        fur(d, rng, CX + side * 282, CY + 24, 46, 62, lambda nx, ny: (214, 150, 118), 700, inner=8, outer=18)
    fur(d, rng, CX, CY + 20, 236, 226, lambda nx, ny: face, 9000, inner=8, outer=22, width=3)
    fur(d, rng, CX, CY + 128, 190, 110, lambda nx, ny: (226, 170, 134), 5200, inner=8, outer=20)
    round_eye(d, CX - 94, CY - 34, 58, look=(0.15, 0.0), pupil=0.5)
    round_eye(d, CX + 94, CY - 34, 58, look=(-0.15, 0.0), pupil=0.5)
    nose(d, CX, CY + 58, 24, 13, color=(70, 52, 46))
    smile(d, CX, CY + 62, 150, 92, width=7)


def puppy(d, rng):  # skill builder: eager, scruffy
    white, brown = (240, 234, 222), (98, 66, 52)
    patch = lambda nx, ny: brown if (nx + 0.42) ** 2 + (ny + 0.22) ** 2 < 0.20 else white
    shoulders(d, rng, lambda nx, ny: brown if nx > 0.25 and ny < 0.2 else white)
    for side in (-1, 1):  # floppy ears
        fur(d, rng, CX + side * 300, CY - 120, 104, 178, lambda nx, ny: brown, 3400,
            inner=18, outer=56, rot=side * -0.42, droop=0.25)
    fur(d, rng, CX, CY, 300, 280, patch, 14000, inner=18, outer=72, jitter=0.6)
    stray_hairs(d, rng, CX, CY, 300, 280, (150, 130, 118), 110, lo=50, hi=150)
    fur(d, rng, CX, CY + 124, 150, 112, lambda nx, ny: white, 3400, inner=12, outer=32)
    round_eye(d, CX - 104, CY - 46, 66, look=(0.3, -0.05), pupil=0.4)
    round_eye(d, CX + 110, CY - 56, 58, look=(-0.3, 0.1), pupil=0.4)
    smile(d, CX + 6, CY + 104, 132, 92, width=8)
    nose(d, CX, CY + 86, 58, 42)


def black_cat(d, rng):  # quality reviewer: unimpressed until it passes
    coat = (46, 44, 52)
    shoulders(d, rng, lambda nx, ny: coat)
    for side in (-1, 1):
        d.polygon([(CX + side * 110, CY - 250), (CX + side * 232, CY - 400), (CX + side * 300, CY - 190)],
                  fill=coat)
        fur(d, rng, CX + side * 222, CY - 290, 62, 86, lambda nx, ny: coat, 1500,
            inner=12, outer=30, rot=side * 0.35)
    fur(d, rng, CX, CY + 10, 356, 312, lambda nx, ny: mix(coat, (96, 90, 104), max(0.0, -ny) * 0.5),
        18000, inner=18, outer=66)
    stray_hairs(d, rng, CX, CY + 10, 356, 312, (70, 66, 78), 120, lo=40, hi=110, top_bias=False)
    stern_eye(d, CX - 118, CY - 40, -1)
    stern_eye(d, CX + 118, CY - 40, 1)
    nose(d, CX, CY + 58, 20, 13, color=(96, 84, 92))
    frown(d, CX, CY + 96, 62, 26)
    for side in (-1, 1):
        for k in range(3):
            d.line((CX + side * 120, CY + 92 + k * 16, CX + side * (410 + rng.randint(0, 70)),
                    CY + 60 + k * 44), fill=(150, 146, 158), width=3)


CHARACTERS = [
    ("astra-studio-team-lead", collie, (232, 238, 246)),
    ("product-manager", hamster, (253, 240, 222)),
    ("architect", schnauzer, (234, 238, 240)),
    ("ux-researcher", siamese, (228, 242, 244)),
    ("domain-expert", orangutan, (250, 238, 226)),
    ("skill-builder", puppy, (244, 240, 230)),
    ("quality-reviewer", black_cat, (238, 236, 244)),
]


def render(name, painter, background):
    rng = random.Random(name)
    img = Image.new("RGB", (S, S), background)
    painter(ImageDraw.Draw(img), rng)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    return img.resize((512, 512), Image.LANCZOS)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    tiles = []
    for name, painter, background in CHARACTERS:
        img = render(name, painter, background)
        # 256-colour palette: a third of the size, no visible loss on fur texture
        img.quantize(256).save(out / f"{name}.png", optimize=True)
        tiles.append(img)
        print(f"wrote {name}.png")
    team = Image.new("RGB", (512, 512), "white")
    for i, tile in enumerate(tiles[:4]):
        team.paste(tile.resize((256, 256), Image.LANCZOS), ((i % 2) * 256, (i // 2) * 256))
    team.quantize(256).save(out / "team.png", optimize=True)
    print("wrote team.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
