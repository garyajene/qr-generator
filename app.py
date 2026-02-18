from flask import Flask, request, send_file
from io import BytesIO
from PIL import Image, ImageDraw
import requests
import segno

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Client-Safe Art QR</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  h1 { margin-bottom: 14px; }
  .badge { display:inline-block; padding:4px 10px; border-radius:999px; background:#eef2ff; margin-left:10px; font-size:14px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  .row { margin-top: 10px; }
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
  .hint { margin-top: 14px; color:#444; max-width: 900px; }
  .small { font-weight: 400; }
</style>
</head>
<body>
  <h1>QR Code Generator <span class="badge">Client-Safe</span></h1>

  <form action="/generate" method="get">
    <label>QR Data <span class="small">(URL or text)</span></label>
    <input type="text" name="data" placeholder="https://..." required />

    <label>Artwork Image URL <span class="small">(optional — your art shows through)</span></label>
    <input type="text" name="art" placeholder="https://.../image.png" />

    <label>Output Size (pixels) <span class="small">(recommended: 1000–1500)</span></label>
    <input type="text" name="size" value="1000" />

    <label>Dot Size <span class="small">(0.55 to 0.92). Bigger = more scannable. Default 0.78</span></label>
    <input type="text" name="dot" value="0.78" />

    <label>Art Wash <span class="small">(0.00 to 0.90). Higher = brighter art = more scannable. Default 0.35</span></label>
    <input type="text" name="wash" value="0.35" />

    <label>Polarity Strength <span class="small">(0.00 to 1.00). Higher = more white dots in dark areas. Default 0.65</span></label>
    <input type="text" name="pol" value="0.65" />

    <div class="row">
      <button type="submit">Generate QR</button>
    </div>

    <div class="hint">
      <b>What to put in Output Size?</b> Use a whole number like <b>1000</b> (best default), <b>1200</b>, or <b>1500</b>.
      Do <b>not</b> enter decimals like 0.60 — that is a ratio, not pixels.
    </div>
  </form>
</body>
</html>
"""

def clamp_float(v, lo, hi, default):
    try:
        x = float(v)
        if x < lo: return lo
        if x > hi: return hi
        return x
    except Exception:
        return default

def clamp_int(v, lo, hi, default):
    try:
        # Accept "1000" or "1000.0"
        x = int(float(v))
        if x < lo: return lo
        if x > hi: return hi
        return x
    except Exception:
        return default

def fetch_image(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")

def cover_resize(img, size):
    w, h = img.size
    scale = max(size / w, size / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - size) // 2
    top = (new_h - size) // 2
    return img.crop((left, top, left + size, top + size))

def luminance(rgb):
    r, g, b = rgb
    return 0.299*r + 0.587*g + 0.114*b

def is_in_finder(r, c, n):
    # Protect full 7x7 finder zones at three corners
    if r <= 6 and c <= 6:
        return True
    if r <= 6 and c >= n - 7:
        return True
    if r >= n - 7 and c <= 6:
        return True
    return False

def is_in_timing(r, c, n):
    # Timing patterns on row 6 and col 6 (excluding finder blocks)
    if r == 6 and 8 <= c <= n - 9:
        return True
    if c == 6 and 8 <= r <= n - 9:
        return True
    return False

@app.route("/")
def home():
    return HTML

@app.route("/generate")
def generate():
    data = (request.args.get("data") or "").strip()
    art_url = (request.args.get("art") or "").strip()

    out_px = clamp_int(request.args.get("size"), 400, 2400, 1000)
    dot = clamp_float(request.args.get("dot"), 0.55, 0.92, 0.78)
    wash = clamp_float(request.args.get("wash"), 0.00, 0.90, 0.35)
    pol = clamp_float(request.args.get("pol"), 0.00, 1.00, 0.65)

    if not data:
        return "Missing QR data", 400

    # Strong correction for art-heavy QRs
    qr = segno.make(data, error="h")
    matrix = list(qr.matrix)
    n = len(matrix)

    # Base art canvas (square, centered, cover-fit)
    if art_url:
        try:
            art = fetch_image(art_url)
            art = cover_resize(art, out_px).convert("RGBA")
        except Exception:
            art = Image.new("RGBA", (out_px, out_px), (255, 255, 255, 255))
    else:
        art = Image.new("RGBA", (out_px, out_px), (255, 255, 255, 255))

    # Apply “wash” = white overlay to brighten art (keeps detail but improves scanning)
    if wash > 0:
        overlay = Image.new("RGBA", (out_px, out_px), (255, 255, 255, int(255 * wash)))
        art = Image.alpha_composite(art, overlay)

    canvas = art.copy()
    draw = ImageDraw.Draw(canvas)

    # Float module size so it fills the canvas EXACTLY (no shrink)
    module = out_px / n

    for r in range(n):
        for c in range(n):
            if not matrix[r][c]:
                continue

            x0 = c * module
            y0 = r * module
            x1 = (c + 1) * module
            y1 = (r + 1) * module

            # Protect critical QR structures as solid black squares
            if is_in_finder(r, c, n) or is_in_timing(r, c, n):
                draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 255))
                continue

            # Sample background luminance at module center
            cx = int((x0 + x1) / 2)
            cy = int((y0 + y1) / 2)
            cx = min(max(cx, 0), out_px - 1)
            cy = min(max(cy, 0), out_px - 1)
            bg = art.getpixel((cx, cy))[:3]
            lum = luminance(bg)

            # Polarity modulation:
            # If background is dark, we allow white dots *some of the time* based on pol.
            # If background is light, dots stay black.
            make_white = (lum < 115) and (pol > 0) and ((1.0 - (lum / 115.0)) < pol)

            color = (255, 255, 255, 255) if make_white else (0, 0, 0, 255)

            # Dot sizing inside the module cell
            pad = (1.0 - dot) * module / 2.0
            draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    # Output PNG
    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png", download_name="qr.png")

@app.route("/health")
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
