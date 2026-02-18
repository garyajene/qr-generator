from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno
import math
import os

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Repo-Style QR Art (Mask Select + Conservative)</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  h1 { margin-bottom: 8px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  .row { margin-top: 14px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
  .hint { margin-top: 16px; color: #555; line-height: 1.35; }
  .small { font-weight: 400; }
</style>
</head>
<body>
  <h1>Repo-Style QR Art (Scannable)</h1>

  <form action="/generate" method="get">
    <label>QR Data <span class="small">(URL or text)</span></label>
    <input type="text" name="data" required />

    <label>Artwork Image URL <span class="small">(optional)</span></label>
    <input type="text" name="art" />

    <label>Dot Size <span class="small">(0.55–0.92). Default 0.78</span></label>
    <input type="text" name="dot" value="0.78" />

    <label>Art Wash <span class="small">(0.00–0.60). Default 0.20</span></label>
    <input type="text" name="wash" value="0.20" />

    <label>Suppression Budget <span class="small">(0.00–0.18). Default 0.08</span></label>
    <input type="text" name="budget" value="0.08" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>
  </form>
</body>
</html>
"""

def clamp(v, lo, hi, default):
    try:
        x = float(v)
        return max(lo, min(hi, x))
    except Exception:
        return default

def fetch_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGBA")

def luminance_rgba(px):
    r, g, b, a = px
    if a == 0:
        return 255.0
    alpha = a / 255.0
    r = r * alpha + 255 * (1 - alpha)
    g = g * alpha + 255 * (1 - alpha)
    b = b * alpha + 255 * (1 - alpha)
    return 0.299 * r + 0.587 * g + 0.114 * b

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
    data = (request.args.get("data") or "").strip()
    art_url = (request.args.get("art") or "").strip()

    dot_scale = clamp(request.args.get("dot"), 0.55, 0.92, 0.78)
    wash = clamp(request.args.get("wash"), 0.00, 0.60, 0.20)
    budget = clamp(request.args.get("budget"), 0.00, 0.18, 0.08)

    if not data:
        return "Missing QR data", 400

    # Generate base QR
    base_qr = segno.make(data, error='h')
    matrix = matrix_from_segno(base_qr)
    n = len(matrix)

    box = 16
    quiet = 6

    art_ok = False
    art_img = None
    luma = None

    if art_url:
        try:
            art_raw = fetch_image(art_url)
            art_ok = True

            # Resize using TRUE matrix dimension
            target_px = n * box
            art_img = art_raw.resize((target_px, target_px), Image.LANCZOS)

            if wash > 0:
                overlay = Image.new("RGBA", art_img.size,
                                    (255, 255, 255, int(255 * wash)))
                art_img = Image.alpha_composite(art_img, overlay)

            tiny = art_img.resize((n, n), Image.BOX)
            px = tiny.load()
            luma = [[luminance_rgba(px[c, r]) for c in range(n)] for r in range(n)]

        except Exception:
            art_ok = False

    size = (n + 2 * quiet) * box
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    if art_ok and art_img:
        canvas.paste(art_img, (quiet * box, quiet * box), art_img)

    draw = ImageDraw.Draw(canvas)

    removed = set()
    if art_ok and luma and budget > 0:
        candidates = []
        dark_count = 0
        for r in range(n):
            for c in range(n):
                if matrix[r][c]:
                    dark_count += 1
                    candidates.append((luma[r][c], r, c))
        candidates.sort(reverse=True, key=lambda x: x[0])
        k = int(dark_count * budget)
        for i in range(min(k, len(candidates))):
            _, rr, cc = candidates[i]
            removed.add((rr, cc))

    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1.0 - scale) * box / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            if matrix[r][c]:
                if (r, c) not in removed:
                    draw_dot(x0, y0, x1, y1, dot_scale, (0, 0, 0))
            else:
                draw_dot(
                    x0, y0, x1, y1,
                    max(0.45, min(0.88, dot_scale * 0.88)),
                    (255, 255, 255)
                )

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png", download_name="qr.png")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
