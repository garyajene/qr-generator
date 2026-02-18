from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Repo-Style QR Art</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
</style>
</head>
<body>
  <h1>Repo-Style QR Art</h1>
  <form action="/generate" method="get">
    <label>QR Data</label>
    <input type="text" name="data" required />
    <label>Artwork Image URL</label>
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

    if not data:
        return "Missing data", 400

    # Generate QR with NO internal border
    qr = segno.make(data, error='h', border=0)

    matrix = [[bool(v) for v in row] for row in qr.matrix]
    n = len(matrix)

    box = 16
    quiet = 6  # manual quiet zone (only one)

    qr_pixel_size = n * box
    total_size = (n + quiet * 2) * box

    canvas = Image.new("RGBA", (total_size, total_size), (255, 255, 255, 255))

    # ---- Center Artwork Correctly ----
    if art_url:
        try:
            art = fetch_image(art_url)
            aw, ah = art.size

            scale = min(qr_pixel_size / aw, qr_pixel_size / ah)
            new_w = int(aw * scale)
            new_h = int(ah * scale)

            art_resized = art.resize((new_w, new_h), Image.LANCZOS)

            offset_x = quiet * box + (qr_pixel_size - new_w) // 2
            offset_y = quiet * box + (qr_pixel_size - new_h) // 2

            canvas.paste(art_resized, (offset_x, offset_y), art_resized)
        except:
            pass

    draw = ImageDraw.Draw(canvas)

    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1.0 - scale) * box / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    # Render QR
    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            if matrix[r][c]:
                draw_dot(x0, y0, x1, y1, dot_scale, (0, 0, 0))
            else:
                white_scale = max(0.45, min(0.88, dot_scale * 0.88))
                draw_dot(x0, y0, x1, y1, white_scale, (255, 255, 255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)

    return send_file(out, mimetype="image/png", download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
