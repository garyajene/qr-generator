from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno
import math

app = Flask(__name__)

# -----------------------------
# (HTML unchanged — omitted here for clarity, keep yours exactly as-is)
# -----------------------------

# KEEP YOUR ORIGINAL HTML BLOCK HERE EXACTLY AS YOU POSTED IT


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
    m = []
    for row in qr.matrix:
        m.append([bool(v) for v in row])
    return m


@app.route("/")
def home():
    return Response(HTML, mimetype="text/html")


@app.route("/health")
def health():
    return {"ok": True}


@app.route("/generate")
def generate():

    data = (request.args.get("data") or "").s
