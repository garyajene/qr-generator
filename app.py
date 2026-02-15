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
    <title>QR Code Generator (Client-Safe Art QR)</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 40px; }
      h1 { margin-bottom: 20px; }
      label { display:block; margin-top: 16px; font-weight: 700; }
      input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
      .row { margin-top: 10px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
      button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
      .hint { margin-top: 16px; color: #555; }
      .small { font-weight: 400; }
      code { background:#f4f4f4; padding:2px 6px; border-radius:6px; }
      .pill { display:inline-block; padding:2px 10px; border-radius:999px; background:#eef; font-size:12px; margin-left:10px;}
    </style>
  </head>
  <body>
    <h1>QR Code Generator <span class="pill">Client-Safe</span></h1>

    <form action="/generate" method="get">
      <label>QR Data <span class="small">(URL or text)</span></label>
      <input type="text" name="data" placeholder="https://..." required />

      <label>Artwork Image URL <span class="small">(optional — your artwork shows through safely)</span></label>
      <input type="text" name="art" placeholder="https://.../image.png" />

      <label>Dot Size <span class="small">(safe range 0.60 to 0.90). Default 0.78</span></label>
      <input type="text" name="dot" value="0.78" />

      <label>Art Wash <span class="small">(safe min 0.55). Default 0.65 — higher = more scannable</span></label>
      <input type="text" name="wash" value="0.65" />

      <label>Light Module Protect <span class="small">(0.20 to 0.75). Default 0.40 — higher = more scannable</span></label>
      <input type="text" name="light" value="0.40" />

      <div class="row">
        <button type="submit">Generate QR</button>
        <a href="/health" style="margin-top:18px;">health</a>
      </div>

      <div class="hint">
        This generator is tuned for <b>client safety</b>: it enforces a stronger quiet zone, protects structural QR areas,
        and applies a light overlay on “white” modules so artwork can’t destroy contrast.
        <br/><br/>
        Tip: For printing, make the QR at least <b>1.25–1.5 inches</b> wide if possible.
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
    # Format information areas (around finders).
    # These modules are critical and should be kept solid.
    # Approximate protection for typical QR placement.
    if r == 8 and (c <= 8 or c >= n - 8):
        return True
    if c == 8 and (r <= 8 or r >= n - 8):
        return True
    return False

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

    # Client-safe constraints (no user can break scanning)
    dot_scale = clamp(request.args.get("dot"), 0.60, 0.90, 0.78)  # locked safe range
    wash = clamp(request.args.get("wash"), 0.00, 0.95, 0.65)
    wash = max(wash, 0.55)  # enforce safe minimum
    light_protect = clamp(request.args.get("light"), 0.20, 0.75, 0.40)  # overlay for light modules

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
    box = 16        # pixel size per module (bigger = better scanning)
    quiet = 6       # stronger quiet zone for art QRs (client-safe)
    size = (n + 2 * quiet) * box

    # Base canvas: white
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    # If artwork provided, place it under the QR area (excluding quiet zone)
    if art_url:
        try:
            art = fetch_image(art_url).convert("RGBA")
            art = art.resize((n * box, n * box), Image.LANCZOS)

            # Global wash (white overlay) to keep art from going too dark
            if wash > 0:
                overlay = Image.new("RGBA", art.size, (255, 255, 255, int(255 * wash)))
                art = Image.alpha_composite(art, overlay)

            canvas.paste(art, (quiet * box, quiet * box), art)
        except Exception:
            # If art fails to load, proceed with plain white background
            pass

    draw = ImageDraw.Draw(canvas)

    # Draw QR modules with client-safe rules:
    # - Structural areas are solid squares
    # - Light modules get a soft white overlay when art is present (prevents contrast collapse)
    # - Dark modules are black dots (or solid squares for protected areas)
    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            in_protected = (
                is_in_finder(r, c, n)
                or is_in_timing(r, c, n)
                or is_in_format_info(r, c, n)
            )

            if not matrix[r][c]:
                # Light module: preserve art but enforce scannable contrast
                if art_url:
                    overlay = Image.new("RGBA", (box, box), (255, 255, 255, int(255 * light_protect)))
                    canvas.paste(overlay, (x0, y0), overlay)
                continue

            # Dark module
            if in_protected:
                draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 255))
                continue

            # Dots for normal modules
            pad = (1.0 - dot_scale) * box / 2.0  # float pad for cleaner circles
            draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=(0, 0, 0, 255))

    # Force quiet zone to be pure white (critical for scanning)
    qpx = quiet * box
    draw.rectangle([0, 0, size, qpx], fill=(255, 255, 255, 255))                 # top
    draw.rectangle([0, size - qpx, size, size], fill=(255, 255, 255, 255))       # bottom
    draw.rectangle([0, 0, qpx, size], fill=(255, 255, 255, 255))                 # left
    draw.rectangle([size - qpx, 0, size, size], fill=(255, 255, 255, 255))       # right

    out = BytesIO()
    # Keep alpha during processing, but PNG output for QR typically fine in RGB.
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png", download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
