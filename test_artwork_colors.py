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


def finder_layer_points(image):
    matrix_size = image.width // BOX - 2 * QUIET
    starts = ((0, 0), (matrix_size - 7, 0), (0, matrix_size - 7))
    layers = {"backdrop": [], "outer": [], "middle": [], "pupil": []}
    for column, row in starts:
        left = (QUIET + column) * BOX
        top = (QUIET + row) * BOX
        center_y = (QUIET + row) * BOX + 3 * BOX + BOX // 2
        # Stay inside the cleared footprint but well outside the antialiased
        # superellipse edge.
        layers["backdrop"].append((left + 1, top + 1))
        layers["outer"].append((left + BOX // 2, center_y))
        layers["middle"].append((left + BOX + BOX // 2, center_y))
        layers["pupil"].append((left + 3 * BOX + BOX // 2, center_y))
    return layers


def finder_pupil_centers(image):
    return finder_layer_points(image)["pupil"]


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

        points = finder_layer_points(image)
        backdrop_colors = [
            image.getpixel(point)[:3] for point in points["backdrop"]
        ]
        outer_colors = [image.getpixel(point)[:3] for point in points["outer"]]
        middle_colors = [image.getpixel(point)[:3] for point in points["middle"]]
        pupil_colors = [image.getpixel(point)[:3] for point in points["pupil"]]

        self.assertEqual([PURPLE] * 3, backdrop_colors)
        self.assertEqual([(0, 0, 0)] * 3, outer_colors)
        self.assertEqual([(255, 255, 255)] * 3, middle_colors)
        self.assertEqual([ORANGE] * 3, pupil_colors)

    def test_all_diagnostic_versions_use_orange_accent(self):
        art = fedex_style_artwork()
        for variant in QR_DIAGNOSTIC_VARIANTS:
            with self.subTest(variant=variant["key"]):
                image = generate_branded_qr_diagnostic_variant(
                    "https://example.com", art.copy(), variant=variant
                )
                points = finder_layer_points(image)
                backdrop_colors = [
                    image.getpixel(point)[:3] for point in points["backdrop"]
                ]
                outer_colors = [
                    image.getpixel(point)[:3] for point in points["outer"]
                ]
                middle_colors = [
                    image.getpixel(point)[:3] for point in points["middle"]
                ]
                pupil_colors = [
                    image.getpixel(point)[:3] for point in points["pupil"]
                ]
                self.assertEqual([PURPLE] * 3, backdrop_colors)
                self.assertEqual([(0, 0, 0)] * 3, outer_colors)
                self.assertEqual([(255, 255, 255)] * 3, middle_colors)
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
