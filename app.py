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
<style>
body {{
    font-family: Arial, sans-serif;
    padding: 30px;
    background: #f3f3f3;
}}
#dropzone {{
    width: 420px;
    height: 220px;
    border: 2px dashed #999;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    background: #fff;
}}
</style>
</head>
<body>

<h1>QR Generator</h1>

<form method="post" enctype="multipart/form-data">
    <input type="text" name="data" required placeholder="Enter QR Data"><br><br>

    <div id="dropzone">Drop Image Here or Click</div>
    <input type="file" name="artfile" id="artfile" style="display:none">

    <br><br>
    <button type="submit">Generate</button>
</form>

{f'<h2>QR</h2><img src="data:image/png;base64,{qr_img_b64}">' if qr_img_b64 else ""}

{f'''
<h2>Mockups</h2>
<img src="data:image/png;base64,{card_mockup_b64}">
<img src="data:image/png;base64,{dome_mockup_b64}">
''' if card_mockup_b64 and dome_mockup_b64 else ""}

<script>
const dz = document.getElementById("dropzone");
const input = document.getElementById("artfile");

dz.onclick = () => input.click();

dz.ondrop = e => {{
    e.preventDefault();
    input.files = e.dataTransfer.files;
}};
dz.ondragover = e => e.preventDefault();
</script>

</body>
</html>"""


def image_to_base64(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def fetch_uploaded_image(f):
    if not f:
        return None
    try:
        img = Image.open(BytesIO(f.read()))
        img.load()
        return img.convert("RGBA")
    except:
        return None


def generate_branded_qr(data, art=None):
    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = [[bool(v) for v in row] for row in qr.matrix]

    n = len(matrix)
    size = (n + 2 * QUIET) * BOX

    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x0 = (QUIET + c) * BOX
                y0 = (QUIET + r) * BOX
                draw.rectangle([x0, y0, x0+BOX, y0+BOX], fill=(0,0,0))

    return canvas


def trim_qr_for_mockup(img):
    crop = (QUIET * BOX) // 2
    return img.crop((crop, crop, img.width - crop, img.height - crop))


def create_card_mockup(qr_img):
    card = Image.open("static/blackcard.png").convert("RGBA")
    qr = trim_qr_for_mockup(qr_img).resize((200,200))
    card.paste(qr, (card.width-220, card.height-220), qr)
    return card


# 🔥 ONLY FUNCTION CHANGED
def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    w, h = dome.size

    qr = trim_qr_for_mockup(qr_img).resize((int(w*0.45), int(h*0.45)))

    canvas = Image.new("RGBA", dome.size, (0,0,0,0))
    canvas.paste(qr, ((w-qr.width)//2, (h-qr.height)//2), qr)

    # TRUE MASK
    alpha = dome.split()[-1]
    masked = Image.new("RGBA", dome.size, (0,0,0,0))
    masked.paste(canvas, (0,0), alpha)

    masked.alpha_composite(dome)

    return masked


@app.route("/", methods=["GET","POST"])
def home():
    qr_b64 = card_b64 = dome_b64 = None

    if request.method == "POST":
        data = request.form.get("data")
        art = fetch_uploaded_image(request.files.get("artfile"))

        if data:
            qr = generate_branded_qr(data, art)
            qr_b64 = image_to_base64(qr)

            card = create_card_mockup(qr)
            dome = create_dome_mockup(qr)

            card_b64 = image_to_base64(card)
            dome_b64 = image_to_base64(dome)

    return render_page(qr_b64, card_b64, dome_b64)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
