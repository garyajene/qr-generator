# app.py

from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno  # used for mask pattern selection + matrix access
import math

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Repo-Style QR Art (Mask Select + Conservative)</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  h1 { margin-bottom: 8px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  .row { margin-top: 14px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
  .hint { margin-top: 16px; color: #555; line-height: 1.35; }
  .small { font-weight: 400; }
</style>
</head>
<body>
  <h1>Repo-Style QR Art (Scannable)</h1>

  <form action="/generate" method="get">
    <label>QR Data <span class="small">(URL or text)</span></label>
    <input type="text" name="data" placeholder="https://..." required />

    <label>Artwork Image URL <span class="small">(optional)</span></label>
    <input type="text" name="art" placeholder="https://.../image.png" />

    <label>Dot Size <span class="small">(0.55–0.92). Default 0.78</span></label>
    <input type="text" name="dot" value="0.78" />

    <label>Art Wash <span class="small">(0.00–0.60). Default 0.20 — higher = safer scan</span></label>
    <input type="text" name="wash" value="0.20" />

    <label>Suppression Budget <span class="small">(0.00–0.18). Default 0.08 — % of dark modules removed</span></label>
    <input type="text" name="budget" value="0.08" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>

    <div class="hint">
      This version selects the best QR mask (0–7) for your image, then conservatively suppresses
      a small percentage of dark modules in bright image areas, and renders <b>both black and white dots</b>.
    </div>
  </form>
</body>
</html>
"""

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

def matrix_from_segno(qr) -> list[list[bool]]:
    m = []
    for row in qr.matrix:
        m.append([bool(v) for v in row])
    return m

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

# --- ARTWORK CENTERING HELPERS (ONLY CHANGE AREA) ---

def pad_to_square_rgba(img: Image.Image) -> Image.Image:
    """Pad to a square canvas (transparent), keeping the original centered."""
    w, h = img.size
    s = max(w, h)
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ox = (s - w) // 2
    oy = (s - h) // 2
    out.paste(img, (ox, oy), img)
    return out

def fit_art_to_module_area_centered(art_img: Image.Image, module_px: int) -> Image.Image:
    """
    Make artwork square (by padding), then resize to exactly the module area size.
    This preserves the QR matrix size and keeps art mathematically centered.
    """
    art_sq = pad_to_square_rgba(art_img.convert("RGBA"))
    return art_sq.resize((module_px, module_px), Image.LANCZOS)

# --- END ARTWORK CENTERING HELPERS ---

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

    dot_scale = clamp(request.args.get("dot"), 0.55, 0.92, 0.78)
    wash = clamp(request.args.get("wash"), 0.00, 0.60, 0.20)
    budget = clamp(request.args.get("budget"), 0.00, 0.18, 0.08)

    if not data:
        return "Missing QR data", 400

    best_qr = None
    best_matrix = None
    best_version = None
    best_mask = None

    box = 16
    quiet = 6

    art_ok = False
    luma = None
    art_img = None

    if art_url:
        try:
            art_img = fetch_image(art_url)
            art_ok = True
        except Exception:
            art_ok = False
            art_img = None

    if not art_ok:
        qr0 = segno.make(data, error='h')
        best_qr = qr0
        best_matrix = matrix_from_segno(qr0)
        best_version = int(qr0.version)
        best_mask = None
    else:
        tmp = segno.make(data, error='h', mask=0)
        n = tmp.symbol_size()[0]
        version = int(tmp.version)

        # --- ONLY CHANGE: center-safe artwork sizing/placement prep ---
        module_px = n * box
        art_resized = fit_art_to_module_area_centered(art_img, module_px)
        # --- END ONLY CHANGE ---

        if wash > 0:
            overlay = Image.new("RGBA", art_resized.size, (255, 255, 255, int(255 * wash)))
            art_resized = Image.alpha_composite(art_resized, overlay)

        tiny = art_resized.resize((n, n), Image.BOX).convert("RGBA")
        px = tiny.load()
        luma = [[float(luminance_rgba(px[c, r])) for c in range(n)] for r in range(n)]

        best_score = -1e18
        for mask in range(8):
            qr_i = segno.make(data, error='h', mask=mask)
            m_i = matrix_from_segno(qr_i)
            v_i = int(qr_i.version)
            sc = score_mask(m_i, luma, v_i)
            if sc > best_score:
                best_score = sc
                best_qr = qr_i
                best_matrix = m_i
                best_version = v_i
                best_mask = mask

        art_img = art_resized

    matrix = best_matrix
    n = len(matrix)
    version = best_version

    size = (n + 2 * quiet) * box
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    # Place artwork underneath (ONLY in module area; quiet zone untouched)
    if art_ok and art_img is not None:
        module_origin_px = quiet * box
        module_px = n * box

        # Art is already exactly module_px x module_px, but we keep centered math explicit.
        x_art = module_origin_px + (module_px - art_img.size[0]) // 2
        y_art = module_origin_px + (module_px - art_img.size[1]) // 2

        canvas.paste(art_img, (x_art, y_art), art_img)

    draw = ImageDraw.Draw(canvas)

    removed = set()
    if art_ok and luma is not None and budget > 0:
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
        k = int(dark_count * budget)
        k = min(k, 2500)
        for i in range(k):
            _, rr, cc = candidates[i]
            removed.add((rr, cc))

    def draw_dot(x0, y0, x1, y1, scale, rgb):
        pad = (1.0 - scale) * box / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=rgb)

    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            if is_protected(r, c, n, version):
                if matrix[r][c]:
                    draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))
                else:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))
                continue

            if matrix[r][c]:
                if (r, c) in removed:
                    continue
                draw_dot(x0, y0, x1, y1, dot_scale, (0, 0, 0))
            else:
                white_scale = max(0.45, min(0.88, dot_scale * 0.88))
                draw_dot(x0, y0, x1, y1, white_scale, (255, 255, 255))

    qpx = quiet * box
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
