from flask import Flask, request, send_file
import qrcode
from PIL import Image, ImageDraw
import requests
from io import BytesIO

app = Flask(__name__)

DEFAULT_BG_IMAGE = "https://replicassets.s3.us-west-2.amazonaws.com/buttnB.jpg"

def fetch_image(url: str) -> Image.Image:
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    img = Image.open(BytesIO(r.content))
    return img.convert("RGBA")

def luminance(r, g, b):
    # Perceived brightness
    return int(0.2126 * r + 0.7152 * g + 0.0722 * b)

def is_in_finder(r, c, n):
    # Finder pattern 7x7 areas (top-left, top-right, bottom-left)
    if r < 7 and c < 7:
        return True
    if r < 7 and c >= n - 7:
        return True
    if r >= n - 7 and c < 7:
        return True
    return False

def draw_finder(draw: ImageDraw.ImageDraw, x0, y0, box):
    # Standard finder: outer black 7x7, inner white 5x5, center black 3x3
    # Outer
    draw.rectangle([x0, y0, x0 + 7*box - 1, y0 + 7*box - 1], fill=(0, 0, 0, 255))
    # Inner white
    draw.rectangle([x0 + box, y0 + box, x0 + 6*box - 1, y0 + 6*box - 1], fill=(255, 255, 255, 255))
    # Center black
    draw.rectangle([x0 + 2*box, y0 + 2*box, x0 + 5*box - 1, y0 + 5*box - 1], fill=(0, 0, 0, 255))

@app.route("/")
def home():
    return """
    <h2>Artistic QR (Mode B: Inverting over Image)</h2>
    <form action="/generate" method="get">
        <input type="text" name="data" placeholder="Enter URL or text" required style="width:420px;">
        <br><br>
        <input type="text" name="bg" placeholder="Background image URL (optional)" style="width:420px;">
        <br><br>
        <button type="submit">Generate</button>
    </form>
    <p style="font-size:12px;color:#666;">Tip: Leave bg blank to use a default.</p>
    """

@app.route("/generate")
def generate():
    data = request.args.get("data", "").strip()
    bg_url = request.args.get("bg", "").strip() or DEFAULT_BG_IMAGE

    if not data:
        return "Missing data", 400

    # Build QR matrix
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()  # True = dark module
    n = len(matrix)

    box = qr.box_size
    border = qr.border
    size_px = (n + border * 2) * box
    offset = border * box

    # White canvas
    canvas = Image.new("RGBA", (size_px, size_px), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # Background image only under the data area (not in quiet zone)
    bg = fetch_image(bg_url)
    bg = bg.resize((n * box, n * box), Image.LANCZOS)
    canvas.paste(bg, (offset, offset), bg)

    # Draw modules (image stays visible; dark modules invert based on underlying brightness)
    for r in range(n):
        for c in range(n):
            x = offset + c * box
            y = offset + r * box

            if is_in_finder(r, c, n):
                # Skip — we draw full finders below for clean scan reliability
                continue

            if matrix[r][c]:
                # sample underlying pixel at the center of this module
                cx = c * box + box // 2
                cy = r * box + box // 2
                pr, pg, pb, pa = bg.getpixel((cx, cy))
                lum = luminance(pr, pg, pb)

                # Invert logic:
                # If background is dark -> draw white module
                # If background is light -> draw black module
                color = (255, 255, 255, 255) if lum < 128 else (0, 0, 0, 255)
                draw.rectangle([x, y, x + box - 1, y + box - 1], fill=color)

    # Draw clean finders (standard QR look)
    # top-left
    draw_finder(draw, offset + 0*box, offset + 0*box, box)
    # top-right
    draw_finder(draw, offset + (n-7)*box, offset + 0*box, box)
    # bottom-left
    draw_finder(draw, offset + 0*box, offset + (n-7)*box, box)

    # Output
    out = BytesIO()
    canvas.convert("RGBA").save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
