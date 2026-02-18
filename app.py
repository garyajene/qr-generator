from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno
import hashlib
import math

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Repo-Style QR Art (Centered)</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  h1 { margin-bottom: 8px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  .row { margin-top: 14px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
  .hint { margin-top: 14px; color: #555; line-height: 1.35; max-width: 900px; }
</style>
</head>
<body>
  <h1>Repo-Style QR Art (Centered)</h1>

  <form action="/generate" method="get">
    <label>QR Data (URL or text)</label>
    <input type="text" name="data" required />

    <label>Artwork Image URL (optional)</label>
    <input type="text" name="art" placeholder="https://example.com/image.png" />

    <label>Dot Size (0.55–0.92). Default 0.78</label>
    <input type="text" name="dot" value="0.78" />

    <label>Artwork Fit (0.50–0.98). Default 0.92 (how big art is inside the QR data area)</label>
    <input type="text" name="fit" value="0.92" />

    <label>Art Wash (0.00–0.80). Default 0.20 (higher = lighter art under dots)</label>
    <input type="text" name="wash" value="0.20" />

    <label>Suppression Budget (0.00–0.10). Default 0.00 (removes some dark dots in SAFE zones)</label>
    <input type="text" name="budget" value="0.00" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>

    <div class="hint">
      This version: (1) centers and fits artwork inside the QR data area (excluding quiet zone),
      (2) draws ONLY dark dots (no white-dot “dust” field), and
      (3) if you use suppression, it only applies in non-critical zones.
    </div>
  </form>
</body>
</html>
"""

def clamp(v, lo, hi, default):
    try:
        x = float(v)
        return max(lo, min(hi, x))
    except:
        return default

def fetch_image(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")

def matrix_from_segno(qr):
    return [[bool(v) for v in row] for row in qr.matrix]

def is_reserved_module(r, c, n):
    # Finder patterns (top-left, top-right, bottom-left) including separators area
    # Typical finder+separator occupies 9x9 in corners in matrix coordinates.
    in_tl = (r <= 8 and c <= 8)
    in_tr = (r <= 8 and c >= n - 9)
    in_bl = (r >= n - 9 and c <= 8)

    # Timing patterns row/col (usually row 6 and col 6)
    in_timing = (r == 6 or c == 6)

    # Format info areas (around finders)
    in_format = (
        (r == 8 and (c <= 8 or c >= n - 9)) or
        (c == 8 and (r <= 8 or r >= n - 9))
    )

    # Dark module (fixed)
    dark_module = (r == (n - 8) and c == 8)

    return in_tl or in_tr or in_bl or in_timing or in_format or dark_module

def should_suppress(r, c, n, budget, seed_bytes):
    # Only suppress if not reserved and budget > 0
    if budget <= 0.0:
        return False
    if is_reserved_module(r, c, n):
        return False

    # Deterministic pseudo-random per (r,c) based on seed
    h = hashlib.sha256(seed_bytes + f"{r},{c}".encode("utf-8")).digest()
    x = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
    return x < budget

@app.route("/")
def home():
    return Response(HTML, mimetype="text/html")

@app.route("/health")
def health():
    return {"ok": True}

@app.route("/generate")
def generate():
    data = request.args.get("data", "").strip()
    art_url = request.args.get("art", "").strip()

    dot_scale = clamp(request.args.get("dot"), 0.55, 0.92, 0.78)
    art_fit  = clamp(request.args.get("fit"), 0.50, 0.98, 0.92)
    wash     = clamp(request.args.get("wash"), 0.00, 0.80, 0.20)
    budget   = clamp(request.args.get("budget"), 0.00, 0.10, 0.00)

    if not data:
        return "Missing QR data", 400

    # High error correction for art overlays
    qr = segno.make(data, error='h')
    matrix = matrix_from_segno(qr)
    n = len(matrix)

    # Rendering settings
    box = 16
    quiet = 6  # quiet zone in modules

    size = (n + 2 * quiet) * box
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # Compute QR data-area pixel box (exclude quiet zone)
    qr_area_px = n * box
    qr_area_x0 = quiet * box
    qr_area_y0 = quiet * box
    qr_area_x1 = qr_area_x0 + qr_area_px
    qr_area_y1 = qr_area_y0 + qr_area_px

    # --- Artwork: center + fit INSIDE QR data area ---
    if art_url:
        try:
            art = fetch_image(art_url)

            # Square-crop to center
            w, h = art.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            art = art.crop((left, top, left + side, top + side))

            # Resize to fit
            target = int(qr_area_px * art_fit)
            if target < 1:
                target = 1
            art = art.resize((target, target), Image.LANCZOS)

            # Wash (blend towards white)
            if wash > 0:
                white = Image.new("RGBA", art.size, (255, 255, 255, 255))
                art = Image.blend(art, white, wash)

            # Center art within the QR data area
            ax = qr_area_x0 + (qr_area_px - target) // 2
            ay = qr_area_y0 + (qr_area_px - target) // 2

            canvas.alpha_composite(art, (ax, ay))
        except Exception as e:
            return f"Failed to fetch/process artwork image: {e}", 400

    # Dot drawing helper
    def draw_dot(x0, y0, scale, color):
        pad = (1.0 - scale) * box / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x0 + box - pad, y0 + box - pad], fill=color)

    # Deterministic seed for suppression
    seed_bytes = hashlib.sha256(data.encode("utf-8")).digest()

    # --- Draw ONLY dark modules (no white-dot dust) ---
    for r in range(n):
        for c in range(n):
            if not matrix[r][c]:
                continue  # leave white background clean

            # Optionally suppress some dark dots in safe zones
            if should_suppress(r, c, n, budget, seed_bytes):
                continue

            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            draw_dot(x0, y0, dot_scale, (0, 0, 0, 255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png")

# IMPORTANT: no app.run()
# Railway uses gunicorn
