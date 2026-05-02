from flask import Flask, request, redirect
import base64

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def qr():
    qr_html = ""

    if request.method == "POST":
        data = request.form.get("data")

        # Dummy QR (your real QR logic stays in your version)
        qr_img = "https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=" + data

        qr_html = f"""
        <h3>Generated QR</h3>
        <img src="{qr_img}" style="width:200px;">
        <br><br>
        <a href="/buttn/edit/test" style="
            display:inline-block;
            padding:12px 20px;
            background:black;
            color:white;
            border-radius:10px;
            text-decoration:none;
        ">
            Continue to BUTTN Setup
        </a>
        """

    return f"""
    <h1>QR Generator</h1>
    <form method="POST">
        <input name="data" placeholder="Enter URL" required>
        <button type="submit">Generate</button>
    </form>
    {qr_html}
    """


@app.route("/buttn/edit/test")
def edit():

    return """
    <h1>Create Your BUTTN Profile</h1>

    <div style="display:flex; gap:40px;">

        <!-- LEFT SIDE -->
        <div style="width:400px;">

            Name<br>
            <input id="name" style="width:100%"><br><br>

            Title<br>
            <input id="title" style="width:100%"><br><br>

            Header Color<br>
            <input type="color" id="color" value="#68cce2"><br><br>

            Logo<br>
            <input type="file" id="logo"><br><br>

        </div>

        <!-- RIGHT SIDE (LIVE PREVIEW) -->
        <div style="
            width:300px;
            border-radius:20px;
            overflow:hidden;
            background:#fff;
            box-shadow:0 10px 30px rgba(0,0,0,0.1);
        ">

            <div id="previewHeader" style="
                height:150px;
                background:#68cce2;
                display:flex;
                align-items:center;
                justify-content:center;
            ">
                <img id="previewLogo" style="
                    width:90px;
                    height:90px;
                    border-radius:50%;
                    background:#fff;
                ">
            </div>

            <div style="padding:20px; text-align:center;">
                <h2 id="previewName">Your Name</h2>
                <p id="previewTitle">Your Title</p>
            </div>

        </div>

    </div>

    <script>

    function updatePreview() {

        document.getElementById("previewName").innerText =
            document.getElementById("name").value;

        document.getElementById("previewTitle").innerText =
            document.getElementById("title").value;

        document.getElementById("previewHeader").style.background =
            document.getElementById("color").value;
    }

    document.getElementById("name").addEventListener("input", updatePreview);
    document.getElementById("title").addEventListener("input", updatePreview);
    document.getElementById("color").addEventListener("input", updatePreview);

    document.getElementById("logo").addEventListener("change", function() {
        const file = this.files[0];
        if (file) {
            const reader = new FileReader();
            reader.onload = function(e) {
                document.getElementById("previewLogo").src = e.target.result;
            };
            reader.readAsDataURL(file);
        }
    });

    </script>
    """


@app.route("/buttn/test")
def preview():
    return "<h1>Public Profile Page</h1>"


if __name__ == "__main__":
    app.run(debug=True)
