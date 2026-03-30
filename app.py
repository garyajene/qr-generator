from flask import Flask, request
from io import BytesIO
import base64
from collections import Counter
from PIL import Image
import segno
import os

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6


# ---------- UI ----------
def render_page(qr_img_b64=None, card_mockup_b64=None, dome_mockup_b64=None):
    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Generator</title>
<style>
body {{ font-family: Arial; padding: 30px; }}

#dropzone {{
    width: 400px;
    height: 200px;
    border: 2px dashed #999;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
}}

img {{ margin-top: 20px; }}
</style>
</head>
<body>

<h1>QR Generator</h1>

<form method="POST" enctype="multipart/form-data">
<input name="data" placeholder="Enter QR Data" required><br><br>

<div id="dropzone">Drop Image Here or Click</div>
<input type="file" id="file" name="artfile" accept="image/*" style="display:none">

<br><br>
<button type="submit">Generate</button>
</form>

{f'<h2>Generated QR</h2><img src="data:image/png;base64,{qr_img_b64}">' if qr_img_b64 else ''}

{f'''
<h2>Mockups</h2>
<b>Business Card</b><br>
<img src="data:image/png;base64,{card_mockup_b64}"><br><br>

<b>Dome Sticker</b><br>
<img src="data:image/png;base64,{dome_mockup_b64}">
''' if card_mockup_b64 else ''}

<script>
const dz = document.getElementById("dropzone");
const file = document.getElementById("file");

dz.onclick = () => file.click();

dz.ondragover = e => e.preventDefault();

dz.ondrop = e => {{
    e.preventDefault();
    file.files = e.dataTransfer.files;
}};
</script>

</body>
</html>
"""


# ---------- HELPERS ----------
def image_to_base64(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def fetch_uploaded_image(f):
    if not f or f.filename == "":
        return None
    try:
        return Image.open(BytesIO(f.read())).convert("RGBA")
    except:
        return None


def safe_bg_color(img):
    try:
        if not img:
            return (255, 255, 255)

        small = img.resize((100, 100))
        pixels = list(small.getdata())

        colors = [(r, g, b) for r, g, b, a in pixels if a > 0]

        if not colors:
            return (255, 255, 255)

        return Counter(colors).most_common(1)[0][0]

    except:
        return (255, 255, 255)


# ---------- SAFE PASTE ----------
def safe_paste(base, overlay, pos):
    if overlay.mode == "RGBA":
        base.paste(overlay, pos, overlay)
    else:
        base.paste(overlay, pos)


# ---------- QR ----------
def generate_qr(data, art):
    qr = segno.make(data, error=ERROR_LEVEL)

    buffer = BytesIO()
    qr.save(buffer, kind="png", scale=BOX, border=QUIET)
    buffer.seek(0)

    qr_img = Image.open(buffer).convert("RGBA")

    bg_color = safe_bg_color(art)

    img = Image.new("RGBA", qr_img.size, (*bg_color, 255))
    safe_paste(img, qr_img, (0, 0))

    if art:
        art = art.resize(qr_img.size)
        safe_paste(img, art, (0, 0))

    return img.convert("RGB"), bg_color


def trim_qr(img):
    crop = (QUIET * BOX) // 2
    return img.crop((crop, crop, img.width - crop, img.height - crop))


# ---------- MOCKUPS ----------
def create_card_mockup(qr):
    path = "static/blackcard.png"
    if not os.path.exists(path):
        return None

    card = Image.open(path).convert("RGBA")
    qr = trim_qr(qr)

    w, h = card.size
    size = int(w * 0.38)

    qr = qr.resize((size, size))
    safe_paste(card, qr, (w - size - 20, h - size - 20))

    return card


def create_dome_mockup(qr, bg_color):
    path = "static/dome_piece1.png"
    if not os.path.exists(path):
        return None

    dome = Image.open(path).convert("RGBA")
    qr = trim_qr(qr)

    dw, dh = dome.size

    base = Image.new("RGBA", (dw, dh), (*bg_color, 255))

    size = int(dw * 0.42)
    qr = qr.resize((size, size))

    safe_paste(base, qr, ((dw - size)//2, (dh - size)//2))
    base.alpha_composite(dome)

    return base.resize((int(dw * 0.5), int(dh * 0.5)))


# ---------- ROUTE ----------
@app.route("/", methods=["GET", "POST"])
def home():
    try:
        qr_b64 = None
        card_b64 = None
        dome_b64 = None

        if request.method == "POST":
            data = request.form.get("data")
            art = fetch_uploaded_image(request.files.get("artfile"))

            if data:
                qr, bg_color = generate_qr(data, art)

                qr_b64 = image_to_base64(qr)

                card = create_card_mockup(qr)
                dome = create_dome_mockup(qr, bg_color)

                if card:
                    card_b64 = image_to_base64(card)
                if dome:
                    dome_b64 = image_to_base64(dome)

        return render_page(qr_b64, card_b64, dome_b64)

    except Exception as e:
        return f"<h1>ERROR</h1><pre>{str(e)}</pre>"


# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
