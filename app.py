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
<title>QR Art Mask (Black + White Dots, Conservative)</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  h1 { margin-bottom: 8px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  .row { margin-top: 14px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
  .hint { margin-top: 16px; color: #555; line-height: 1.35; }
  .small { font-weight: 400; }
  code { background:#f4f4f4; padding:2px 6px; border-radius:6px; }
</style>
</head>
<body>
  <h1>QR Art Mask (Repo-style: Black + White Dots)</h1>

  <form action="/generate" method="get">
    <label>QR Data <span class="small">(URL or text)</span></label>
    <input type="text" name="data" placeholder="https://..." required />

    <label>Artwork Image URL <span class="small">(optional)</span></label>
    <input type="text" name="art" placeholder="https://.../image.png" />

    <label>Black Dot Size <span class="small">(0.55–0.90). Default 0.70</span></label>
    <input type="text" name="dot" value="0.70" />

    <label>White Dot Strength <span class="small">(0.00–1.00). Default 0.90</span></label>
    <input type="text" name="white" value="0.90" />

    <label>Art Wash <span class="small">(0.00–0.60). Default 0.18 — higher = safer scan</span></label>
    <input type="text" name="wash" value="0.18" />

    <label>Mask Strength <span class="small">(0.00–0.15). Default 0.07 — conservative module removal budget</span></label>
    <input type="text" name="mask" value="0.07" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>

    <div class="hint">
      This generator draws <b>black dots for dark modules</b> and <b>white dots for light modules</b> (mainly on dark art areas),
      while keeping all QR structural regions exact for scanning.
    </div>
  </form>
</body>
</html>
"""

def clamp(v, lo, hi, default):
    try:
        x = float(v)
        if x < lo:
            return lo
        if x > hi:
            return hi
        return x
    except Exception:
        return default

def fetch_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=12)
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
    # Standard QR alignment center positions (conservative approximation).
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
    # Protect 9x9 around each finder (includes the white separator border).
    if r <= 8 and c <= 8:
        return True
    if r <= 8 and c >= n - 9:
        return True
    if r >= n - 9 and c <= 8:
        return True
    return False

def in_timing(r, c, n):
    # Timing patterns at row 6 and col 6 (excluding finder zones)
    if r == 6 and 8 <= c <= n - 9:
        return True
    if c == 6 and 8 <= r <= n - 9:
        return True
    return False

def in_format_info(r, c, n):
    # Format info near row/col 8 around finder areas
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
            # skip overlap with finder zones
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

@app.route("/health")
def health():
    return {"ok": True}

@app.route("/generate")
def generate():
    data = (request.args.get("data") or "").strip()
    art_url = (request.args.get("art") or "").strip()

    black_dot = clamp(request.args.get("dot"), 0.55, 0.90, 0.70)
    white_strength = clamp(request.args.get("white"), 0.00, 1.00, 0.90)
    wash = clamp(request.args.get("wash"), 0.00, 0.60, 0.18)
    mask_strength = clamp(request.args.get("mask"), 0.00, 0.15, 0.07)

    if not data:
        return "Missing QR data", 400

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=0
    )
    qr.add_data(data)
    qr.make(fit=True)

    matrix = qr.get_matrix()  # modules only
    n = len(matrix)
    version = qr.version or 1

    # Rendering scale
    box = 16
    quiet = 6
    size = (n + 2 * quiet) * box

    # Canvas starts white
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    # Artwork + luminance grid
    luma = None
    art_ok = False
    if art_url:
        try:
            art = fetch_image(art_url).resize((n * box, n * box), Image.LANCZOS)
            if wash > 0:
                overlay = Image.new("RGBA", art.size, (255, 255, 255, int(255 * wash)))
                art = Image.alpha_composite(art, overlay)

            canvas.paste(art, (quiet * box, quiet * box), art)

            tiny = art.resize((n, n), Image.BOX).convert("RGBA")
            px = tiny.load()
            luma = [[float(luminance_rgba(px[c, r])) for c in range(n)] for r in range(n)]
            art_ok = True
        except Exception:
            art_ok = False
            luma = None

    draw = ImageDraw.Draw(canvas)

    # Conservative masking: remove a small % of non-protected dark modules
    removed = set()
    if art_ok and luma is not None and mask_strength > 0:
        candidates = []
        dark_count = 0
        for r in range(n):
            for c in range(n):
                if not matrix[r][c]:
                    continue
                if is_protected(r, c, n, version):
                    continue
                dark_count += 1
                # brighter art => better candidate to remove (reveals image)
                candidates.append((luma[r][c], r, c))

        candidates.sort(reverse=True, key=lambda x: x[0])
        k = int(dark_count * mask_strength)
        k = min(k, 2000)
        for i in range(k):
            _, rr, cc = candidates[i]
            removed.add((rr, cc))

    # Helper for dot drawing
    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1.0 - scale) * box / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    # Main render
    # Key idea:
    # - Protected areas: exact black/white squares (no art influence)
    # - Dark modules: black dots (unless removed)
    # - Light modules: optional white dots when background is dark (this creates the repo look)
    for r in range(n):
        for c in range(n):
            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            if is_protected(r, c, n, version):
                # Block artwork here and draw exact matrix
                if matrix[r][c]:
                    draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 255))
                else:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255, 255))
                continue

            bg_l = 255.0
            if art_ok and luma is not None:
                bg_l = luma[r][c]

            # Light module
            if not matrix[r][c]:
                if art_ok and luma is not None:
                    # White dot alpha increases on darker backgrounds only
                    # target=170 means: below 170 (darker), white dots begin to show
                    target = 170.0
                    darkness = max(0.0, min(1.0, (target - bg_l) / target))
                    alpha = int(255 * white_strength * darkness)

                    # Only draw if visible enough (prevents muddy gray everywhere)
                    if alpha >= 35:
                        # Slightly smaller than black dots for cleaner look
                        white_scale = max(0.45, min(0.80, black_dot * 0.85))
                        draw_dot(x0, y0, x1, y1, white_scale, (255, 255, 255, alpha))
                # If no art, do nothing (white on white is pointless)
                continue

            # Dark module
            if (r, c) in removed:
                continue

            # Mild adaptive black-dot sizing (helps “image feel” without breaking scan)
            scale = black_dot
            if art_ok and luma is not None:
                t = bg_l / 255.0  # 0 dark -> 1 light
                scale = black_dot + (0.10 * (0.5 - t))
                scale = max(0.55, min(0.90, scale))

            draw_dot(x0, y0, x1, y1, scale, (0, 0, 0, 255))

    # Quiet zone forced pure white (critical for scanning)
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
