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
<title>Centered Art QR</title>
<style>
body { font-family: Arial; margin: 40px; }
label { display:block; margin-top: 16px; font-weight: bold; }
input { width: 700px; max-width: 95vw; padding: 8px; font-size: 16px; }
button { margin-top: 20px; padding: 10px 18px; font-size: 18px; }
</style>
</head>
<body>
<h1>Centered Art QR</h1>
<form action="/generate" method="get">
<label>QR Data</label>
<input type="text" name="data" required />

<label>Artwork Image URL</label>
<input type="text" name="art" />

<label>Output Size (px)</label>
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
    r,g,b = rgb
    return 0.299*r + 0.587*g + 0.114*b

@app.route("/")
def home():
    return HTML

@app.route("/generate")
def generate():
    data = request.args.get("data")
    art_url = request.args.get("art")
    out_px = int(request.args.get("size", 1000))

    if not data:
        return "Missing data", 400

    # Generate QR
    qr = segno.make(data, error="h")
    matrix = list(qr.matrix)
    n = len(matrix)

    # Create square artwork exactly same size as final output
    if art_url:
        art = fetch_image(art_url)
        art = art.resize((out_px, out_px), Image.LANCZOS)
    else:
        art = Image.new("RGB", (out_px, out_px), (255,255,255))

    canvas = art.copy()
    draw = ImageDraw.Draw(canvas)

    # FLOAT module size so QR fills entire canvas exactly
    module_size = out_px / n

    for r in range(n):
        for c in range(n):
            x0 = c * module_size
            y0 = r * module_size
            x1 = (c + 1) * module_size
            y1 = (r + 1) * module_size

            if matrix[r][c]:
                # Sample background center pixel
                cx = int((x0 + x1)/2)
                cy = int((y0 + y1)/2)
                bg = art.getpixel((min(cx,out_px-1), min(cy,out_px-1)))
                lum = luminance(bg)

                # Adaptive polarity
                color = (0,0,0) if lum > 128 else (255,255,255)

                draw.ellipse([x0, y0, x1, y1], fill=color)

    # Force finder patterns solid black
    finder_size = 7
    for fr, fc in [(0,0),(0,n-7),(n-7,0)]:
        for r in range(fr, fr+finder_size):
            for c in range(fc, fc+finder_size):
                x0 = c * module_size
                y0 = r * module_size
                x1 = (c + 1) * module_size
                y1 = (r + 1) * module_size
                draw.rectangle([x0,y0,x1,y1], fill=(0,0,0))

    out = BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
