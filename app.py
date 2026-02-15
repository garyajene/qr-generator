from flask import Flask, request, send_file
import qrcode
from PIL import Image, ImageDraw
import requests
import io
import math

app = Flask(__name__)

@app.route("/")
def home():
    return """
    <h2>QR Code Generator (Hybrid Art Mask)</h2>

    <form action="/generate" method="get">
        <label>QR Data (URL or text)</label><br>
        <input type="text" name="data" placeholder="https://example.com" required style="width:400px;"><br><br>

        <label>Artwork Image URL (optional)</label><br>
        <input type="text" name="art" placeholder="https://.../image.png" style="width:400px;"><br><br>

        <label>Dot Size (0.80–0.90 recommended)</label><br>
        <input type="text" name="dot" value="0.85"><br><br>

        <button type="submit">Generate QR</button>
    </form>
    """


@app.route("/generate")
def generate():
    data = request.args.get("data")
    art_url = request.args.get("art")
    dot_scale = float(request.args.get("dot", 0.85))

    if not data:
        return "Missing QR data"

    # -------- QR CONFIG (Balanced Reliability) --------
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        box_size=10,
        border=4
    )

    qr.add_data(data)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    size = len(matrix)

    img_size = size * 10
    img = Image.new("RGBA", (img_size, img_size), "white")
    draw = ImageDraw.Draw(img)

    # -------- LOAD ARTWORK IF PROVIDED --------
    art_img = None
    if art_url:
        try:
            response = requests.get(art_url, timeout=10)
            response.raise_for_status()
            art_img = Image.open(io.BytesIO(response.content)).convert("RGBA")
            art_img = art_img.resize((img_size, img_size))
        except:
            art_img = None

    # -------- DRAW QR WITH CONTROLLED DOTS --------
    for r in range(size):
        for c in range(size):
            if matrix[r][c]:

                x = c * 10
                y = r * 10

                # PROTECT STRUCTURE (Finder patterns)
                if (
                    (r < 7 and c < 7) or
                    (r < 7 and c > size - 8) or
                    (r > size - 8 and c < 7)
                ):
                    draw.rectangle([x, y, x+10, y+10], fill="black")
                else:
                    dot_size = 10 * dot_scale
                    offset = (10 - dot_size) / 2

                    if art_img:
                        # Sample brightness from artwork
                        pixel = art_img.getpixel((x + 5, y + 5))
                        brightness = (pixel[0] + pixel[1] + pixel[2]) / 3

                        # Dark art → white dots
                        color = "white" if brightness < 128 else "black"
                    else:
                        color = "black"

                    draw.ellipse(
                        [
                            x + offset,
                            y + offset,
                            x + offset + dot_size,
                            y + offset + dot_size
                        ],
                        fill=color
                    )

    # -------- COMBINE ART UNDER QR --------
    if art_img:
        img = Image.alpha_composite(art_img, img)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
