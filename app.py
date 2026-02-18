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
<title>Repo-Style QR Art</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  h1 { margin-bottom: 8px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  .row { margin-top: 14px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
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
    <label>Art Wash (0.00–0.60)</label>
    <input type="text" name="wash" value="0.20" />
    <label>Suppression Budget (0.00–0.18)</label>
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
    data = request.args.get("data","").strip()
    art_url = request.args.get("art","").strip()
    dot_scale = clamp(request.args.get("dot"), 0.55, 0.92, 0.78)

    if not data:
        return "Missing QR data", 400

    qr = segno.make(data, error='h')
    matrix = matrix_from_segno(qr)
    n = len(matrix)

    box = 16
    quiet = 6

    size = (n + 2*quiet) * box
    canvas = Image.new("RGBA", (size,size), (255,255,255,255))

    draw = ImageDraw.Draw(canvas)

    def draw_dot(x0,y0,x1,y1,scale,color):
        pad = (1-scale)*box/2
        draw.ellipse([x0+pad,y0+pad,x1-pad,y1-pad], fill=color)

    for r in range(n):
        for c in range(n):
            x0 = (quiet+c)*box
            y0 = (quiet+r)*box
            x1 = x0+box
            y1 = y0+box
            if matrix[r][c]:
                draw_dot(x0,y0,x1,y1,dot_scale,(0,0,0))
            else:
                draw_dot(x0,y0,x1,y1,
                         max(0.45,min(0.88,dot_scale*0.88)),
                         (255,255,255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png")

# IMPORTANT: DO NOT use app.run()
# Railway will use gunicorn to start this
