from flask import Flask, request, send_file
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
    <title>QR Code Generator (Repository-Style Art Mask)</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 40px; }
      h1 { margin-bottom: 20px; }
      label { display:block; margin-top: 16px; font-weight: 700; }
      input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
      .row { margin-top: 10px; }
      button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
      .hint { margin-top: 16px; color: #555; }
      .small { font-weight: 400; }
    </style>
  </head>
  <body>
    <h1>QR Code Generator (Repository-Style Art Mask)</h1>

    <form action="/generate" method="get">
      <label>QR Data <span class="small">(URL or text)</span></label>
      <input type="text" name="data" placeholder="https://..." required />

      <label>Artwork Image URL <span class="small">(optional — shows art behind the QR)</span></label>
      <input type="text" name="art" placeholder="https://.../image.png" />

      <label>Dot Size <span class="small">(0.35 to 0.95). Default 0.78</span></label>
      <input type="text" name="dot" value="0.78" />

      <label>Art Wash <span class="small">(0.00 to 0.95). Default 0.65 — higher = more scannable</span></label>
      <input type="text" name="wash" value="0.65" />

      <div class="row">
        <button type="submit">Generate QR</button>
      </div>

      <div class="hint">
        Tip: Use a direct image URL (public). Transparent PNGs are fine.
        If scanning is weak, increase <b>Art Wash</b> or slightly increase the quiet zone by using a larger print size.
      </div>
    </form>
  </body>
</html>
"""

def clamp(v, lo, hi, default):
    try:
        x = float(v)
        if x < lo: return lo
        if x > hi: return hi
        return x
    except Exception:
        return default

def is_in_finder(r, c, n):
    # Finder patterns are 7x7 blocks at:
    # (0,0), (0,n-7), (n-7,0)
    # We protect the full 7x7 area.
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

def fetch_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=12)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content))
    return img

@app.route("/")
def home():
    return HTML

@app.route("/generate")
def generate():
    data = (request.args.get("data") or "").strip()
    art_url = (request.args.get("art") or "").strip()
    dot_scale = clamp(request.args.get("dot"), 0.35, 0.95, 0.78)
    wash = clamp(request.args.get("wash"), 0.00, 0.95, 0.65)

    if not data:
        return "Missing QR data", 400

    # Strongest correction for artistic QRs
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=0,  # we'll draw our own quiet zone
    )
    qr.add_data(data)
    qr.make(fit=True)

    matrix = qr.get_matrix()  # modules only, no border
    n = len(matrix)

    # Rendering parameters
    box = 16                 # pixel size per module (bigger = better scanning)
    quiet = 4                # modules of quiet zone (pure white)
    size = (n + 2 * quiet) * box

    # Base canvas: white
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    # If artwork provided, place it under the QR area (excluding quiet zone)
    if art_url:
        try:
            art = fetch_image(art_url).convert("RGBA")
            art = art.resize((n * box, n * box), Image.LANCZOS)

            # Optional: lightly brighten the art overall (helps scanning)
            # We'll apply a "wash" on top of the art (white overlay)
            if wash > 0:
                overlay = Image.new("RGBA", art.size, (255, 255, 255, int(255 * wash)))
                art = Image.alpha_composite(art, overlay)

            canvas.paste(art, (quiet * box, quiet * box), art)
        except Exception:
            # If art fails to load, just proceed with plain white background
            pass

    draw = ImageDraw.Draw(canvas)

    # Draw QR modules
    # Key: we do NOT remove modules. Dark modules stay dark.
    # Art is visible because the background is art, and light modules remain mostly light.
    for r in range(n):
        for c in range(n):
            if not matrix[r][c]:
                continue  # light module -> leave background as-is (art/white)

            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            # Protect scanner-critical parts as solid squares
            if is_in_finder(r, c, n) or is_in_timing(r, c, n):
                draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 255))
                continue

            # Dots for normal modules
            # Dot size controls how much art you can see while keeping black presence.
            pad = int((1.0 - dot_scale) * box / 2.0)
            draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=(0, 0, 0, 255))

    # Force quiet zone to be pure white (critical for scanning)
    # Top band
    draw.rectangle([0, 0, size, quiet * box], fill=(255, 255, 255, 255))
    # Bottom band
    draw.rectangle([0, size - quiet * box, size, size], fill=(255, 255, 255, 255))
    # Left band
    draw.rectangle([0, 0, quiet * box, size], fill=(255, 255, 255, 255))
    # Right band
    draw.rectangle([size - quiet * box, 0, size, size], fill=(255, 255, 255, 255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png", download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
