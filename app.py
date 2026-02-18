from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno
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
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
</style>
</head>
<body>
  <h1>Repo-Style QR Art (Centered)</h1>
  <form action="/generate" method="get">
    <label>QR Data</label>
    <input type="text" name="data" required />
    <label>Artwork Image URL (optional)</label>
    <input type="text" name="art" />
    <label>Dot Size (0.55–0.92)</label>
    <input type="text" name="dot" value="0.78" />
    <button type="submit">Generate QR</button>
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

    if not data:
        return "Missing QR data", 400

    # High error correction for safety
    qr = segno.make(data, error='h')
    matrix = matrix_from_segno(qr)
    n = len(matrix)

    box = 16
    quiet = 4
    size = (n + 2 * quiet) * box

    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1 - scale) * box / 2
        draw.ellipse(
            [x0 + pad, y0 + pad, x1 - pad, y1 - pad],
            fill=color
        )

    # Draw QR
    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            if matrix[r][c]:
                draw_dot(x0, y0, x1, y1, dot_scale, (0, 0, 0))
            else:
                draw_dot(x0, y0, x1, y1, dot_scale * 0.85, (255, 255, 255))

    # ---------- CENTER ARTWORK PROPERLY ----------
    if art_url:
        try:
            art = fetch_image(art_url)

            # Resize artwork to fit safely inside QR
            max_art_size = int(size * 0.55)
            art = art.resize((max_art_size, max_art_size), Image.LANCZOS)

            # Calculate exact center
            center_x = size // 2
            center_y = size // 2

            art_x = center_x - (art.width // 2)
            art_y = center_y - (art.height // 2)

            canvas.paste(art, (art_x, art_y), art)

        except Exception:
            pass

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)

    return send_file(out, mimetype="image/png")
