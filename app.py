# app.py
from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno

app = Flask(__name__)

# Two-field UI (data + art) only
HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Generator</title>
<style>
  body { font-family: Georgia, serif; margin: 40px; }
  h1 { font-size: 64px; margin: 0 0 22px 0; }
  label { display:block; margin-top: 18px; font-weight: 700; font-size: 28px; }
  input { width: 520px; max-width: 95vw; padding: 10px; font-size: 20px; }
  button { margin-top: 18px; padding: 8px 16px; font-size: 22px; cursor:pointer; }
</style>
</head>
<body>
  <h1>QR Generator</h1>

  <form action="/generate" method="get">
    <label>QR Data</label>
    <input type="text" name="data" placeholder="https://..." required />

    <label>Artwork URL <span style="font-weight:400;">(optional)</span></label>
    <input type="text" name="art" placeholder="https://.../image.png" />

    <button type="submit">Generate</button>
  </form>
</body>
</html>
"""

# HARD-INVARIANTS SAFE DEFAULTS (fixed; no UI controls)
ERROR_LEVEL = "h"      # maximum error correction
BOX = 16               # module pixel size (QR structure unchanged)
QUIET = 6              # quiet zone thickness in modules (pure white)
DOT_SCALE = 0.48       # AGGRESSIVE (your request)
WHITE_SCALE_FACTOR = 0.88  # white dots slightly smaller than black dots
WASH = 0.20            # gentle wash for scan safety (applies only to artwork layer)
BUDGET = 0.08          # conservative suppression of non-protected dark modules

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

def qr_size_from_version(version: int) -> int:
    return 17 + 4 * version

def alignment_centers(version: int):
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
    # 9x9 including separator
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
    if not centers:
        return False
    n = qr_size_from_version(version)
    for cy in centers:
        for cx in centers:
            # skip overlaps with finder zones
            if (cx == 6 and cy == 6) or (cx == 6 and cy == n - 7) or (cx == n - 7 and cy == 6):
                continue
            if abs(r - cy) <= 2 and abs(c - cx) <= 2:
                return True
    return False

def is_protected(r, c, n, version):
    # HARD INVARIANT: protected zones are untouchable
    return (
        in_finder_or_separator(r, c, n)
        or in_timing(r, c, n)
        or in_format_info(r, c, n)
        or in_alignment(r, c, version)
    )

def matrix_from_segno(qr) -> list[list[bool]]:
    m = []
    for row in qr.matrix:
        m.append([bool(v) for v in row])
    return m

def score_mask(matrix, luma, version):
    # prefer dark modules on dark image pixels
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

@app.route("/health")
def health():
    return {"ok": True}

@app.route("/generate")
def generate():
    data = (request.args.get("data") or "").strip()
    art_url = (request.args.get("art") or "").strip()

    if not data:
        return "Missing QR data", 400

    art_ok = False
    art_img = None
    luma = None

    if art_url:
        try:
            art_img = fetch_image(art_url)
            art_ok = True
        except Exception:
            art_ok = False
            art_img = None

    # Mask selection stays (0..7 scoring) when art present
    if not art_ok:
        qr_best = segno.make(data, error=ERROR_LEVEL)
        matrix = matrix_from_segno(qr_best)
        version = int(qr_best.version)
        art_resized = None
    else:
        tmp = segno.make(data, error=ERROR_LEVEL, mask=0)
        n_tmp = tmp.symbol_size()[0]
        version = int(tmp.version)

        # HARD INVARIANT: QR matrix size is untouchable.
        # We only resize artwork to QR pixel area (n*BOX) and CENTER it.
        # Center-crop the artwork to square, then scale to fit the QR pixel area.
        art = art_img.convert("RGBA")
        w, h = art.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        art = art.crop((left, top, left + side, top + side))

        target_px = n_tmp * BOX
        art_resized = art.resize((target_px, target_px), Image.LANCZOS)

        # Wash only affects artwork layer (never protected zones, never matrix)
        if WASH > 0:
            overlay = Image.new("RGBA", art_resized.size, (255, 255, 255, int(255 * WASH)))
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
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # Artwork: centered exactly under module area only (not quiet zone)
    if art_ok and art_resized is not None:
        ox = QUIET * BOX
        oy = QUIET * BOX
        canvas.paste(art_resized, (ox, oy), art_resized)

    # Suppression stays: only non-protected DARK modules, brightest areas first
    removed = set()
    if art_ok and luma is not None and BUDGET > 0:
        candidates = []
        dark_count = 0
        for r in range(n):
            for c in range(n):
                if not matrix[r][c]:
                    continue
                if is_protected(r, c, n, version):
                    continue
                dark_count += 1
                candidates.append((luma[r][c], r, c))
        candidates.sort(reverse=True, key=lambda x: x[0])
        k = int(dark_count * BUDGET)
        k = min(k, 2500)
        for i in range(k):
            _, rr, cc = candidates[i]
            removed.add((rr, cc))

    # Dot helper (no square backgrounds)
    def draw_dot(x0, y0, x1, y1, scale, rgb):
        pad = (1.0 - scale) * BOX / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=rgb)

    # Render
    for r in range(n):
        for c in range(n):
            x0 = (QUIET + c) * BOX
            y0 = (QUIET + r) * BOX
            x1 = x0 + BOX
            y1 = y0 + BOX

            # Protected zones are untouchable: solid squares only
            if is_protected(r, c, n, version):
                if matrix[r][c]:
                    draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))
                else:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))
                continue

            # Non-protected: circles
            if matrix[r][c]:
                if (r, c) in removed:
                    continue
                draw_dot(x0, y0, x1, y1, DOT_SCALE, (0, 0, 0))
            else:
                white_scale = max(0.35, min(0.85, DOT_SCALE * WHITE_SCALE_FACTOR))
                draw_dot(x0, y0, x1, y1, white_scale, (255, 255, 255))

    # Quiet zone hard white and untouched
    qpx = QUIET * BOX
    draw.rectangle([0, 0, size, qpx], fill=(255, 255, 255))
    draw.rectangle([0, size - qpx, size, size], fill=(255, 255, 255))
    draw.rectangle([0, 0, qpx, size], fill=(255, 255, 255))
    draw.rectangle([size - qpx, 0, size, size], fill=(255, 255, 255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png", download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
