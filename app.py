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

def fetch_image(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")

def matrix_from_segno(qr):
    return [[bool(v) for v in row] for row in qr.matrix]

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

@app.route("/")
def home():
    return Response(HTML, mimetype="text/html")

@app.route("/generate")
def generate():
    data = (request.args.get("data") or "").strip()
    art_url = (request.args.get("art") or "").strip()

    if not data:
        return "Missing data", 400

    qr = segno.make(data, error="h")
    matrix = matrix_from_segno(qr)
    version = int(qr.version)
    n = len(matrix)

    box = 16
    quiet = 6
    size = (n + 2 * quiet) * box

    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    # ---- KEEP EXISTING ARTWORK SCALING BEHAVIOR ----
    if art_url:
        try:
            art = fetch_image(art_url)
            art_resized = art.resize((n * box, n * box), Image.LANCZOS)
            canvas.paste(art_resized, (quiet * box, quiet * box), art_resized)
        except:
            pass
    # -------------------------------------------------

    draw = ImageDraw.Draw(canvas)

    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1 - scale) * box / 2
        draw.ellipse(
            [x0 + pad, y0 + pad, x1 - pad, y1 - pad],
            fill=color
        )

    dot_scale = 0.78

    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            # 🔒 HARD INVARIANT: Protected zones render as squares ONLY
            if is_protected(r, c, n, version):
                if matrix[r][c]:
                    draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))
                else:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))
                continue

            # Non-protected modules render as dots
            if matrix[r][c]:
                draw_dot(x0, y0, x1, y1, dot_scale, (0, 0, 0))
            else:
                draw_dot(x0, y0, x1, y1, dot_scale * 0.85, (255, 255, 255))

    # 🔒 HARD INVARIANT: Quiet zone enforcement
    qpx = quiet * box
    draw.rectangle([0, 0, size, qpx], fill=(255, 255, 255))
    draw.rectangle([0, size - qpx, size, size], fill=(255, 255, 255))
    draw.rectangle([0, 0, qpx, size], fill=(255, 255, 255))
    draw.rectangle([size - qpx, 0, size, size], fill=(255, 255, 255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
