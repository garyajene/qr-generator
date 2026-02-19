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
body { font-family: Arial; margin: 40px; }
label { display:block; margin-top: 14px; font-weight: bold; }
input { width: 600px; padding: 8px; }
button { margin-top: 16px; padding: 10px 16px; }
</style>
</head>
<body>
<h1>Repo-Style QR Art</h1>
<form action="/generate">
<label>QR Data</label>
<input name="data" required>

<label>Artwork URL</label>
<input name="art">

<label>Dot Size (0.55–0.92)</label>
<input name="dot" value="0.78">

<label>Art Wash (0.0–0.6)</label>
<input name="wash" value="0.20">

<label>Suppression (0.0–0.18)</label>
<input name="budget" value="0.08">

<br>
<button type="submit">Generate</button>
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
    wash = clamp(request.args.get("wash"), 0.0, 0.6, 0.20)
    budget = clamp(request.args.get("budget"), 0.0, 0.18, 0.08)

    if not data:
        return "Missing data", 400

    qr = segno.make(data, error="h")
    matrix = [[bool(v) for v in row] for row in qr.]()
