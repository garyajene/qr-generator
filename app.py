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
<title>Repo-Style QR Art (Balanced)</title>
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
  <h1>Repo-Style QR Art (Balanced + Scannable)</h1>

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

    <label>Balanced Removal <span class="small">(0.00–0.08). Default 0.02 — small, quadrant-balanced</span></label>
    <input type="text" name="rm" value="0.02" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>

    <div class="hint">
      This version fixes the “pull/zoom” look by making dot changes <b>balanced</b> across the code.
      It uses dot-size modulation first (safe), and only tiny balanced removal second (optional).
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
    # 9x9 finder + separator
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
    # Prefer dark modules over darker pixels, and light modules over lighter pixels.
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
    # 2x2 quadrants
    top = 0 if r < n // 2 else 1
    left = 0 if c < n // 2 else 1
    return top * 2 + left  # 0..3

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

    if not data:
        return "Missing QR data", 400

    # Render params
    box = 16
    quiet = 6

    # Load art (optional)
    art_ok = False
    art_img = None
    if art_url:
        try:
            art_img = fetch_image(art_url)
            art_ok = True
        except Exception:
            art_ok = False
            art_img = None

    # Build QR matrices with segno masks; pick best if art present
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

        # Resize art to exact module area
        art_resized = art_img.convert("RGBA").resize((n * box, n * box), Image.LANCZOS)

        # Wash for scannability
        if wash > 0:
            overlay = Image.new("RGBA", art_resized.size, (255, 255, 255, int(255 * wash)))
            art_resized = Image.alpha_composite(art_resized, overlay)

        # Luma grid per module
        tiny = art_resized.resize((n, n), Image.BOX).convert("RGBA")
        px = tiny.load()
        luma = [[float(luminance_rgba(px[c, r])) for c in range(n)] for r in range(n)]

        best_score = -1e18
        best = None
        best_m = None
        best_v = None

        for mask in range(8):
            qr_i = segno.make(data, error='h', mask=mask)
            m_i = matrix_from_segno(qr_i)
            v_i = int(qr_i.version)
            sc = score_mask(m_i, luma, v_i)
            if sc > best_score:
                best_score = sc
                best = qr_i
                best_m = m_i
                best_v = v_i

        matrix = best_m
        version = best_v

    n = len(matrix)
    size = (n + 2 * quiet) * box

    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # Paste art under module area only
    if art_ok and art_resized is not None:
        canvas.paste(art_resized, (quiet * box, quiet * box), art_resized)

    # Balanced removal candidates (tiny) — distribute evenly across quadrants
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
                # brighter pixels are better places to remove (reveals art)
                candidates_by_q[q].append((luma[r][c], r, c))

        # per-quadrant quota
        for q in range(4):
            candidates_by_q[q].sort(reverse=True, key=lambda x: x[0])
            kq = int(dark_counts_q[q] * rm)
            kq = min(kq, 800)  # guardrail
            for i in range(kq):
                _, rr, cc = candidates_by_q[q][i]
                removed.add((rr, cc))

    # Dot draw helper (binary only)
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
                # Hard squares for protected structure (no art influence)
                if matrix[r][c]:
                    draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))
                else:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))
                continue

            if matrix[r][c]:
                # dark module
                if (r, c) in removed:
                    continue

                scale = base_dot
                if art_ok and luma is not None:
                    # Modulation: in brighter areas shrink black dot; in darker areas slightly grow it.
                    # This reveals the image WITHOUT unbalancing density.
                    t = luma[r][c] / 255.0  # 0 dark, 1 bright
                    scale = base_dot + mod * (0.50 - t)  # bright -> smaller, dark -> bigger
                    scale = max(0.55, min(0.92, scale))

                draw_dot(x0, y0, x1, y1, scale, (0, 0, 0))

            else:
                # light module -> white dot (structural)
                # Keep slightly smaller so art can show in the gaps between dots.
                wscale = max(0.40, min(0.85, base_dot * 0.88))
                draw_dot(x0, y0, x1, y1, wscale, (255, 255, 255))

    # Quiet zone forced pure white
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
