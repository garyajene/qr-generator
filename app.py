# (everything above stays EXACTLY the same from your master file)

def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    qr_target = int(min(dome_w, dome_h) * 0.44)
    qr_small = qr_crop.resize((qr_target, qr_target), Image.LANCZOS)

    zoom = 1.15
    zoomed_w = int(qr_small.width * zoom)
    zoomed_h = int(qr_small.height * zoom)
    qr_small = qr_small.resize((zoomed_w, zoomed_h), Image.LANCZOS)

    # 🔥 NEW: get background color FROM FINAL QR (safe)
    small = qr_img.resize((60, 60))
    pixels = list(small.getdata())
    colors = [(r, g, b) for r, g, b, a in pixels if a > 0]

    if colors:
        bg_color = Counter(colors).most_common(1)[0][0]
    else:
        bg_color = (255, 255, 255)

    # 🔥 FIX: use real background color instead of transparent/white
    base = Image.new("RGBA", dome.size, (*bg_color, 255))

    qr_x = (dome_w - qr_small.width) // 2
    qr_y = (dome_h - qr_small.height) // 2

    base.paste(qr_small, (qr_x, qr_y), qr_small)
    base.alpha_composite(dome, (0, 0))

    final_w = int(dome_w * 0.50)
    final_h = int(dome_h * 0.50)
    return base.resize((final_w, final_h), Image.LANCZOS)

# (everything below stays EXACTLY the same)
