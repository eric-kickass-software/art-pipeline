"""Palette ramps: 5 colours per ramp, darkest first, hue-shifted from a base colour."""
import colorsys
import json
from pathlib import Path

import numpy as np


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(c):
    return "#" + "".join(f"{int(v):02x}" for v in c)


def _shift_hue(h, target, amount):
    d = ((target - h + 0.5) % 1.0) - 0.5
    return (h + d * amount) % 1.0


def ramp_from_base(base, size=5):
    """Pixel-art ramp: shadows drift towards blue/purple and gain saturation, highlights drift
    towards yellow and lose a little saturation. The base colour sits in the middle."""
    r, g, b = (v / 255 for v in hex_rgb(base))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    mid = size // 2
    out = []
    for i in range(size):
        k = (i - mid) / max(mid, 1)  # -1 .. 1
        if k < 0:
            hh = _shift_hue(h, 0.72, 0.12 * -k)
            ss = min(1.0, s * (1 + 0.25 * -k) + 0.05 * -k)
            vv = v * (1 + 0.55 * k)
        else:
            hh = _shift_hue(h, 0.15, 0.08 * k)
            ss = s * (1 - 0.3 * k)
            vv = min(1.0, v + (1 - v) * 0.55 * k + 0.08 * k)
        out.append(tuple(round(c * 255) for c in colorsys.hsv_to_rgb(hh, ss, vv)))
    return out


class Palettes:
    def __init__(self, path):
        self.doc = json.loads(Path(path).read_text(encoding="utf-8"))
        self.size = self.doc.get("rampSize", 5)
        self.outline = hex_rgb(self.doc.get("outline", "#1b1421"))
        self.ramps = {}
        for group, entries in self.doc["groups"].items():
            for name, e in entries.items():
                cols = [hex_rgb(c) for c in e["colors"]] if "colors" in e else ramp_from_base(e["base"], self.size)
                self.ramps[f"{group}.{name}"] = np.array(cols, np.uint8)

    @staticmethod
    def group_for(channel):
        return "cloth" if channel.startswith("cloth") else channel

    def options(self, channel):
        g = self.group_for(channel) + "."
        return sorted(k for k in self.ramps if k.startswith(g))

    def export(self, path):
        """Resolved ramps (explicit hex colours) for the game: Assets/Art/Characters/palettes.json."""
        data = {"rampSize": self.size, "outline": rgb_hex(self.outline),
                "ramps": {k: [rgb_hex(c) for c in v] for k, v in sorted(self.ramps.items())}}
        Path(path).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
        return path

    def swatch(self, path, scale=12):
        from PIL import Image, ImageDraw
        names = sorted(self.ramps)
        img = Image.new("RGB", (self.size * scale + 150, len(names) * scale), (24, 20, 28))
        d = ImageDraw.Draw(img)
        for y, n in enumerate(names):
            for x, c in enumerate(self.ramps[n]):
                d.rectangle([x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1], fill=tuple(int(v) for v in c))
            d.text((self.size * scale + 6, y * scale), n, fill=(220, 220, 220))
        img.save(path)
