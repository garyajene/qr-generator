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
<title>Client-Safe QR Art (Centered + Full Coverage)</title>
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
  <h1>QR Code Generator (Centered + Full Coverage + Scannable)</h1>

  <form action="/generate" method="get">
    <label>QR Data <span class="small">(URL or text)</span></label>
    <input type="text" name="data" placeholder="https://..." required />

    <label>Artwork Image URL <span class="small">(optional — auto-fit, centered, no crop)</span></label>
    <input type="text" name="art" placeholder="https://.../image.png" />

    <label>Output Size (px) <span class="small">(min 512, max 2048). Default 1024</span></label>
    <input type="text" name="px" value="1024" />

    <label>Quiet Zone (modules) <span class="small">(min 4). Default 6 — higher = safer scan</span></label>
    <input type="text" name="quiet" value="6" />

    <label>Dot Size (dark modules) <span class="small">(0.60–0.92). Default 0.80</span></label>
    <input type="text" name="dot" value="0.80" />

    <label>Dot Size (light modules) <span class="small">(0.30–0.80). Default 0.48</span></label>
    <input type="text" name="lightdot" value="0.48" />

    <label>Base Wash <span class="small">(0.00–0.60). Default 0.10</span></label>
    <input type="text" name="wash" value="0.10" />

    <label>Auto Wash (1=on, 0=off) <span class="small">(default 1) — boosts wash only if needed to scan</span></label>
    <input type="text" name="autowash" value="1" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>

    <div class="hint">
      This version forces one consistent coordinate system: the artwork is fitted+centered into the same final square canvas as the QR,
      then QR modules are rendered on top. Quiet zone is forced pure white for scanning.
      Auto-wash brightens only when your artwork is too dark under dark modules.
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

def clamp_int(v, lo, hi, default):
    try:
        x = int(float(v))
        return max(lo, min(hi, x))
    except Exception:
        return default

def fetch_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGBA")

def contain_and_pad_square(img: Image.Image, side: int, pad_color=(255, 255, 255, 255)) -> Image.Image:
    """
    Fit whole image into a square (no crop), keep aspect ratio, center it (client-friendly).
    """
    img = img.convert("RGBA")
    w, h = img.size
    if w <= 0 or h <= 0:
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

def luma_from_rgb(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b

def score_mask(matrix, luma_grid, version):
    """
    Prefer masks that align dark modules with darker pixels and light modules with lighter pixels.
    """
    n = len(matrix)
    s = 0.0
    count = 0
    for r in range(n):
        for c in range(n):
            if is_protected(r, c, n, version):
                continue
            y = luma_grid[r][c]
            if matrix[r][c]:
                s += (255.0 - y)
            else:
                s += 0.35 * y
            count += 1
    return s / max(1, count)

def compute_luma_grid_from_canvas(canvas_rgba: Image.Image, quiet: int, box: int, n: int):
    """
    Build an n x n luminance grid sampled from the module area of the FINAL canvas.
    """
    module_side_px = n * box
    x0 = quiet * box
    y0 = quiet * box
    crop = canvas_rgba.crop((x0, y0, x0 + module_side_px, y0 + module_side_px)).convert("RGB")
    tiny = crop.resize((n, n), Image.BOX)
    px = tiny.load()
    grid = [[luma_from_rgb(px[c, r]) for c in range(n)] for r in range(n)]
    return grid

def apply_wash(img_rgba: Image.Image, wash: float) -> Image.Image:
    if wash <= 0:
        return img_rgba
    overlay = Image.new("RGBA", img_rgba.size, (255, 255, 255, int(255 * wash)))
    return Image.alpha_composite(img_rgba, overlay)

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

    out_px = clamp_int(request.args.get("px"), 512, 2048, 1024)
    quiet = clamp_int(request.args.get("quiet"), 4, 16, 6)

    dot = clamp(request.args.get("dot"), 0.60, 0.92, 0.80)
    lightdot = clamp(request.args.get("lightdot"), 0.30, 0.80, 0.48)
    base_wash = clamp(request.args.get("wash"), 0.00, 0.60, 0.10)
    autowash = clamp_int(request.args.get("autowash"), 0, 1, 1)

    if not data:
        return "Missing QR data", 400

    # Make QR with error H. We'll pick best mask AFTER we have the final art canvas.
    # First generate any QR to get version/module count.
    qr0 = segno.make(data, error='h', mask=0)
    version = int(qr0.version)
    n = qr0.symbol_size()[0]  # module count per side (no border)

    # Compute box so the QR (modules + quiet) fills the entire output canvas
    total_modules = n + 2 * quiet
    box = max(6, out_px // total_modules)  # keep modules large enough to scan
    size = total_modules * box

    # FINAL CANVAS: artwork and QR share the same exact size and origin (this fixes your centering issues)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    if art_url:
        try:
            art_img = fetch_image(art_url)
            art_canvas = contain_and_pad_square(art_img, size, pad_color=(255, 255, 255, 255))
            art_canvas = apply_wash(art_canvas, base_wash)
            canvas.paste(art_canvas, (0, 0), art_canvas)
        except Exception:
            pass
    else:
        # no art -> white background (still works)
        pass

    # Build luminance grid from the module area of the FINAL canvas (not a separate resized art object)
    luma_grid = compute_luma_grid_from_canvas(canvas, quiet, box, n)

    # Choose best mask (0-7) given the art behind it
    best_score = -1e18
    best_matrix = None
    best_version = version
    for mask in range(8):
        qri = segno.make(data, error='h', mask=mask)
        mi = matrix_from_segno(qri)
        vi = int(qri.version)
        sc = score_mask(mi, luma_grid, vi)
        if sc > best_score:
            best_score = sc
            best_matrix = mi
            best_version = vi

    matrix = best_matrix
    version = best_version
    n = len(matrix)

    draw = ImageDraw.Draw(canvas)

    # Auto-wash: if the art is too dark under dark modules, boost wash slightly (ONLY when needed)
    if art_url and autowash == 1:
        # Sample average luminance where dark modules will be placed (excluding protected)
        s = 0.0
        count = 0
        for r in range(n):
            for c in range(n):
                if is_protected(r, c, n, version):
                    continue
                if matrix[r][c]:
                    s += luma_grid[r][c]
                    count += 1
        avg_dark_under = s / max(1, count)

        # If avg luminance is too low, scanning will fail. Target a safer floor.
        # Conservative target: 110–140 depending on phones; we’ll aim for ~125.
        target = 125.0
        if avg_dark_under < target:
            # Convert deficit to extra wash (gentle). Clamp so we don't blow out the art.
            deficit = (target - avg_dark_under) / 255.0
            extra = max(0.0, min(0.35, deficit * 0.85))
            if extra > 0:
                canvas = apply_wash(canvas, extra)
                draw = ImageDraw.Draw(canvas)
                # Recompute luma_grid after wash so dots behave consistently
                luma_grid = compute_luma_grid_from_canvas(canvas, quiet, box, n)

    # Helper to draw circle dot in module cell
    def draw_dot(x0, y0, scale, rgb):
        pad = (1.0 - scale) * box / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x0 + box - pad, y0 + box - pad], fill=rgb)

    # Render modules
    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box

            if is_protected(r, c, n, version):
                # keep structural areas crisp
                if matrix[r][c]:
                    draw.rectangle([x0, y0, x0 + box, y0 + box], fill=(0, 0, 0, 255))
                else:
                    draw.rectangle([x0, y0, x0 + box, y0 + box], fill=(255, 255, 255, 255))
                continue

            if matrix[r][c]:
                # Dark module = always black dot (do not invert QR meaning)
                draw_dot(x0, y0, dot, (0, 0, 0, 255))
            else:
                # Light module = white dot (repo look), smaller so it doesn't harm scanning
                draw_dot(x0, y0, lightdot, (255, 255, 255, 255))

    # Force quiet zone pure white ON TOP (so it scans)
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
