from flask import Flask, request, send_file
import io
import math
import requests
import qrcode
from PIL import Image, ImageDraw

app = Flask(__name__)

# ---------- Helpers ----------

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def luminance(rgb):
    # Perceived luminance (0..255)
    r, g, b = rgb
    return int(0.2126 * r + 0.7152 * g + 0.0722 * b)

def is_in_finder_or_separator(x, y, n, quiet=4):
    """
    Protect the 3 finder patterns + their 1-module separators.
    Coordinates include quiet zone already.
    Finder core is 7x7; with 1-module separator => 9x9.
    """
    # Finder top-left (including separator): [quiet-1 .. quiet+7] => size 9
    # But quiet zone is already all white; we draw finders inside.
    tl_x0, tl_y0 = quiet, quiet
    tr_x0, tr_y0 = quiet + (n - 7), quiet
    bl_x0, bl_y0 = quiet, quiet + (n - 7)

    def in_block(x, y, x0, y0):
        return (x0 - 1) <= x <= (x0 + 7) and (y0 - 1) <= y <= (y0 + 7)

    return (
        in_block(x, y, tl_x0, tl_y0) or
        in_block(x, y, tr_x0, tr_y0) or
        in_block(x, y, bl_x0, bl_y0)
    )

def draw_finder(draw, x0, y0, box, color_fg=(0, 0, 0), color_bg=(255, 255, 255)):
    """
    Draw a standard QR finder pattern at module origin (x0,y0) in module units.
    Finder is 7x7 with the classic rings.
    """
    # Outer 7x7 black
    draw.rectangle(
        [x0 * box, y0 * box, (x0 + 7) * box - 1, (y0 + 7) * box - 1],
        fill=color_fg
    )
    # Inner 5x5 white
    draw.rectangle(
        [(x0 + 1) * box, (y0 + 1) * box, (x0 + 6) * box - 1, (y0 + 6) * box - 1],
        fill=color_bg
    )
    # Inner 3x3 black
    draw.rectangle(
        [(x0 + 2) * box, (y0 + 2) * box, (x0 + 5) * box - 1, (y0 + 5) * box - 1],
        fill=color_fg
    )

def build_qr_matrix(data: str):
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # high for art/masking
        box_size=10,
        border=4
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()  # includes border
    return matrix

def fetch_image(url: str, timeout=12):
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content))

def render_art_qr(data: str, art_url: str, box_size: int = 10, dot_scale: float = 0.78):
    """
    Repository-style approach:
    - Use the artwork as the base image
    - For each 'black' module, draw a dot whose color flips based on art brightness
    - Protect finder patterns so scanners can lock on
    """
    matrix = build_qr_matrix(data)
    h = len(matrix)
    w = len(matrix[0])
    assert h == w, "QR matrix must be square"
    modules = w

    # Load and prep artwork
    art = fetch_image(art_url).convert("RGB")
    # Fit artwork to QR pixel size
    size_px = modules * box_size
    art = art.resize((size_px, size_px), Image.LANCZOS)

    # Start with artwork as the canvas
    canvas = art.copy().convert("RGB")
    draw = ImageDraw.Draw(canvas)

    # Determine quiet zone size from qrcode border=4
    quiet = 4  # modules

    # Dot radius inside a module
    dot_scale = clamp(dot_scale, 0.35, 0.95)
    r = int((box_size * dot_scale) / 2)

    # Draw dots module-by-module
    for y in range(modules):
        for x in range(modules):
            # Skip finder patterns + their separators (we draw them cleanly after)
            if is_in_finder_or_separator(x, y, n=(modules - 2 * quiet), quiet=quiet):
                continue

            if not matrix[y][x]:
                # White module => draw nothing; artwork shows through
                continue

            # Sample artwork brightness at module center
            cx = x * box_size + box_size // 2
            cy = y * box_size + box_size // 2
            px = art.getpixel((cx, cy))
            lum = luminance(px)

            # Flip color based on brightness (dark art => white dot, light art => black dot)
            dot_color = (255, 255, 255) if lum < 128 else (0, 0, 0)

            # Draw circular dot
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot_color)

    # Force quiet zone to white for scanner reliability
    qpx = quiet * box_size
    # Top
    draw.rectangle([0, 0, size_px - 1, qpx - 1], fill=(255, 255, 255))
    # Bottom
    draw.rectangle([0, size_px - qpx, size_px - 1, size_px - 1], fill=(255, 255, 255))
    # Left
    draw.rectangle([0, 0, qpx - 1, size_px - 1], fill=(255, 255, 255))
    # Right
    draw.rectangle([size_px - qpx, 0, size_px - 1, size_px - 1], fill=(255, 255, 255))

    # Draw clean finder patterns (on top) for scan reliability
    n = modules - 2 * quiet  # data area size in modules
    # Finder origins in module coords (including quiet zone)
    tl = (quiet, quiet)
    tr = (quiet + (n - 7), quiet)
    bl = (quiet, quiet + (n - 7))

    # Also draw 1-module white separators around finders
    def white_separator(x0, y0):
        draw.rectangle(
            [(x0 - 1) * box_size, (y0 - 1) * box_size, (x0 + 7) * box_size - 1, (y0 + 7) * box_size - 1],
            fill=(255, 255, 255)
        )

    for (fx, fy) in (tl, tr, bl):
        white_separator(fx, fy)
        draw_finder(draw, fx, fy, box_size)

    return canvas

def render_basic_qr(data: str, box_size: int = 10):
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=4
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img

# ---------- Routes ----------

@app.route("/")
def home():
    return """
    <html>
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>QR Code Generator (Art Mask)</title>
      </head>
      <body style="font-family: Arial, sans-serif; padding: 24px;">
        <h2>QR Code Generator (Repository-Style Art Mask)</h2>
        <form action="/generate" method="get" style="max-width: 720px;">
          <div style="margin-bottom: 12px;">
            <label><b>QR Data</b> (URL or text)</label><br/>
            <input type="text" name="data" placeholder="https://..." required
                   style="width: 100%; padding: 10px; font-size: 16px;" />
          </div>

          <div style="margin-bottom: 12px;">
            <label><b>Artwork Image URL</b> (optional — this creates the repository-style look)</label><br/>
            <input type="text" name="art" placeholder="https://.../image.png"
                   style="width: 100%; padding: 10px; font-size: 16px;" />
          </div>

          <div style="margin-bottom: 12px;">
            <label><b>Dot Size</b> (0.35 to 0.95). Default 0.78</label><br/>
            <input type="text" name="dot" placeholder="0.78"
                   style="width: 140px; padding: 10px; font-size: 16px;" />
          </div>

          <button type="submit" style="padding: 10px 18px; font-size: 16px;">
            Generate QR
          </button>
        </form>

        <p style="margin-top: 18px; color: #444;">
          Tip: Use a direct image URL (publicly accessible). Transparent PNGs are fine.
        </p>
      </body>
    </html>
    """

@app.route("/generate")
def generate():
    data = request.args.get("data", "").strip()
    art_url = request.args.get("art", "").strip()
    dot = request.args.get("dot", "").strip()

    if not data:
        return "Missing 'data' parameter", 400

    # Dot size (how big the circle is inside each module)
    dot_scale = 0.78
    if dot:
        try:
            dot_scale = float(dot)
        except:
            dot_scale = 0.78

    try:
        if art_url:
            img = render_art_qr(data, art_url, box_size=10, dot_scale=dot_scale)
        else:
            img = render_basic_qr(data, box_size=10)
    except Exception as e:
        return f"Generation failed: {str(e)}", 500

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png", as_attachment=True, download_name="qr.png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
