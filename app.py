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
<title>Client-Safe QR Art (Fit + Centered)</title>
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
  <h1>QR Code Generator (Client-Safe • Fit + Centered)</h1>

  <form action="/generate" method="get">
    <label>QR Data <span class="small">(URL or text)</span></label>
    <input type="text" name="data" placeholder="https://..." required />

    <label>Artwork Image URL <span class="small">(optional — auto-fit, no crop)</span></label>
    <input type="text" name="art" placeholder="https://.../image.png" />

    <label>Base Dot Size <span class="small">(0.60–0.92). Default 0.80</span></label>
    <input type="text" name="dot" value="0.80" />

    <label>Light Dot Size <span class="small">(0.35–0.85). Default 0.55</span></label>
    <input type="text" name="lightdot" value="0.55" />

    <label>Art Wash <span class="small">(0.00–0.70). Default 0.15 — higher = safer scan</span></label>
    <input type="text" name="wash" value="0.15" />

    <label>Dot Modulation <span class="small">(0.00–0.30). Default 0.16 — higher = more “image”</span></label>
    <input type="text" name="mod" value="0.16" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>

    <div class="hint">
      This version is built for client uploads: it <b>fits artwork automatically</b> (no cropping),
      pads to square, and pins it to the same pixel grid as the QR. No shifting. No removal.
      It renders <b>black + white dots</b> (repo look) while keeping QR meaning intact.
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

def matrix_from_segno(qr):
    return [[bool(v) for v in row] for row in qr.matrix]

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
    # 9x9 finder+separator
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

def contain_and_pad_square(img: Image.Image, side: int, pad_color=(255, 255, 255, 255)) -> Image.Image:
    """
    Fit whole image into a square (no cropping), keep aspect ratio, center it.
    This is what clients expect.
    """
    img = img.convert("RGBA")
    w, h = img.size
    if w == 0 or h == 0:
        return Image.new("RGBA", (side, side), pad_color)

    scale = min(side / w, side / h)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)

    canvas = Image.new("RGBA", (side, side), pad_color)
    ox = (side - nw) // 2
    oy = (side - nh) // 2
    canvas.paste(resized, (ox, oy), resized)
    return canvas

def score_mask(matrix, luma, version):
    # Prefer dark modules over darker pixels, light modules over lighter pixels.
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

    base_dot = clamp(request.args.get("dot"), 0.60, 0.92, 0.80)
    light_dot = clamp(request.args.get("lightdot"), 0.35, 0.85, 0.55)
    wash = clamp(request.args.get("wash"), 0.00, 0.70, 0.15)
    mod = clamp(request.args.get("mod"), 0.00, 0.30, 0.16)

    if not data:
        return "Missing QR data", 400

    # Build QR with strongest correction; pick best mask when art exists
    tmp = segno.make(data, error='h', mask=0)
    version = int(tmp.version)
    n = tmp.symbol_size()[0]

    box = 16
    quiet = 6
    size = (n + 2 * quiet) * box

    # Background canvas
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # Prepare module-area artwork
    art_ok = False
    luma = None
    art_area = None

    if art_url:
        try:
            art_img = fetch_image(art_url)

            # FIT WHOLE IMAGE (no crop), pad to square, and center
            module_side_px = n * box
            art_area = contain_and_pad_square(art_img, module_side_px, pad_color=(255, 255, 255, 255))

            # Wash (brighten) for scan reliability, but not destructive
            if wash > 0:
                overlay = Image.new("RGBA", art_area.size, (255, 255, 255, int(255 * wash)))
                art_area = Image.alpha_composite(art_area, overlay)

            canvas.paste(art_area, (quiet * box, quiet * box), art_area)

            # Luma per module
            tiny = art_area.resize((n, n), Image.BOX).convert("RGBA")
            px = tiny.load()
            luma = [[float(luminance_rgba(px[c, r])) for c in range(n)] for r in range(n)]

            art_ok = True
        except Exception:
            art_ok = False
            luma = None
            art_area = None

    # Choose best mask (0-7) based on art (if present)
    best_score = -1e18
    best_matrix = None
    best_version = version

    for mask in range(8):
        qr_i = segno.make(data, error='h', mask=mask)
        m_i = matrix_from_segno(qr_i)
        v_i = int(qr_i.version)

        if not art_ok:
            # no art: any mask is fine; take first
            best_matrix = m_i
            best_version = v_i
            break

        sc = score_mask(m_i, luma, v_i)
        if sc > best_score:
            best_score = sc
            best_matrix = m_i
            best_version = v_i

    matrix = best_matrix
    version = best_version
    n = len(matrix)

    def draw_dot(x0, y0, x1, y1, scale, rgb):
        pad = (1.0 - scale) * box / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=rgb)

    # Render modules
    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            if is_protected(r, c, n, version):
                # keep structure crisp and boring (that’s why it scans)
                if matrix[r][c]:
                    draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 255))
                else:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255, 255))
                continue

            if matrix[r][c]:
                # DARK module: always dark (do not flip QR meaning)
                scale = base_dot
                if art_ok and luma is not None:
                    t = luma[r][c] / 255.0  # 0 dark, 1 bright
                    # shrink on bright areas to reveal art; slightly grow on dark
                    scale = base_dot + mod * (0.50 - t)
                    scale = max(0.55, min(0.92, scale))
                draw_dot(x0, y0, x1, y1, scale, (0, 0, 0, 255))
            else:
                # LIGHT module: render as WHITE dot (repo look) but smaller
                draw_dot(x0, y0, x1, y1, light_dot, (255, 255, 255, 255))

    # Force quiet zone pure white
    qpx = quiet * box
    draw.rectangle([0, 0, size, qpx], fill=(255, 255, 255, 255))
    draw.rectangle([0, size - qpx, size, size], fill=(255, 255, 255, 255))
    draw.rectangle([0, 0, qpx, size], fill=(255, 255, 255, 255))
    draw.rectangle([size - qpx, 0, size, size], fill=(255, 255, 255, 255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png", download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
