"""Generate the Max Studio Hub app icons: a BRIGHT, SHINY light-blue 12-ray
starburst with a soft glow, on a fully TRANSPARENT background. Matches the
in-app logo. Run: python assets/make_icon.py  (needs Pillow).

Outputs:
  assets/app.ico   Windows executable / shortcut icon
  assets/app.png   preview (256px, used in the README)
  assets/app.icns  macOS .app bundle icon
"""
import math
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app.ico")


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


def render(final_size):
    """Render the starburst at final_size (supersampled 4x for smooth edges)."""
    ss = final_size * 4
    blue = (95, 210, 255, 255)
    core = burst(ss, blue)
    glow = burst(ss, (110, 220, 255, 210)).filter(ImageFilter.GaussianBlur(ss * 0.022))
    out = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    out = Image.alpha_composite(out, glow)        # shiny halo
    out = Image.alpha_composite(out, glow)        # …intensified
    out = Image.alpha_composite(out, core)        # crisp rays on top
    return out.resize((final_size, final_size), Image.LANCZOS)


if __name__ == "__main__":
    im256 = render(256)
    im256.save(OUT, format="ICO",
               sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    im256.save(OUT.replace(".ico", ".png"))
    print("wrote", OUT)

    # macOS bundle icon — .icns wants sizes up to 1024 (512@2x)
    im1024 = render(1024)
    icns = os.path.join(HERE, "app.icns")
    im1024.save(icns, format="ICNS")
    print("wrote", icns)
