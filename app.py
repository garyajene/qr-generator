from flask import Flask, request, send_file, render_template_string
import qrcode
import io

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>QR Generator</title>
    <style>
        body { font-family: Arial; text-align: center; margin-top: 60px; }
        input { padding: 10px; width: 300px; }
        button { padding: 10px 20px; margin-left: 10px; }
        img { margin-top: 30px; }
    </style>
</head>
<body>
    <h2>QR Code Generator</h2>
    <form method="GET">
        <input type="text" name="data" placeholder="Enter URL or text" required>
        <button type="submit">Generate</button>
    </form>
    {% if qr %}
        <div>
            <img src="{{ qr }}">
            <br>
            <a href="{{ qr }}" download="qrcode.png">
                <button>Download QR</button>
            </a>
        </div>
    {% endif %}
</body>
</html>
"""

@app.route("/", methods=["GET"])
def home():
    data = request.args.get("data")
    qr_image = None

    if data:
        img = qrcode.make(data)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        encoded = "data:image/png;base64," + buffer.getvalue().encode("base64") if False else None

    if data:
        import base64
        img = qrcode.make(data)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        qr_image = f"data:image/png;base64,{encoded}"

    return render_template_string(HTML, qr=qr_image)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
