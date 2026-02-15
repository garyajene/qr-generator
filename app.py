from flask import Flask, request, send_file
import qrcode
from PIL import Image
import requests
import io

app = Flask(__name__)

@app.route("/")
def home():
    return """
    <h2>QR Code Generator with Logo Overlay</h2>
    <form action="/generate" method="get">
        <input type="text" name="data" placeholder="Enter URL or text" required>
        <br><br>
        <input type="text" name="logo" placeholder="Enter logo image URL (optional)">
        <br><br>
        <button type="submit">Generate QR</button>
    </form>
    """

@app.route("/generate")
def generate():
    data = request.args.get("data")
    logo_url = request.args.get("logo")

    if not data:
        return "Missing QR data"

    # Create QR Code
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )

    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    # If logo URL provided, overlay it
    if logo_url:
        try:
            response = requests.get(logo_url, timeout=10)
            response.raise_for_status()

            logo = Image.open(io.BytesIO(response.content)).convert("RGBA")

            qr_width, qr_height = img.size

            # Logo size = 25% of QR width
            logo_size = qr_width // 4
            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)

            # Center position
            pos = (
                (qr_width - logo_size) // 2,
                (qr_height - logo_size) // 2
            )

            img.paste(logo, pos, logo)

        except Exception as e:
            return f"Logo load failed: {str(e)}"

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
