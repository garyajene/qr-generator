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
    font-family: Arial;
    padding: 30px;
}}

#dropzone {{
    width: 400px;
    height: 200px;
    border: 2px dashed #999;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
}}

img {{
    margin-top: 20px;
}}
</style>
</head>
<body>

<h1>QR Generator</h1>

<form method="POST" enctype="multipart/form-data">
<input name="data" placeholder="Enter QR Data" required><br><br>

<div id="dropzone">Drop Image Here or Click</div>
<input type="file" id="file" name="artfile" style="display:none">

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

dz.ondragover = e => {{
    e.preventDefault();
}};

dz.ondrop = e => {{
    e.preventDefault();
    file.files = e.dataTransfer.files;
}};
</script>

</body>
</html>
"""


def image_to_base64(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def fetch_uploaded_image(f):
    if not f:
        return None
    try:
        return Image.open(BytesIO(f.read())).convert("RGBA")
    except:
        return None


def choose_background_color(img):
    if not img:
        return (255, 255, 255)

    img = img.resize((200, 200))
    pixels = list(img.getdata())

    colors = []
    for r, g, b, a in pixels:
        if a > 0:
            colors.append((r, g, b))

    count = Counter(colors).most_common(1)
    return count[0][0] if count else (255, 255, 255)


def generate_qr(data, art):
    qr = segno.make(data, error=ERROR_LEVEL)
    size = (qr.symbol_size()[0]) * BOX

    bg_color = choose_background_color(art)

    img = Image.new("RGB", (size, size), bg_color)
    draw = ImageDraw.Draw(img)

    qr_img = qr.to_pil(scale=BOX, border=QUIET)
    img.paste(qr_img, (0, 0))

    if art:
        art = art.resize((size, size))
        img.paste(art, (0, 0), art)

    return img, bg_color


def trim_qr(img):
    crop = (QUIET * BOX) // 2
    return img.crop((crop, crop, img.width - crop, img.height - crop))


def create_card_mockup(qr):
    card = Image.open("static/blackcard.png").convert("RGBA")
    qr = trim_qr(qr)

    w, h = card.size
    size = int(w * 0.35)

    qr = qr.resize((size, size))
    x = w - size - 20
    y = h - size - 20

    card.paste(qr, (x, y), qr)
    return card


def create_dome_mockup(qr, bg_color):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    qr = trim_qr(qr)

    dw, dh = dome.size

    # 🔥 IMPORTANT FIX: background is now artwork color
    base = Image.new("RGBA", (dw, dh), (*bg_color, 255))

    size = int(dw * 0.45)
    qr = qr.resize((size, size))

    x = (dw - size) // 2
    y = (dh - size) // 2

    base.paste(qr, (x, y), qr)

    # overlay dome AFTER background + QR
    base.alpha_composite(dome)

    # scale down final output
    return base.resize((int(dw * 0.5), int(dh * 0.5)))


@app.route("/", methods=["GET", "POST"])
def home():
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

            card_b64 = image_to_base64(card)
            dome_b64 = image_to_base64(dome)

    return render_page(qr_b64, card_b64, dome_b64)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
