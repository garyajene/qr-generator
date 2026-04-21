# --- YOUR ORIGINAL IMPORTS (UNCHANGED) ---
from flask import Flask, request
from io import BytesIO
import base64
import random
from collections import Counter
from PIL import Image, ImageDraw, ImageStat
import segno
import html

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6

# =========================
# NEW SIMPLE QR GENERATOR
# =========================
def generate_simple_qr(data, logo=None):
    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = [[bool(v) for v in row] for row in qr.matrix]
    n = len(matrix)

    size = (n + 2 * QUIET) * BOX
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x0 = (QUIET + c) * BOX
                y0 = (QUIET + r) * BOX
                x1 = x0 + BOX
                y1 = y0 + BOX
                draw.ellipse([x0, y0, x1, y1], fill=(0, 0, 0, 255))

    if logo:
        logo = logo.convert("RGBA")
        logo_size = int(size * 0.22)
        logo = logo.resize((logo_size, logo_size), Image.LANCZOS)

        lx = (size - logo_size) // 2
        ly = (size - logo_size) // 2
        img.paste(logo, (lx, ly), logo)

    return img


# =========================
# YOUR EXISTING FUNCTIONS
# (UNCHANGED — I DID NOT TOUCH THESE)
# =========================
# >>> EVERYTHING YOU ALREADY HAD STAYS HERE <<<
# (I am not rewriting your 1000+ lines—leave them exactly as-is)


# =========================
# ONLY CHANGE INSIDE HOME()
# =========================
@app.route("/", methods=["GET", "POST"])
def home():
    qr_b64 = None
    card_mockup_b64 = None
    dome_mockup_b64 = None
    data_value = ""
    art_data_b64 = ""
    bg_override_value = ""
    current_bg_hex = "#ffffff"

    if request.method == "POST":
        data_value = (request.form.get("data") or "").strip()
        bg_override_value = (request.form.get("bg_override") or "").strip()
        art_data_b64 = (request.form.get("art_data") or "").strip()

        # NEW
        qr_style = request.form.get("qr_style") or "artistic"

        art_file = request.files.get("artfile")
        art = fetch_uploaded_image(art_file)

        if art is None and art_data_b64:
            art = fetch_image_from_hidden_b64(art_data_b64)

        if data_value:
            if qr_style == "simple":
                qr_img = generate_simple_qr(data_value, logo=art)
            else:
                art = normalize_artwork_to_square(art, tolerance=0.12, bg_override=bg_override_value)
                qr_img = generate_branded_qr(data_value, art, bg_override=bg_override_value)

            qr_b64 = image_to_base64(qr_img)

            card_mockup = create_card_mockup(qr_img)
            dome_mockup = create_dome_mockup(qr_img)

            card_mockup_b64 = image_to_base64(card_mockup)
            dome_mockup_b64 = image_to_base64(dome_mockup)

            current_bg_hex = rgb_to_hex(qr_img.convert("RGB").getpixel((5, 5)))

            if art is not None:
                art_data_b64 = image_to_base64(art)

    return render_page(
        qr_img_b64=qr_b64,
        card_mockup_b64=card_mockup_b64,
        dome_mockup_b64=dome_mockup_b64,
        data_value=data_value,
        art_data_b64=art_data_b64,
        bg_override_value=bg_override_value,
        current_bg_hex=current_bg_hex,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
