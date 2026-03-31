from flask import Flask, request
from io import BytesIO
import base64
from collections import Counter
from PIL import Image, ImageDraw, ImageStat
import segno

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6


def render_page(qr_img_b64=None, card_mockup_b64=None, dome_mockup_b64=None):
    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Generator</title>
</head>
<body>
<h1>QR Generator</h1>

<form action="/" method="post" enctype="multipart/form-data">
<input type="text" name="data" required placeholder="Enter QR Data"><br><br>
<input type="file" name="artfile"><br><br>
<button type="submit">Generate</button>
</form>

{f'<img src="data:image/png;base64,{qr_img_b64}"><br>' if qr_img_b64 else ''}
{f'<img src="data:image/png;base64,{card_mockup_b64}"><br>' if card_mockup_b64 else ''}
{f'<img src="data:image/png;base64,{dome_mockup_b64}"><br>' if dome_mockup_b64 else ''}

</body>
</html>
"""


def image_to_base64(img):
    out = BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def fetch_uploaded_image(file_storage):
    if not file_storage or file_storage.filename == "":
        return None
    try:
        img = Image.open(BytesIO(file_storage.read()))
        return img.convert("RGBA")
    except:
        return None


def choose_background_color(art):
    if not art:
        return (255, 255, 255)
    stat = ImageStat.Stat(art.convert("RGB"))
    return tuple(int(x) for x in stat.mean)


def generate_branded_qr(data, art=None):
    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = [[bool(v) for v in row] for row in qr.matrix]
    n = len(matrix)

    bg_color = choose_background_color(art)
    size = (n + 2 * QUIET) * BOX

    canvas = Image.new("RGBA", (size, size), (*bg_color, 255))
    draw = ImageDraw.Draw(canvas)

    if art:
        art_resized = art.resize((n * BOX, n * BOX), Image.LANCZOS)
        canvas.paste(art_resized, (QUIET * BOX, QUIET * BOX), art_resized)

    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x0 = (QUIET + c) * BOX
                y0 = (QUIET + r) * BOX
                x1 = x0 + BOX
                y1 = y0 + BOX
                draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 255))

    return canvas


def trim_qr_for_mockup(img):
    crop_px = max(1, (QUIET * BOX) // 2)
    return img.crop((crop_px, crop_px, img.width - crop_px, img.height - crop_px))


def create_card_mockup(qr_img):
    card = Image.open("static/blackcard.png").convert("RGBA")
    qr_small = trim_qr_for_mockup(qr_img).resize((180, 180))
    card.paste(qr_small, (card.width - 200, card.height - 200), qr_small)
    return card


# 🔥 FIXED DOME FUNCTION ONLY
def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    qr_small = qr_crop.resize((180, 180), Image.LANCZOS)

    # 🔴 circular mask
    mask = Image.new("L", qr_small.size, 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.ellipse((0, 0, qr_small.width, qr_small.height), fill=255)

    qr_circle = Image.new("RGBA", qr_small.size)
    qr_circle.paste(qr_small, (0, 0), mask)

    bg_color = qr_crop.convert("RGB").getpixel((5, 5))
    base = Image.new("RGBA", dome.size, (*bg_color, 255))

    qr_x = (dome_w - qr_circle.width) // 2
    qr_y = (dome_h - qr_circle.height) // 2

    base.paste(qr_circle, (qr_x, qr_y), qr_circle)
    base.alpha_composite(dome)

    return base


@app.route("/", methods=["GET", "POST"])
def home():
    qr_b64 = None
    card_mockup_b64 = None
    dome_mockup_b64 = None

    if request.method == "POST":
        data = request.form.get("data")
        art = fetch_uploaded_image(request.files.get("artfile"))

        qr_img = generate_branded_qr(data, art)

        qr_b64 = image_to_base64(qr_img)
        card_mockup_b64 = image_to_base64(create_card_mockup(qr_img))
        dome_mockup_b64 = image_to_base64(create_dome_mockup(qr_img))

    return render_page(qr_b64, card_mockup_b64, dome_mockup_b64)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
