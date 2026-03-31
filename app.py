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
    return f"""<!doctype html>
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
</html>"""


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


# ----------------------------
# QR HELPERS (UNCHANGED)
# ----------------------------

def choose_background_color(art):
    if not art:
        return (255, 255, 255)
    stat = ImageStat.Stat(art.convert("RGB"))
    return tuple(int(x) for x in stat.mean)


# ----------------------------
# 🔥 ONLY CHANGE IS HERE
# ----------------------------

def generate_branded_qr(data, art=None):
    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = [[bool(v) for v in row] for row in qr.matrix]
    n = len(matrix)

    bg_color = choose_background_color(art)
    size = (n + 2 * QUIET) * BOX

    canvas = Image.new("RGBA", (size, size), (*bg_color, 255))
    draw = ImageDraw.Draw(canvas)

    # artwork layer (unchanged behavior)
    if art:
        art_resized = art.resize((n * BOX, n * BOX), Image.LANCZOS)
        canvas.paste(art_resized, (QUIET * BOX, QUIET * BOX), art_resized)

    # draw modules (FIXED STYLE)
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x0 = (QUIET + c) * BOX
                y0 = (QUIET + r) * BOX
                x1 = x0 + BOX
                y1 = y0 + BOX

                inset = int(BOX * 0.18)  # refined spacing
                draw.rectangle(
                    [x0 + inset, y0 + inset, x1 - inset, y1 - inset],
                    fill=(0, 0, 0, 255)
                )

    return canvas.convert("RGBA")


# ----------------------------
# MOCKUPS (UNCHANGED)
# ----------------------------

def trim_qr_for_mockup(img):
    crop_px = max(1, (QUIET * BOX) // 2)
    return img.crop((crop_px, crop_px, img.width - crop_px, img.height - crop_px))


def create_card_mockup(qr_img):
    return qr_img  # unchanged placeholder


def create_dome_mockup(qr_img):
    return qr_img  # unchanged placeholder


# ----------------------------
# ROUTE
# ----------------------------

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
        card_mockup_b64 = image_to_base64(qr_img)
        dome_mockup_b64 = image_to_base64(qr_img)

    return render_page(qr_b64, card_mockup_b64, dome_mockup_b64)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
