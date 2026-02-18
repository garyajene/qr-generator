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
<title>Repo-Style QR Art (Centered + Balanced)</title>
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
  <h1>Repo-Style QR Art (Auto-Centered + Scannable)</h1>

  <form action="/generate" method="get">
    <label>QR Data <span class="small">(URL or text)</span></label>
    <input type="text" name="data" placeholder="https://..." required />

    <label>Artwork Image URL <span class="small">(optional)</span></label>
    <input type="text" name="art" placeholder="https://.../image.png" />

    <label>Base Dot Size <span class="small">(0.60–0.92). Default 0.78</span></label>
    <input type="text" name="dot" value="0.78" />

    <label>Art Wash <span class="small">(0.00–0.65). Default 0.22 — higher = safer scan</span></label>
    <input type="text" name="wash" value="0.22" />

    <label>Modulation Strength <span class="small">(0.00–0.40). Default 0.22 — higher = more “image”</span></label>
    <input type="text" name="mod" value="0.22" />

    <label>Balanced Removal <span class="small">(0.00–0.08). Default 0.02 — tiny, quadrant-balanced</span></label>
    <input type="text" name="rm" value="0.02" />

    <label>Auto-Center Crop <span class="small">(0 or 1). Default 1 — fixes “pull/zoom”</span></label>
    <input type="text" name="center" value="1" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>

    <div class="hint">
      This version auto-centers the artwork by cropping to content (alpha if available; otherwise non-white),
      then padding to a square before resizing. This removes the “pull/zoom” illusion.
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

def quadrant_index(r, c, n):
    top = 0 if r < n // 2 else 1
    left = 0 if c < n // 2 else 1
    return top * 2 + left

def autocenter_crop_rgba(img: Image.Image) -> Image.Image:
    """
    Crops to content, then pads to square, centered.
    - Uses alpha if present
    - Otherwise treats near-white pixels as background
    """
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()

    # Determine if alpha is meaningful
    has_alpha_content = False
    for y in (0, h//2, h-1):
        for x in (0, w//2, w-1):
            if px[x, y][3] < 250:
                has_alpha_content = True
                break
        if has_alpha_content:
            break

    x0, y0 = w, h
    x1, y1 = 0, 0
    found = False

    if has_alpha_content:
        # Content = alpha > threshold
        for y in range(h):
            for x in range(w):
                if px[x, y][3] > 20:
                    found = True
                    if x < x0: x0 = x
                    if y < y0: y0 = y
                    if x > x1: x1 = x
                    if y > y1: y1 = y
    else:
        # Content = not near-white
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a > 10 and (r < 245 or g < 245 or b < 245):
                    found = True
                    if x < x0: x0 = x
                    if y < y0: y0 = y
                    if x > x1: x1 = x
                    if y > y1: y1 = y

    if not found:
        return img

    # Add a small margin so we don't crop too tight
    margin = int(0.03 * max(w, h))
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(w - 1, x1 + margin)
    y1 = min(h - 1, y1 + margin)

    cropped = img.crop((x0, y0, x1 + 1, y1 + 1))

    # Pad to square
    cw, ch = cropped.size
    side = max(cw, ch)
    out = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    ox = (side - cw) // 2
    oy = (side - ch) // 2
    out.paste(cropped, (ox, oy), cropped)
    return out

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

    base_dot = clamp(request.args.get("dot"), 0.60, 0.92, 0.78)
    wash = clamp(request.args.get("wash"), 0.00, 0.65, 0.22)
    mod = clamp(request.args.get("mod"), 0.00, 0.40, 0.22)
    rm = clamp(request.args.get("rm"), 0.00, 0.08, 0.02)
    center = int(clamp(request.args.get("center"), 0, 1, 1))

    if not data:
        return "Missing QR data", 400

    box = 16
    quiet = 6

    art_ok = False
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
        matrix = matrix_from_segno(qr0)
        version = int(qr0.version)
        luma = None
        art_resized = None
    else:
        tmp = segno.make(data, error='h', mask=0)
        n = tmp.symbol_size()[0]
        version = int(tmp.version)

        if center == 1 and art_img is not None:
            art_img = autocenter_crop_rgba(art_img)

        art_resized = art_img.convert("RGBA").resize((n * box, n * box), Image.LANCZOS)

        if wash > 0:
            overlay = Image.new("RGBA", art_resized.size, (255, 255, 255, int(255 * wash)))
            art_resized = Image.alpha_composite(art_resized, overlay)

        tiny = art_resized.resize((n, n), Image.BOX).convert("RGBA")
        px = tiny.load()
        luma = [[float(luminance_rgba(px[c, r])) for c in range(n)] for r in range(n)]

        best_score = -1e18
        best_m = None
        best_v = None

        for mask in range(8):
            qr_i = segno.make(data, error='h', mask=mask)
            m_i = matrix_from_segno(qr_i)
            v_i = int(qr_i.version)
            sc = score_mask(m_i, luma, v_i)
            if sc > best_score:
                best_score = sc
                best_m = m_i
                best_v = v_i

        matrix = best_m
        version = best_v

    n = len(matrix)
    size = (n + 2 * quiet) * box

    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    if art_ok and art_resized is not None:
        canvas.paste(art_resized, (quiet * box, quiet * box), art_resized)

    removed = set()
    if art_ok and luma is not None and rm > 0:
        candidates_by_q = {0: [], 1: [], 2: [], 3: []}
        dark_counts_q = {0: 0, 1: 0, 2: 0, 3: 0}

        for r in range(n):
            for c in range(n):
                if not matrix[r][c]:
                    continue
                if is_protected(r, c, n, version):
                    continue
                q = quadrant_index(r, c, n)
                dark_counts_q[q] += 1
                candidates_by_q[q].append((luma[r][c], r, c))

        for q in range(4):
            candidates_by_q[q].sort(reverse=True, key=lambda x: x[0])
            kq = int(dark_counts_q[q] * rm)
            kq = min(kq, 800)
            for i in range(kq):
                _, rr, cc = candidates_by_q[q][i]
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

                scale = base_dot
                if art_ok and luma is not None:
                    t = luma[r][c] / 255.0
                    scale = base_dot + mod * (0.50 - t)
                    scale = max(0.55, min(0.92, scale))

                draw_dot(x0, y0, x1, y1, scale, (0, 0, 0))
            else:
                wscale = max(0.40, min(0.85, base_dot * 0.88))
                draw_dot(x0, y0, x1, y1, wscale, (255, 255, 255))

    # Quiet zone pure white
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
