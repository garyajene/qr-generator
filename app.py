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
<title>QR Generator</title>
</head>
<body>
<h1>QR Generator</h1>
<form action="/generate" method="get">
  <label>QR Data</label><br>
  <input type="text" name="data" required><br><br>

  <label>Artwork URL (optional)</label><br>
  <input type="text" name="art"><br><br>

  <button type="submit">Generate</button>
</form>
</body>
</html>
"""

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6

DOT_SCALE = 0.48
WHITE_SCALE_FACTOR = 0.88

WASH = 0.20
BUDGET = 0.08

def fetch_image(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")

def luminance_rgba(px):
    r, g, b, a = px
    if a == 0:
        return 255.0
    alpha = a / 255.0
    r = r * alpha + 255 * (1 - alpha)
    g = g * alpha + 255 * (1 - alpha)
    b = b * alpha + 255 * (1 - alpha)
    return 0.299 * r + 0.587 * g + 0.114 * b

def qr_size_from_version(version):
    return 17 + 4 * version

def alignment_centers(version):
    if version <= 1:
        return []
    n = qr_size_from_version(version)
    num = version // 7 + 2
    if num == 2:
        return [6, n - 7]
    step = (n - 13) // (num - 1)
    if step % 2 == 1:
        step += 1
    centers = [6]
    last = n - 7
    for i in range(num - 2):
        centers.append(last - (num - 3 - i) * step)
    centers.append(last)
    return centers

def in_finder_or_separator(r, c, n):
    if r <= 8 and c <= 8:
        return True
    if r <= 8 and c >= n - 9:
        return True
    if r >= n - 9 and c <= 8:
        return True
    return False

def in_timing(r, c, n):
    if r == 6 and 8 <= c <= n - 9:
        return True
    if c == 6 and 8 <= r <= n - 9:
        return True
    return False

def in_format_info(r, c, n):
    if r == 8 and (c <= 8 or c >= n - 9):
        return True
    if c == 8 and (r <= 8 or r >= n - 9):
        return True
    return False

def in_alignment(r, c, version):
    if version <= 1:
        return False
    centers = alignment_centers(version)
    n = qr_size_from_version(version)
    for cy in centers:
        for cx in centers:
            if (cx == 6 and cy == 6) or (cx == 6 and cy == n - 7) or (cx == n - 7 and cy == 6):
                continue
            if abs(r - cy) <= 2 and abs(c - cx) <= 2:
                return True
    return False

def is_protected(r, c, n, version):
    return (
        in_finder_or_separator(r, c, n)
        or in_timing(r, c, n)
        or in_format_info(r, c, n)
        or in_alignment(r, c, version)
    )

def matrix_from_segno(qr):
    return [[bool(v) for v in row] for row in qr.matrix]

def score_mask(matrix, luma, version):
    n = len(matrix)
    s = 0.0
    count = 0
    for r in range(n):
        for c in range(n):
            if is_protected(r, c, n, version):
                continue
            y = luma[r][c]
            if matrix[r][c]:
                s += (255.0 - y)
            else:
                s += 0.35 * y
            count += 1
    return s / max(1, count)

@app.route("/")
def home():
    return Response(HTML, mimetype="text/html")

@app.route("/generate")
def generate():
    data = (request.args.get("data") or "").strip()
    art_url = (request.args.get("art") or "").strip()

    if not data:
        return "Missing data", 400

    art_ok = False
    art_img = None
    luma = None

    if art_url:
        try:
            art_img = fetch_image(art_url)
            art_ok = True
        except:
            art_ok = False

    if not art_ok:
        qr_best = segno.make(data, error=ERROR_LEVEL)
        matrix = matrix_from_segno(qr_best)
        version = int(qr_best.version)
        art_resized = None
    else:
        tmp = segno.make(data, error=ERROR_LEVEL, mask=0)
        n_tmp = tmp.symbol_size()[0]
        version = int(tmp.version)

        # 🔥 NO CROPPING — direct resize (restores centering)
        art_resized = art_img.resize((n_tmp * BOX, n_tmp * BOX), Image.LANCZOS)

        if WASH > 0:
            overlay = Image.new("RGBA", art_resized.size, (255,255,255,int(255*WASH)))
            art_resized = Image.alpha_composite(art_resized, overlay)

        tiny = art_resized.resize((n_tmp, n_tmp), Image.BOX).convert("RGBA")
        px = tiny.load()
        luma = [[float(luminance_rgba(px[c, r])) for c in range(n_tmp)] for r in range(n_tmp)]

        best_score = -1e18
        qr_best = None
        best_matrix = None

        for mask in range(8):
            qr_i = segno.make(data, error=ERROR_LEVEL, mask=mask)
            m_i = matrix_from_segno(qr_i)
            v_i = int(qr_i.version)
            sc = score_mask(m_i, luma, v_i)
            if sc > best_score:
                best_score = sc
                qr_best = qr_i
                best_matrix = m_i
                version = v_i

        matrix = best_matrix

    n = len(matrix)
    size = (n + 2 * QUIET) * BOX
    canvas = Image.new("RGBA", (size, size), (255,255,255,255))
    draw = ImageDraw.Draw(canvas)

    if art_ok and art_resized:
        ox = QUIET * BOX
        oy = QUIET * BOX
        canvas.paste(art_resized, (ox, oy), art_resized)

    removed = set()
    if art_ok and luma and BUDGET > 0:
        candidates = []
        dark_count = 0
        for r in range(n):
            for c in range(n):
                if not matrix[r][c]:
                    continue
                if is_protected(r,c,n,version):
                    continue
                dark_count += 1
                candidates.append((luma[r][c], r, c))
        candidates.sort(reverse=True)
        k = min(int(dark_count * BUDGET), 2500)
        for i in range(k):
            _, rr, cc = candidates[i]
            removed.add((rr,cc))

    def draw_dot(x0,y0,x1,y1,scale,color):
        pad = (1-scale)*BOX/2
        draw.ellipse([x0+pad,y0+pad,x1-pad,y1-pad], fill=color)

    for r in range(n):
        for c in range(n):
            x0 = (QUIET + c) * BOX
            y0 = (QUIET + r) * BOX
            x1 = x0 + BOX
            y1 = y0 + BOX

            if is_protected(r,c,n,version):
                if matrix[r][c]:
                    draw.rectangle([x0,y0,x1,y1], fill=(0,0,0))
                else:
                    draw.rectangle([x0,y0,x1,y1], fill=(255,255,255))
                continue

            if matrix[r][c]:
                if (r,c) in removed:
                    continue
                draw_dot(x0,y0,x1,y1,DOT_SCALE,(0,0,0))
            else:
                white_scale = max(0.35, min(0.85, DOT_SCALE*WHITE_SCALE_FACTOR))
                draw_dot(x0,y0,x1,y1,white_scale,(255,255,255))

    qpx = QUIET * BOX
    draw.rectangle([0,0,size,qpx], fill=(255,255,255))
    draw.rectangle([0,size-qpx,size,size], fill=(255,255,255))
    draw.rectangle([0,0,qpx,size], fill=(255,255,255))
    draw.rectangle([size-qpx,0,size,size], fill=(255,255,255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
