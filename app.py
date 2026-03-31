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
h1 {{
 margin-bottom: 24px;
}}
.label {{
 font-weight: bold;
 margin-bottom: 8px;
}}
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
#dropzone.hover {{
 border-color: #000;
}}
#preview {{
 max-width: 260px;
 max-height: 180px;
 display: none;
}}
button {{
 margin-top: 16px;
 padding: 10px 18px;
 font-size: 16px;
 cursor: pointer;
}}
.results {{
 margin-top: 40px;
}}
.result-block {{
 margin-top: 30px;
}}
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
.mockup-card {{
 max-width: 540px;
 height: auto;
 display: block;
 margin-top: 12px;
}}
.mockup-dome {{
 max-width: 200px;
 height: auto;
 display: block;
 margin-top: 12px;
}}
.subhead {{
 font-weight: bold;
 margin-bottom: 8px;
}}
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
 {f'''
 <div class="result-block">
 <h2>Generated QR</h2>
 <img class="generated-qr" src="data:image/png;base64,{qr_img_b64}">
 </div>
 ''' if qr_img_b64 else ''}

 {f'''
 <div class="result-block">
 <h2>Mockups</h2>
 <div class="mockups">
 <div>
 <div class="subhead">Business Card</div>
 <img class="mockup-card" src="data:image/png;base64,{card_mockup_b64}">
 </div>
 <div>
 <div class="subhead">Dome Sticker</div>
 <img class="mockup-dome" src="data:image/png;base64,{dome_mockup_b64}">
 </div>
 </div>
 </div>
 ''' if card_mockup_b64 and dome_mockup_b64 else ''}
</div>

<script>
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("artfile");
const preview = document.getElementById("preview");
const droptext = document.getElementById("droptext");

dropzone.onclick = () => fileInput.click();

fileInput.onchange = () => {{
 const file = fileInput.files[0];
 if (file) {{
 preview.src = URL.createObjectURL(file);
 preview.style.display = "block";
 droptext.style.display = "none";
 }}
}};

dropzone.addEventListener("dragover", e => {{
 e.preventDefault();
 dropzone.classList.add("hover");
}});

dropzone.addEventListener("dragleave", () => {{
 dropzone.classList.remove("hover");
}});

dropzone.addEventListener("drop", e => {{
 e.preventDefault();
 dropzone.classList.remove("hover");
 if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {{
 fileInput.files = e.dataTransfer.files;
 const file = e.dataTransfer.files[0];
 preview.src = URL.createObjectURL(file);
 preview.style.display = "block";
 droptext.style.display = "none";
 }}
}});
</script>
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


# ---------------------------
# QR ENGINE (UNCHANGED)
# ---------------------------

def choose_background_color(img):
    if not img:
        return (255, 255, 255)
    stat = ImageStat.Stat(img.convert("RGB"))
    return tuple(int(c) for c in stat.mean)


def generate_branded_qr(data, art=None):
    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = [[bool(v) for v in row] for row in qr.matrix]
    n = len(matrix)

    bg_color = choose_background_color(art) if art else (255, 255, 255)

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


# ---------------------------
# MOCKUPS
# ---------------------------

def trim_qr_for_mockup(img):
    crop_px = max(1, (QUIET * BOX) // 2)
    return img.crop((crop_px, crop_px, img.width - crop_px, img.height - crop_px))


def create_card_mockup(qr_img):
    card = Image.open("static/blackcard.png").convert("RGBA")
    qr_crop = trim_qr_for_mockup(qr_img)

    qr_small = qr_crop.resize((int(card.width * 0.32), int(card.width * 0.32)))
    card.paste(qr_small, (card.width - qr_small.width - 20, card.height - qr_small.height - 20), qr_small)

    return card


# ---------------------------
# 🔥 FIXED DOME FUNCTION
# ---------------------------

def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    # 🔴 Extract background color from QR
    bg_color = qr_crop.getpixel((5, 5))[:3]

    # 🔴 Create FULL background (this is the fix)
    base = Image.new("RGBA", dome.size, (*bg_color, 255))

    qr_target = int(min(dome_w, dome_h) * 0.44)
    qr_small = qr_crop.resize((qr_target, qr_target), Image.LANCZOS)

    zoom = 1.15
    qr_small = qr_small.resize(
        (int(qr_small.width * zoom), int(qr_small.height * zoom)),
        Image.LANCZOS
    )

    qr_x = (dome_w - qr_small.width) // 2
    qr_y = (dome_h - qr_small.height) // 2

    base.paste(qr_small, (qr_x, qr_y), qr_small)

    # Apply dome overlay
    base.alpha_composite(dome)

    return base.resize((int(dome_w * 0.5), int(dome_h * 0.5)), Image.LANCZOS)


# ---------------------------
# ROUTE
# ---------------------------

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

            card_mockup = create_card_mockup(qr_img)
            dome_mockup = create_dome_mockup(qr_img)

            card_mockup_b64 = image_to_base64(card_mockup)
            dome_mockup_b64 = image_to_base64(dome_mockup)

    return render_page(
        qr_img_b64=qr_b64,
        card_mockup_b64=card_mockup_b64,
        dome_mockup_b64=dome_mockup_b64,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
