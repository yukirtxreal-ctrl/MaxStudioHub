"""Generate the Max Studio Hub app icon (assets/app.ico): a BRIGHT, SHINY light-blue
12-ray starburst with a soft glow, on a fully TRANSPARENT background. Matches the
in-app logo. Run: python assets/make_icon.py  (needs Pillow)."""
import math
import os
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")

S = 256
SS = S * 4  # supersample for smooth edges
BLUE = (95, 210, 255, 255)


def burst(size, color, r_out_f=0.42, w_f=0.05):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = cy = size // 2
    r_out = size * r_out_f
    w = size * w_f
    for k in range(12):
        a = math.radians(k * 30)
        dx, dy = math.cos(a), math.sin(a)
        p2 = (cx + r_out * dx, cy + r_out * dy)
        d.line([(cx, cy), p2], fill=color, width=int(w))
        d.ellipse([p2[0] - w / 2, p2[1] - w / 2, p2[0] + w / 2, p2[1] + w / 2], fill=color)
    d.ellipse([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2], fill=color)
    return im

core = burst(SS, BLUE)
glow = burst(SS, (110, 220, 255, 210)).filter(ImageFilter.GaussianBlur(SS * 0.022))

out = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
out = Image.alpha_composite(out, glow)        # shiny halo
out = Image.alpha_composite(out, glow)        # …intensified
out = Image.alpha_composite(out, core)        # crisp rays on top

out = out.resize((S, S), Image.LANCZOS)
out.save(OUT, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
out.save(OUT.replace(".ico", ".png"))
print("wrote", OUT)
