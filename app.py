from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno
import math

app = Flask(__name__)

# -----------------------------
# (HTML unchanged — omitted here for clarity, keep yours exactly as-is)
# -----------------------------

# KEEP YOUR ORIGINAL HTML BLOCK HERE EXACTLY AS YOU POSTED IT


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
    m = []
    for row in qr.matrix:
        m.append([bool(v) for v in row])
    return m


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

    qr = segno.make(data, error='h')
    matrix = matrix_from_segno(qr)

    n = len(matrix)   # TRUE module grid size
    version = int(qr.version)

    box = 16
    quiet = 6

    size = (n + 2 * quiet) * box
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))

    # --------------------------
    # FIXED ARTWORK SIZING
    # --------------------------
    if art_url:
        try:
            art_img = fetch_image(art_url)

            # resize EXACTLY to module grid size (no quiet zone confusion)
            module_pixels = n * box
            art_resized = art_img.resize(
                (module_pixels, module_pixels),
                Image.LANCZOS
            )

            if wash > 0:
                overlay = Image.new(
                    "RGBA",
                    art_resized.size,
                    (255, 255, 255, int(255 * wash))
                )
                art_resized = Image.alpha_composite(art_resized, overlay)

            # paste directly into module area only
            canvas.paste(
                art_resized,
                (quiet * box, quiet * box),
                art_resized
            )

        except Exception:
            pass

    draw = ImageDraw.Draw(canvas)

    def draw_dot(x0, y0, x1, y1, scale, rgb):
        pad = (1.0 - scale) * box / 2.0
        draw.ellipse(
            [x0 + pad, y0 + pad, x1 - pad, y1 - pad],
            fill=rgb
        )

    # --------------------------
    # RENDER MATRIX (UNCHANGED LOGIC)
    # --------------------------
    for r in range(n):
        for c in range(n):

            x0 = (quiet + c) * box
            y0 = (quiet + r) * box
            x1 = x0 + box
            y1 = y0 + box

            if matrix[r][c]:
                draw_dot(x0, y0, x1, y1, dot_scale, (0, 0, 0))
            else:
                white_scale = max(0.45, min(0.88, dot_scale * 0.88))
                draw_dot(x0, y0, x1, y1, white_scale, (255, 255, 255))

    # Quiet zone
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
