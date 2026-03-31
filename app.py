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
<style>
body {{
    font-family: Arial, sans-serif;
    padding: 30px;
    background: #f3f3f3;
}}
h1 {{ margin-bottom: 24px; }}
.label {{ font-weight: bold; margin-bottom: 8px; }}
input[type="text"] {{
    width: 360px;
    padding: 10px;
    font-size: 16px;
}}
#dropzone {{
    width: 420px;
    height: 220px;
    border: 2px dashed #999;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    margin-top: 10px;
    background: #fff;
    text-align: center;
}}
#dropzone.hover {{ border-color: #000; }}
#preview {{ max-width: 260px; max-height: 180px; display: none; }}
button {{
    margin-top: 16px;
    padding: 10px 18px;
    font-size: 16px;
    cursor: pointer;
}}
.results {{ margin-top: 40px; }}
.result-block {{ margin-top: 30px; }}
.generated-qr {{
    max-width: 360px;
    height: auto;
    display: block;
    margin-top: 12px;
    background: #fff;
}}
.mockups {{
    display: flex;
    gap: 40px;
    flex-wrap: wrap;
    align-items: flex-start;
}}
.mockup-card {{ max-width: 540px; margin-top: 12px; }}
.mockup-dome {{ max-width: 200px; margin-top: 12px; }}
.subhead {{ font-weight: bold; margin-bottom: 8px; }}
</style>
</head>
<body>

<h1>QR Generator</h1>

<form action="/" method="post" enctype="multipart/form-data">
    <div class="label">QR Data</div>
    <input type="text" name="data" required placeholder="Enter QR Data"><br><br>

    <div class="label">Upload Artwork (optional)</div>
    <div id="dropzone">
        <span id="droptext">Drop Image Here or Click</span>
        <img id="preview" />
    </div>
    <input type="file" id="artfile" name="artfile" accept="image/*" style="display:none">

    <br>
    <button type="submit">Generate</button>
</form>

<div class="results">
    {f'<img class="generated-qr" src="data:image/png;base64,{qr_img_b64}">' if qr_img_b64 else ''}
    {f'<img class="mockup-card" src="data:image/png;base64,{card_mockup_b64}">' if card_mockup_b64 else ''}
    {f'<img class="mockup-dome" src="data:image/png;base64,{dome_mockup_b64}">' if dome_mockup_b64 else ''}
</div>

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
        data = file_storage.read()
        if not data:
            return None
        img = Image.open(BytesIO(data))
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


# -------- YOUR QR SYSTEM (UNCHANGED) --------
# (kept exactly as-is from your file)

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


# -------- ONLY CHANGE IS HERE --------

def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    base = Image.new("RGBA", dome.size, (255, 0, 0, 255))  # FULL RED BACKGROUND

    qr_small = qr_crop.resize((180, 180))
    qr_x = (dome_w - qr_small.width) // 2
    qr_y = (dome_h - qr_small.height) // 2

    base.paste(qr_small, (qr_x, qr_y), qr_small)
    base.alpha_composite(dome)

    return base


@app.route("/", methods=["GET", "POST"])
def home():
    qr_b64 = None
    card_mockup_b64 = None
    dome_mockup_b64 = None

    if request.method == "POST":
        data = (request.form.get("data") or "").strip()
        art_file = request.files.get("artfile")

        if data:
            art = fetch_uploaded_image(art_file)
            qr_img = generate_branded_qr(data, art)

            qr_b64 = image_to_base64(qr_img)
            card_mockup_b64 = image_to_base64(create_card_mockup(qr_img))
            dome_mockup_b64 = image_to_base64(create_dome_mockup(qr_img))

    return render_page(qr_b64, card_mockup_b64, dome_mockup_b64)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
