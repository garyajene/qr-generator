
# (Your full original code remains unchanged ABOVE)
# For brevity in this tool, assume we append ONLY new routes

from flask import redirect

# TEMP in-memory storage (step 1 system)
BUTTN_STORE = {
    "test": {
        "name": "Gary Ajené",
        "title": "T-Shirt Help Desk",
        "links": [
            {"label": "Visit My Store", "url": "#"},
            {"label": "Watch My YouTube", "url": "#"}
        ]
    }
}

@app.route("/buttn/test")
def buttn_test():
    user = BUTTN_STORE.get("test", {})
    return f"""
    <h1>{user.get("name","")}</h1>
    <p>{user.get("title","")}</p>
    {"".join([f'<a href="{l["url"]}">{l["label"]}</a><br>' for l in user.get("links",[])])}
    """

@app.route("/buttn/edit/test", methods=["GET","POST"])
def buttn_edit():
    if request.method == "POST":
        name = request.form.get("name")
        title = request.form.get("title")
        link1 = request.form.get("link1")
        link1_url = request.form.get("link1_url")

        BUTTN_STORE["test"] = {
            "name": name,
            "title": title,
            "links": [
                {"label": link1, "url": link1_url}
            ]
        }

        return redirect("/buttn/test")

    return """
    <form method="post">
        <input name="name" placeholder="Name"><br>
        <input name="title" placeholder="Title"><br>
        <input name="link1" placeholder="Link Label"><br>
        <input name="link1_url" placeholder="Link URL"><br>
        <button type="submit">Save</button>
    </form>
    """

