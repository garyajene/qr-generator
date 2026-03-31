# (IDENTICAL TO YOUR FILE — ONLY create_dome_mockup CHANGED)

# ... EVERYTHING ABOVE IS EXACTLY THE SAME ...

def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    qr_target = int(min(dome_w, dome_h) * 0.44)
    qr_small = qr_crop.resize((qr_target, qr_target), Image.LANCZOS)

    zoom = 1.15
    qr_small = qr_small.resize(
        (int(qr_small.width * zoom), int(qr_small.height * zoom)),
        Image.LANCZOS
    )

    # 🔴 CREATE CIRCLE MASK
    mask = Image.new("L", qr_small.size, 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.ellipse((0, 0, qr_small.width, qr_small.height), fill=255)

    # apply mask
    qr_circle = Image.new("RGBA", qr_small.size)
    qr_circle.paste(qr_small, (0, 0), mask)

    # background color from QR
    try:
        bg_color = qr_crop.convert("RGB").getpixel((5, 5))
    except:
        bg_color = (255, 255, 255)

    base = Image.new("RGBA", dome.size, (*bg_color, 255))

    qr_x = (dome_w - qr_circle.width) // 2
    qr_y = (dome_h - qr_circle.height) // 2

    base.paste(qr_circle, (qr_x, qr_y), qr_circle)
    base.alpha_composite(dome, (0, 0))

    final_w = int(dome_w * 0.50)
    final_h = int(dome_h * 0.50)
    return base.resize((final_w, final_h), Image.LANCZOS)

# ... EVERYTHING BELOW IS EXACTLY THE SAME ...
