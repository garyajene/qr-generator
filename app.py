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
<title>QR Art (Centered Properly)</title>
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
  <h1>QR Art (Centered Properly)</h1>
  <form action="/generate" method="get">
    <label>QR Data</label>
    <input type="text" name="data" required />

    <label>Artwork Image URL</label>
    <input type="text" name="art" />

    <label>Dot Size (0.55–0.92)</label>
    <input type="text" name="dot" value="0.78" />

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
    border =
