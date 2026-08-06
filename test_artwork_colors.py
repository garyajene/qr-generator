import unittest

from PIL import Image, ImageDraw

from app import (
    BOX,
    QUIET,
    QR_DIAGNOSTIC_VARIANTS,
    choose_finder_pupil_colors,
    generate_branded_qr,
    generate_branded_qr_diagnostic_variant,
)


PURPLE = (77, 39, 132)
ORANGE = (255, 90, 31)


def fedex_style_artwork():
    art = Image.new("RGBA", (300, 300), (*PURPLE, 255))
    draw = ImageDraw.Draw(art)
    draw.rectangle((65, 110, 155, 190), fill=(255, 255, 255, 255))
    draw.rectangle((155, 110, 245, 190), fill=(*ORANGE, 255))
    return art


def finder_pupil_centers(image):
    matrix_size = image.width // BOX - 2 * QUIET
    starts = ((0, 0), (matrix_size - 7, 0), (0, matrix_size - 7))
    return [
        ((QUIET + column) * BOX + 3 * BOX + BOX // 2,
         (QUIET + row) * BOX + 3 * BOX + BOX // 2)
        for column, row in starts
    ]


class ArtworkColorTests(unittest.TestCase):
    def test_chromatic_accents_are_preferred_and_cycled(self):
        art = Image.new("RGBA", (300, 300), (255, 255, 255, 255))
        draw = ImageDraw.Draw(art)
        draw.rectangle((0, 0, 149, 299), fill=(210, 30, 40, 255))
        draw.rectangle((150, 0, 299, 299), fill=(20, 70, 190, 255))

        colors = choose_finder_pupil_colors(art, (255, 255, 255))

        self.assertEqual(3, len(colors))
        self.assertNotEqual(colors[0], colors[1])
        self.assertEqual(colors[0], colors[2])

    def test_dark_production_renderer_uses_orange_accent(self):
        image = generate_branded_qr("https://example.com", fedex_style_artwork())

        pupil_colors = [image.getpixel(point)[:3] for point in finder_pupil_centers(image)]

        self.assertEqual([ORANGE] * 3, pupil_colors)

    def test_all_diagnostic_versions_use_orange_accent(self):
        art = fedex_style_artwork()
        for variant in QR_DIAGNOSTIC_VARIANTS:
            with self.subTest(variant=variant["key"]):
                image = generate_branded_qr_diagnostic_variant(
                    "https://example.com", art.copy(), variant=variant
                )
                pupil_colors = [
                    image.getpixel(point)[:3] for point in finder_pupil_centers(image)
                ]
                self.assertEqual([ORANGE] * 3, pupil_colors)

    def test_monochrome_artwork_uses_safe_fallback(self):
        art = Image.new("RGBA", (300, 300), (255, 255, 255, 255))
        ImageDraw.Draw(art).rectangle((80, 80, 220, 220), fill=(0, 0, 0, 255))

        colors = choose_finder_pupil_colors(
            art, (255, 255, 255), fallback_color=(0, 0, 0)
        )

        self.assertEqual([(0, 0, 0, 255)] * 3, colors)


if __name__ == "__main__":
    unittest.main()
