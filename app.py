from flask import Flask, request, send_file
from io import BytesIO
from PIL import Image, ImageDraw
import requests
import segno
import math

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Production QR Generator</title>
<style>
body { font-family: Arial; margin: 40px; }
label { display:block; margin-top: 16px; font-weight: bold; }
input { width: 700px; max-width: 95vw; padding: 8px; font-size: 16px; }
button { margin-top: 20px; padding: 10px 18px; font-size: 18px; }
</style>
</head>
<body>
<h1>Production QR Generator</h1>
<form action="/generate" method="get">
<label>QR Data</label>
<input type="text" name="data" required />

<label>Artwork Image URL</label>
<input type="text" name="art" />

<label>Output Size (pixels)</label>
<input type="text" name="size" value="1000" />

<button type="submit">Generate</button>
</form>
</body>
</html>
"""

def fetch_image(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGB")

def luminance(rgb):
    r, g, b = rgb
    return 0.299*r + 0.587*g + 0.114*b

def cover_resize(img, size):
    w, h = img.size
    scale = max(size / w, size / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - size) // 2
    top = (new_h - size) // 2
    return img.crop((left, top, left + size, top + size))

@app.route("/")
def home():
    return HTML

@app.route("/generate")
def generate():
    data = request.args.get("data", "").strip()
    art_url = request.args.get("art", "").strip()
    size_param = request.args.get("size", "1000").strip()

    if not data:
        return "Missing QR data", 400

    try:
        out_px = int(float(size_param))
        if out_px < 300:
            out_px = 1000
    except:
        out_px = 1000

    qr = segno.make(data, error="h")
    matrix = list(qr.matrix)
    n = len(matrix)

    if art_url:
        try:
            art = fetch_image(art_url)
            art = cover_resize(art, out_px)
        except:
            art = Image.new("RGB", (out_px, out_px), (255,255,255))
    else:
        art = Image.new("RGB", (out_px, out_px), (255,255,255))

    canvas = art.copy()
    draw = ImageDraw.Draw(canvas)

    module_size = out_px / n

    for r in range(n):
        for c in range(n):
            if not matrix[r][c]:
                continue

            x0 = c * module_size
            y0 = r * module_size
            x1 = (c + 1) * module_size
            y1 = (r + 1) * module_size

            cx = int((x0 + x1) / 2)
            cy = int((y0 + y1) / 2)
            cx = min(max(cx, 0), out_px - 1)
            cy = min(max(cy, 0), out_px - 1)

            bg = art.getpixel((cx, cy))
            lum = luminance(bg)

            color = (0,0,0) if lum > 140 else (255,255,255)

            draw.ellipse([x0, y0, x1, y1], fill=color)

    finder_coords = [(0,0),(0,n-7),(n-7,0)]
    for fr, fc in finder_coords:
        for r in range(fr, fr+7):
            for c in range(fc, fc+7):
                x0 = c * module_size
                y0 = r * module_size
                x1 = (c + 1) * module_size
                y1 = (r + 1) * module_size
                draw.rectangle([x0,y0,x1,y1], fill=(0,0,0))

    output = BytesIO()
    canvas.save(output, format="PNG")
    output.seek(0)

    return send_file(output, mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
