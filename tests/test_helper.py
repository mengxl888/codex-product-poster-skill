import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_poster  # noqa: E402


class HelperTests(unittest.TestCase):
    def test_environment_discovery_and_fallback(self):
        with patch.dict(
            "os.environ",
            {
                "IMAGE_API_BASE_URL": "https://skill.example/v1",
                "OPENAI_BASE_URL": "https://openai.example/v1",
                "OPENAI_API_KEY": "key-one",
                "API_KEY": "key-two",
                "CODEX_IMAGE_MODEL": "custom-image-model",
            },
            clear=True,
        ):
            self.assertEqual(
                generate_poster.resolve_base_url(None),
                ("https://skill.example/v1", "IMAGE_API_BASE_URL"),
            )
            self.assertEqual(
                generate_poster.resolve_model(None),
                ("custom-image-model", "CODEX_IMAGE_MODEL"),
            )
            self.assertEqual(
                generate_poster.resolve_api_key_env(None),
                ("OPENAI_API_KEY", "OPENAI_API_KEY"),
            )

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                generate_poster.resolve_base_url(None),
                (generate_poster.DEFAULT_BASE_URL, "skill-default"),
            )
            self.assertEqual(
                generate_poster.resolve_model(None),
                (generate_poster.DEFAULT_MODEL, "skill-default"),
            )
            self.assertEqual(
                generate_poster.resolve_api_key_env(None),
                ("OPENAI_API_KEY", "no-key-found"),
            )

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
