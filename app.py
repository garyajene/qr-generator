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
    matrix = [[bool(v) for v in row] for row in qr.matrix]

    n = len(matrix)
    box = 16
    quiet = 6

    size = (n + quiet * 2) * box
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # === ART CENTERING FIX ===
    art_img = None
    if art_url:
        try:
            resp = requests.get(art_url, timeout=10)
            art_img = Image.open(BytesIO(resp.content)).convert("RGBA")
        except:
            art_img = None

    if art_img:
        qr_pixels = n * box

        # Resize art to EXACT QR pixel size
        art_img = art_img.resize((qr_pixels, qr_pixels), Image.LANCZOS)

        if wash > 0:
            overlay = Image.new("RGBA", art_img.size, (255,255,255,int(255*wash)))
            art_img = Image.alpha_composite(art_img, overlay)

        # Center precisely inside module area
        offset = quiet * box
        canvas.paste(art_img, (offset, offset), art_img)

    # Draw modules
    for r in range(n):
        for c in range(n):

            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            if matrix[r][c]:
                pad = (1 - dot_scale) * box / 2
                draw.ellipse(
                    [x0+pad, y0+pad, x1-pad, y1-pad],
                    fill=(0,0,0)
                )
            else:
                white_scale = dot_scale * 0.88
                pad = (1 - white_scale) * box / 2
                draw.ellipse(
                    [x0+pad, y0+pad, x1-pad, y1-pad],
                    fill=(255,255,255)
                )

    # Quiet zone repaint
    qpx = quiet * box
    draw.rectangle([0,0,size,qpx], fill=(255,255,255))
    draw.rectangle([0,size-qpx,size,size], fill=(255,255,255))
    draw.rectangle([0,0,qpx,size], fill=(255,255,255))
    draw.rectangle([size-qpx,0,size,size], fill=(255,255,255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    out.seek(0)

    return send_file(out, mimetype="image/png")
