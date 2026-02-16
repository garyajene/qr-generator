from flask import Flask, request, send_file, Response
import qrcode
from PIL import Image, ImageDraw
import requests
from io import BytesIO
import random

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Image Mask Generator</title>
<style>
body { font-family: Arial; margin:40px; }
label { display:block; margin-top:16px; font-weight:700; }
input { width:780px; max-width:95vw; padding:10px; font-size:16px; }
button { margin-top:18px; padding:10px 18px; font-size:18px; }
</style>
</head>
<body>

<h1>QR Repository-Style Image Mask</h1>

<form action="/generate" method="get">
<label>QR Data</label>
<input type="text" name="data" required>

<label>Artwork Image URL</label>
<input type="text" name="art">

<label>Dot Size (0.55–0.85)</label>
<input type="text" name="dot" value="0.70">

<label>Mask Strength (0.05–0.25)</label>
<input type="text" name="mask" value="0.15">

<label>Art Contrast Boost (0.0–1.0)</label>
<input type="text" name="contrast" value="0.25">

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
    r = requests.get(url, timeout=12)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")

def luminance(px):
    r,g,b,a = px
    return 0.299*r + 0.587*g + 0.114*b

def in_finder(r,c,n):
    if r <= 8 and c <= 8: return True
    if r <= 8 and c >= n-9: return True
    if r >= n-9 and c <= 8: return True
    return False

def in_timing_or_format(r,c,n):
    if r == 6 or c == 6: return True
    if r == 8 or c == 8: return True
    return False

@app.route("/")
def home():
    return Response(HTML, mimetype="text/html")

@app.route("/generate")
def generate():

    data = request.args.get("data","").strip()
    art_url = request.args.get("art","").strip()

    if not data:
        return "Missing data", 400

    base_dot = clamp(request.args.get("dot"),0.55,0.85,0.70)
    mask_strength = clamp(request.args.get("mask"),0.05,0.25,0.15)
    contrast_boost = clamp(request.args.get("contrast"),0.0,1.0,0.25)

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        border=0
    )
    qr.add_data(data)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    n = len(matrix)

    box = 16
    quiet = 6
    size = (n+2*quiet)*box

    canvas = Image.new("RGBA",(size,size),(255,255,255,255))
    draw = ImageDraw.Draw(canvas)

    luma_grid = None

    if art_url:
        try:
            art = fetch_image(art_url)
            art = art.resize((n*box,n*box),Image.LANCZOS)

            # Light contrast boost
            overlay = Image.new("RGBA", art.size,(255,255,255,int(255*(0.15))))
            art = Image.alpha_composite(art,overlay)

            canvas.paste(art,(quiet*box,quiet*box),art)

            tiny = art.resize((n,n),Image.BOX)
            px = tiny.load()
            luma_grid = [[luminance(px[x,y]) for x in range(n)] for y in range(n)]
        except:
            pass

    # Count dark modules
    dark_modules = [(r,c) for r in range(n) for c in range(n) if matrix[r][c]]
    max_remove = int(len(dark_modules)*mask_strength)

    removal_candidates = []

    if luma_grid:
        for r,c in dark_modules:
            if in_finder(r,c,n) or in_timing_or_format(r,c,n):
                continue
            brightness = luma_grid[r][c]
            removal_candidates.append((brightness,r,c))

        # Remove only brightest areas (image light zones)
        removal_candidates.sort(reverse=True)
        removed = set((r,c) for _,r,c in removal_candidates[:max_remove])
    else:
        removed = set()

    for r in range(n):
        for c in range(n):

            x0 = (quiet+c)*box
            y0 = (quiet+r)*box
            x1 = x0+box
            y1 = y0+box

            # Structural protection
            if in_finder(r,c,n) or in_timing_or_format(r,c,n):
                if matrix[r][c]:
                    draw.rectangle([x0,y0,x1,y1],fill=(0,0,0,255))
                else:
                    draw.rectangle([x0,y0,x1,y1],fill=(255,255,255,255))
                continue

            if not matrix[r][c]:
                continue

            if (r,c) in removed:
                continue

            # Adaptive dot scaling
            scale = base_dot
            if luma_grid:
                t = luma_grid[r][c]/255.0
                scale = base_dot - (0.15*(t-0.5))
                scale = max(0.55,min(0.85,scale))

            pad = (1-scale)*box/2
            draw.ellipse([x0+pad,y0+pad,x1-pad,y1-pad],fill=(0,0,0,255))

    # Quiet zone
    q = quiet*box
    draw.rectangle([0,0,size,q],fill=(255,255,255,255))
    draw.rectangle([0,size-q,size,size],fill=(255,255,255,255))
    draw.rectangle([0,0,q,size],fill=(255,255,255,255))
    draw.rectangle([size-q,0,size,size],fill=(255,255,255,255))

    out = BytesIO()
    canvas.convert("RGB").save(out,format="PNG")
    out.seek(0)
    return send_file(out,mimetype="image/png",download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=8080)
