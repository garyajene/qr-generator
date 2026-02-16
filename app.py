from flask import Flask, request, send_file, Response
import qrcode
from PIL import Image, ImageDraw
import requests
from io import BytesIO

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Code Generator – Polarity + Density</title>
<style>
body { font-family: Arial; margin:40px; }
label { display:block; margin-top:16px; font-weight:700; }
input { width:780px; max-width:95vw; padding:10px; font-size:16px; }
button { margin-top:18px; padding:10px 18px; font-size:18px; }
</style>
</head>
<body>

<h1>QR Code Generator – Artistic Polarity</h1>

<form action="/generate" method="get">
<label>QR Data</label>
<input type="text" name="data" required>

<label>Artwork Image URL</label>
<input type="text" name="art">

<label>Base Dot Size (0.60–0.90)</label>
<input type="text" name="dot" value="0.78">

<label>Polarity Strength (0–1)</label>
<input type="text" name="pol" value="0.65">

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

def luminance(px):
    r, g, b, a = px
    return 0.299*r + 0.587*g + 0.114*b

def fetch_image(url):
    resp = requests.get(url, timeout=12)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGBA")

def in_finder(r, c, n):
    if r <= 6 and c <= 6: return True
    if r <= 6 and c >= n-7: return True
    if r >= n-7 and c <= 6: return True
    return False

def in_timing_or_format(r, c, n):
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

    base_dot = clamp(request.args.get("dot"), 0.60, 0.90, 0.78)
    pol_strength = clamp(request.args.get("pol"), 0.0, 1.0, 0.65)

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
    size = (n + 2*quiet) * box

    canvas = Image.new("RGBA",(size,size),(255,255,255,255))

    luma_grid = None

    if art_url:
        try:
            art = fetch_image(art_url)
            art = art.resize((n*box,n*box),Image.LANCZOS)
            canvas.paste(art,(quiet*box,quiet*box),art)

            tiny = art.resize((n,n),Image.BOX)
            px = tiny.load()
            luma_grid = [[luminance(px[x,y]) for x in range(n)] for y in range(n)]
        except:
            pass

    draw = ImageDraw.Draw(canvas)

    for r in range(n):
        for c in range(n):

            x0 = (quiet+c)*box
            y0 = (quiet+r)*box
            x1 = x0 + box
            y1 = y0 + box

            # 🔒 FULL FINDER PATTERN RESTORATION
            if in_finder(r,c,n):
                if matrix[r][c]:
                    draw.rectangle([x0,y0,x1,y1],fill=(0,0,0,255))
                else:
                    draw.rectangle([x0,y0,x1,y1],fill=(255,255,255,255))
                continue

            # 🔒 Timing + format protected
            if in_timing_or_format(r,c,n):
                if matrix[r][c]:
                    draw.rectangle([x0,y0,x1,y1],fill=(0,0,0,255))
                else:
                    draw.rectangle([x0,y0,x1,y1],fill=(255,255,255,255))
                continue

            # Light modules
            if not matrix[r][c]:
                continue

            bg = 255
            if luma_grid:
                bg = luma_grid[r][c]

            t = bg / 255.0

            dot_scale = base_dot + (0.12 * (0.5 - t))
            dot_scale = max(0.60,min(0.90,dot_scale))

            pad = (1-dot_scale)*box/2

            # Polarity modulation (safe version)
            if luma_grid and t < 0.45:
                draw.ellipse([x0+pad,y0+pad,x1-pad,y1-pad],fill=(0,0,0,255))
                inner = pad + box*(0.18*pol_strength)
                draw.ellipse([x0+inner,y0+inner,x1-inner,y1-inner],fill=(255,255,255,255))
            else:
                draw.ellipse([x0+pad,y0+pad,x1-pad,y1-pad],fill=(0,0,0,255))

    # Quiet zone enforcement
    qpx = quiet*box
    draw.rectangle([0,0,size,qpx],fill=(255,255,255,255))
    draw.rectangle([0,size-qpx,size,size],fill=(255,255,255,255))
    draw.rectangle([0,0,qpx,size],fill=(255,255,255,255))
    draw.rectangle([size-qpx,0,size,size],fill=(255,255,255,255))

    out = BytesIO()
    canvas.convert("RGB").save(out,format="PNG")
    out.seek(0)

    return send_file(out,mimetype="image/png",download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=8080)
