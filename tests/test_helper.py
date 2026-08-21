import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_poster  # noqa: E402


class HelperTests(unittest.TestCase):
    def test_base_url_normalization(self):
        self.assertEqual(
            generate_poster.normalize_base_url("https://example.test"),
            "https://example.test/v1",
        )
        self.assertEqual(
            generate_poster.normalize_base_url("https://example.test/v1/"),
            "https://example.test/v1",
        )

    def test_image_headers(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + bytes.fromhex("0000040000000400")
        self.assertEqual(generate_poster.image_dimensions(png), (1024, 1024))
        self.assertEqual(generate_poster.image_format(png), "png")
        self.assertEqual(generate_poster.image_format(b"\xff\xd8\xff"), "jpeg")
        self.assertEqual(generate_poster.image_format(b"RIFF0000WEBP"), "webp")

    def test_multipart_and_base64_response(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.jpg"
            reference.write_bytes(b"reference")
            body, content_type = generate_poster.multipart_body(
                {"model": "gpt-image-2", "prompt": "测试"},
                [("image", reference)],
            )
            self.assertIn(b'name="image"', body)
            self.assertTrue(content_type.startswith("multipart/form-data; boundary="))

        payload = json.dumps(
            {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}
        ).encode()
        self.assertEqual(
            generate_poster.response_image(payload, "application/json", 1),
            b"image-bytes",
        )


if __name__ == "__main__":
    unittest.main()
