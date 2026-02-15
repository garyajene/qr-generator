from flask import Flask, request, send_file
import qrcode
from PIL import Image
import io

app = Flask(__name__)

@app.route("/")
def home():
    return """
    <h2>QR Code Generator with Logo</h2>
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

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    if logo_url:
        try:
            import requests
            response = requests.get(logo_url)
            logo = Image.open(io.BytesIO(response.content))

            # Resize logo
            qr_width, qr_height = img.size
            logo_size = qr_width // 4
            logo = logo.resize((logo_size, logo_size))

            # Center logo
            pos = ((qr_width - logo_size) // 2, (qr_height - logo_size) // 2)
            img.paste(logo, pos, mask=logo if logo.mode == "RGBA" else None)
        except:
            pass

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return send_file(buffer, mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
