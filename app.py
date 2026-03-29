from flask import Flask, request, Response
from io import BytesIO
import base64
from PIL import Image, ImageOps
import segno

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


# -----------------------------
# QR GENERATION
# -----------------------------
def generate_qr(data):
    qr = segno.make(data, error='h')
    buffer = BytesIO()
    qr.save(buffer, kind='png', scale=10, border=4)
    buffer.seek(0)
    return Image.open(buffer).convert("RGBA")


# -----------------------------
# TRIM WHITE (FOR MOCKUPS ONLY)
# -----------------------------
def trim_qr(img):
    bg = Image.new(img.mode, img.size, (255, 255, 255, 0))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()
    if bbox:
        return img.crop(bbox)
    return img


# -----------------------------
# LOAD ASSETS
# -----------------------------
def load_asset(path):
    return Image.open(path).convert("RGBA")


# -----------------------------
# CREATE MOCKUPS
# -----------------------------
def create_mockups(qr_img):
    # Trim white for mockups
    qr_trimmed = ImageOps.crop(qr_img, border=40)

    # ---------- CARD ----------
    card = load_asset("static/blackcard.png")
    card_w, card_h = card.size

    qr_card = qr_trimmed.copy()
    qr_card.thumbnail((card_w * 0.25, card_h * 0.25))

    card_x = int(card_w * 0.70)
    card_y = int(card_h * 0.65)

    card.paste(qr_card, (card_x, card_y), qr_card)

    # ---------- DOME ----------
    dome = load_asset("static/dome_piece1.png")
    dome_w, dome_h = dome.size

    qr_dome = qr_trimmed.copy()

    # Slight zoom for better fill
    zoom_factor = 1.4
    qr_dome = qr_dome.resize(
        (int(qr_dome.width * zoom_factor), int(qr_dome.height * zoom_factor))
    )

    qr_dome.thumbnail((dome_w * 0.7, dome_h * 0.7))

    dome_base = Image.new("RGBA", dome.size, (0, 0, 0, 0))

    dx = (dome_w - qr_dome.width) // 2
    dy = (dome_h - qr_dome.height) // 2

    dome_base.paste(qr_dome, (dx, dy), qr_dome)
    dome_base.paste(dome, (0, 0), dome)

    return card, dome_base


# -----------------------------
# IMAGE TO BASE64
# -----------------------------
def to_base64(img):
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


# -----------------------------
# ROUTE
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    qr_b64 = None
    card_b64 = None
    dome_b64 = None

    if request.method == "POST":
        data = request.form.get("qr_data")

        if data:
            qr_img = generate_qr(data)

            # Optional artwork overlay
            file = request.files.get("file")
            if file:
                try:
                    art = Image.open(file).convert("RGBA")
                    art.thumbnail((qr_img.width * 0.6, qr_img.height * 0.6))

                    ax = (qr_img.width - art.width) // 2
                    ay = (qr_img.height - art.height) // 2

                    qr_img.paste(art, (ax, ay), art)
                except:
                    pass

            card, dome = create_mockups(qr_img)

            qr_b64 = to_base64(qr_img)
            card_b64 = to_base64(card)
            dome_b64 = to_base64(dome)

    return f"""
    <html>
    <head>
        <title>QR Generator</title>
        <style>
            body {{ font-family: Arial; padding: 30px; }}

            .drop-zone {{
                width: 300px;
                height: 150px;
                border: 2px dashed #aaa;
                display: flex;
                align-items: center;
                justify-content: center;
                margin-top: 10px;
            }}

            img {{ max-width: 300px; margin-top: 20px; }}

            .mockups {{
                display: flex;
                gap: 40px;
                margin-top: 20px;
            }}
        </style>
    </head>
    <body>

        <h1>QR Generator</h1>

        <form method="POST" enctype="multipart/form-data">
            <label>QR Data</label><br>
            <input name="qr_data" placeholder="Enter QR Data"><br><br>

            <label>Upload Artwork (optional)</label>
            <div class="drop-zone" id="dropZone">
                Drop Image Here or Click
            </div>
            <input type="file" name="file" id="fileInput" style="display:none;"><br><br>

            <button type="submit">Generate</button>
        </form>

        {"<h2>Generated QR</h2><img src='data:image/png;base64," + qr_b64 + "'>" if qr_b64 else ""}

        {"<h2>Mockups</h2><div class='mockups'>" if card_b64 else ""}
        {f"<div><h4>Business Card</h4><img src='data:image/png;base64,{card_b64}'></div>" if card_b64 else ""}
        {f"<div><h4>Dome Sticker</h4><img src='data:image/png;base64,{dome_b64}'></div>" if dome_b64 else ""}
        {"</div>" if card_b64 else ""}

        <script>
            const dropZone = document.getElementById('dropZone');
            const fileInput = document.getElementById('fileInput');

            dropZone.addEventListener('click', () => fileInput.click());

            dropZone.addEventListener('dragover', (e) => {{
                e.preventDefault();
                dropZone.style.borderColor = 'black';
            }});

            dropZone.addEventListener('dragleave', () => {{
                dropZone.style.borderColor = '#aaa';
            }});

            dropZone.addEventListener('drop', (e) => {{
                e.preventDefault();
                fileInput.files = e.dataTransfer.files;
                dropZone.innerText = fileInput.files[0].name;
            }});
        </script>

    </body>
    </html>
    """


if __name__ == "__main__":
    app.run()
