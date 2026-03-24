from flask import Flask, request
from io import BytesIO
import base64
import requests
from PIL import Image
import segno

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


def generate_qr(data):
    qr = segno.make(data, error='h')
    buffer = BytesIO()
    qr.save(buffer, kind='png', scale=10, border=2)
    buffer.seek(0)
    return Image.open(buffer).convert("RGBA")


def fetch_image(url):
    response = requests.get(url)
    return Image.open(BytesIO(response.content)).convert("RGBA")


def create_mockup(qr_img):
    # Load assets
    card = Image.open("static/blackcard.png").convert("RGBA")
    dome = Image.open("static/dome_piece_2.png").convert("RGBA")

    # Resize QR
    qr_size = 500
    qr_img = qr_img.resize((qr_size, qr_size))

    # Center QR on card
    card_w, card_h = card.size
    qr_x = (card_w - qr_size) // 2
    qr_y = (card_h - qr_size) // 2

    card.paste(qr_img, (qr_x, qr_y), qr_img)

    # Resize dome slightly bigger than QR
    dome = dome.resize((qr_size + 80, qr_size + 80))

    dome_x = qr_x - 40
    dome_y = qr_y - 40

    card.paste(dome, (dome_x, dome_y), dome)

    return card


def image_to_base64(img):
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@app.route("/", methods=["GET", "POST"])
def index():
    qr_b64 = None
    mockup_b64 = None

    if request.method == "POST":
        data = request.form.get("qr_data")
        artwork_url = request.form.get("artwork_url")

        qr_img = generate_qr(data)

        # If artwork URL exists, overlay effect (your existing logic can go here)
        if artwork_url:
            try:
                art = fetch_image(artwork_url)
                art = art.resize(qr_img.size)
                qr_img = Image.alpha_composite(qr_img, art)
            except:
                pass

        qr_b64 = image_to_base64(qr_img)

        # NEW: create mockup
        mockup_img = create_mockup(qr_img)
        mockup_b64 = image_to_base64(mockup_img)

    return f"""
    <html>
    <head>
        <title>QR Generator</title>
        <style>
            body {{ font-family: Arial; padding: 30px; }}
            img {{ margin-top: 20px; max-width: 400px; }}
        </style>
    </head>
    <body>
        <h1>QR Generator</h1>

        <form method="POST">
            <input type="text" name="qr_data" placeholder="Enter QR Data" required><br><br>
            <input type="text" name="artwork_url" placeholder="Artwork URL (optional)"><br><br>
            <button type="submit">Generate</button>
        </form>

        {f"<h2>QR Code</h2><img src='data:image/png;base64,{qr_b64}' />" if qr_b64 else ""}
        
        {f"<h2>Mockup Preview</h2><img src='data:image/png;base64,{mockup_b64}' />" if mockup_b64 else ""}

    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(debug=True)
