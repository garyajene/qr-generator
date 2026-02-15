from flask import Flask, request, send_file, Response
import qrcode
from PIL import Image, ImageDraw
import requests
from io import BytesIO

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>QR Code Generator (Client-Safe + Polarity Modulation)</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 40px; }
      h1 { margin-bottom: 20px; }
      label { display:block; margin-top: 16px; font-weight: 700; }
      input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
      .row { margin-top: 10px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
      button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
      .hint { margin-top: 16px; color: #555; }
      .small { font-weight: 400; }
      .pill { display:inline-block; padding:2px 10px; border-radius:999px; background:#eef; font-size:12px; margin-left:10px;}
      code { background:#f4f4f4; padding:2px 6px; border-radius:6px; }
    </style>
  </head>
  <body>
    <h1>QR Code Generator <span class="pill">Client-Safe + Polarity</span></h1>

    <form action="/generate" method="get">
      <label>QR Data <span class="small">(URL or text)</span></label>
      <input type="text" name="data" placeholder="https://..." required />

      <label>Artwork Image URL <span class="small">(optional — art shows through)</span></label>
      <input type="text" name="art" placeholder="https://.../image.png" />

      <label>Dot Size <span class="small">(safe range 0.62 to 0.90). Default 0.78</span></label>
      <input type="text" name="dot" value="0.78" />

      <label>Art Wash <span class="small">(0.20 to 0.92). Default 0.52 — higher = safer, lower = more art</span></label>
      <input type="text" name="wash" value="0.52" />

      <label>Light Module Protect <span class="small">(0.10 to 0.70). Default 0.22 — higher = safer</span></label>
      <input type="text" name="light" value="0.22" />

      <label>Polarity Strength <span class="small">(0.00 to 1.00). Default 0.55 — higher = more “repo look”</span></label>
      <input type="text" name="pol" value="0.55" />

      <div class="row">
        <button type="submit">Generate QR</button>
        <a href="/health" style="margin-top:18px;">health</a>
      </div>

      <div class="hint">
        <b>Polarity modulation is enabled</b> (toned-down + guarded). It uses a “white center + black ring” inside dark modules
        on very dark artwork areas, so you get that repo-style look without destroying scan logic.
        <br/><br/>
        Tip: For printing, aim for <b>1.25–1.5 inches</b> wide minimum.
      </div>
    </form>
  </body>
</html>
"""

def clamp(v, lo, hi, default):
    try:
        x = float(v)
        if x < lo:
            return lo
        if x > hi:
            return hi
        return x
    except Exception:
        return default

def fetch_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=12)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content))
    return img

def luminance_rgba(px):
    # px is (r,g,b,a)
    r, g, b, a = px
    if a == 0:
        return 255.0
    # If semi-transparent, blend toward white (safe assumption for scannability)
    alpha = a / 255.0
    r = r * alpha + 255 * (1 - alpha)
    g = g * alpha + 255 * (1 - alpha)
    b = b * alpha + 255 * (1 - alpha)
    return 0.299 * r + 0.587 * g + 0.114 * b

def is_in_finder(r, c, n):
    # Finder patterns are 7x7 blocks at:
    # (0,0), (0,n-7), (n-7,0)
    if r <= 6 and c <= 6:
        return True
    if r <= 6 and c >= n - 7:
        return True
    if r >= n - 7 and c <= 6:
        return True
    return False

def is_in_timing(r, c, n):
    # Timing patterns are row 6 and col 6 (excluding finder areas)
    if r == 6 and 8 <= c <= n - 9:
        return True
    if c == 6 and 8 <= r <= n - 9:
        return True
    return False

def is_in_format_info(r, c, n):
    # Format information areas (critical)
    if r == 8 and (c <= 8 or c >= n - 8):
        return True
    if c == 8 and (r <= 8 or r >= n - 8):
        return True
    return False

def safe_luma_grid(art_rgba: Image.Image, n: int):
    """
    Build a luminance grid (n x n) by resizing artwork to n x n.
    Each cell is average-ish luminance via nearest/box sampling (fast + stable).
    """
    tiny = art_rgba.resize((n, n), Image.BOX).convert("RGBA")
    pix = tiny.load()
    grid = [[255.0] * n for _ in range(n)]
    for y in range(n):
        for x in range(n):
            grid[y][x] = float(luminance_rgba(pix[x, y]))
    return grid

@app.route("/")
def home():
    return Response(HTML, mimetype="text/html")

@app.route("/health")
def health():
    return {"ok": True}

@app.route("/generate")
def generate():
    data = (request.args.get("data") or "").strip()
    art_url = (request.args.get("art") or "").strip()

    # Safe ranges (still adjustable, but guarded)
    base_dot = clamp(request.args.get("dot"), 0.62, 0.90, 0.78)
    wash = clamp(request.args.get("wash"), 0.20, 0.92, 0.52)          # lower than before so art can show
    light_protect = clamp(request.args.get("light"), 0.10, 0.70, 0.22) # lower than before so art shows more
    pol = clamp(request.args.get("pol"), 0.00, 1.00, 0.55)             # polarity strength (toned + guarded)

    if not data:
        return "Missing QR data", 400

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=0,
    )
    qr.add_data(data)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    n = len(matrix)

    # Rendering parameters
    box = 16
    quiet = 6
    size = (n + 2 * quiet) * box

    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    luma = None  # n x n luminance grid if art present

    if art_url:
        try:
            art = fetch_image(art_url).convert("RGBA")
            art = art.resize((n * box, n * box), Image.LANCZOS)

            # Global wash (kept, but reduced) to prevent “dark art kills contrast”
            if wash > 0:
                overlay = Image.new("RGBA", art.size, (255, 255, 255, int(255 * wash)))
                art = Image.alpha_composite(art, overlay)

            # Paste art under the QR modules area
            canvas.paste(art, (quiet * box, quiet * box), art)

            # Luminance grid for polarity modulation + adaptive sizing
            luma = safe_luma_grid(art, n)
        except Exception:
            luma = None

    draw = ImageDraw.Draw(canvas)

    # Polarity thresholds (guardrails)
    # Only apply “white-center ring” on very dark areas; otherwise keep classic black dots.
    DARK_BG = 85.0

    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            protected = (
                is_in_finder(r, c, n)
                or is_in_timing(r, c, n)
                or is_in_format_info(r, c, n)
            )

            bg_l = 255.0
            if luma is not None:
                bg_l = luma[r][c]

            if not matrix[r][c]:
                # Light module: keep it light, but let art show.
                # If art is dark, add a *small* white overlay (safety).
                if luma is not None:
                    # More overlay only when background is darker
                    # (toned-down; avoids washing out everything)
                    strength = light_protect * max(0.0, (140.0 - bg_l) / 140.0)
                    if strength > 0:
                        overlay = Image.new("RGBA", (box, box), (255, 255, 255, int(255 * strength)))
                        canvas.paste(overlay, (x0, y0), overlay)
                continue

            # Dark module
            if protected:
                draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 255))
                continue

            # Adaptive dot sizing (subtle, not extreme):
            # On darker background, slightly increase dot size; on lighter, slightly reduce.
            # Keeps scan reliability while improving “repo look”.
            if luma is None:
                dot_scale = base_dot
            else:
                t = (255.0 - bg_l) / 255.0  # dark=1, light=0
                dot_scale = base_dot + (0.06 * t) - (0.03 * (1 - t))
                dot_scale = max(0.62, min(0.90, dot_scale))

            pad = (1.0 - dot_scale) * box / 2.0

            # Polarity modulation (TONED + GUARDED):
            # If background is very dark, draw a black ring with a white center *inside a dark module*.
            # This gives the “white dot in dark areas” vibe, while the black ring keeps the module logically dark.
            if luma is not None and bg_l < DARK_BG and pol > 0:
                # Outer black ellipse
                draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=(0, 0, 0, 255))

                # White center size controlled by pol (and background darkness)
                # Keep it conservative so the module still reads as dark overall.
                darkness = max(0.0, min(1.0, (DARK_BG - bg_l) / DARK_BG))  # 0..1
                center_strength = pol * (0.45 + 0.35 * darkness)           # 0..~0.8
                center_strength = max(0.0, min(0.80, center_strength))

                # Center pad: bigger value = smaller white center
                # We want a modest white center, not full inversion.
                extra = (box * 0.10) + (box * 0.22 * (1 - center_strength))
                cx0 = x0 + pad + extra
                cy0 = y0 + pad + extra
                cx1 = x1 - pad - extra
                cy1 = y1 - pad - extra

                # If center collapses, skip
                if cx1 > cx0 and cy1 > cy0:
                    draw.ellipse([cx0, cy0, cx1, cy1], fill=(255, 255, 255, 255))
            else:
                # Classic black dot
                draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=(0, 0, 0, 255))

    # Force quiet zone to pure white (critical)
    qpx = quiet * box
    draw.rectangle([0, 0, size, qpx], fill=(255, 255, 255, 255))
    draw.rectangle([0, size - qpx, size, size], fill=(255, 255, 255, 255))
    draw.rectangle([0, 0, qpx, size], fill=(255, 255, 255, 255))
    draw.rectangle([size - qpx, 0, size, size], fill=(255, 255, 255, 255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png", download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
