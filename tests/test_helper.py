import base64
from contextlib import redirect_stderr
import io
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

        with patch.dict(
            "os.environ",
            {"IMAGE_API_KEY_ENV": "MISSING_KEY", "API_KEY": "fallback-key"},
            clear=True,
        ):
            self.assertEqual(
                generate_poster.resolve_api_key_env(None),
                ("API_KEY", "API_KEY"),
            )

        with patch.dict(
            "os.environ",
            {"CODEX_HOME": str(Path("C:/codex-test-no-config"))},
            clear=True,
        ):
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

    def test_codex_config_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "config.toml").write_text(
                """
model_provider = "custom"

[profiles.alt]
model_provider = "alternate"

[model_providers.custom]
base_url = "https://custom.example/v1"
env_key = "CODEX_TEST_IMAGE_KEY"

[model_providers.alternate]
base_url = "https://alternate.example/v1"
env_key = "CODEX_ALT_IMAGE_KEY"
""".strip(),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(
                json.dumps({"OPENAI_API_KEY": "auth-file-key"}),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "CODEX_HOME": str(home),
                    "CODEX_TEST_IMAGE_KEY": "provider-env-key",
                },
                clear=True,
            ):
                settings = generate_poster.discover_codex_settings()
                self.assertEqual(settings.base_url, "https://custom.example/v1")
                self.assertEqual(settings.api_key, "provider-env-key")
                self.assertIn("config.toml", settings.base_url_source)
                self.assertIn("env_key", settings.api_key_source)
                self.assertEqual(
                    generate_poster.resolve_base_url(None, settings),
                    ("https://custom.example/v1", settings.base_url_source),
                )
                self.assertEqual(
                    generate_poster.resolve_api_key(None, settings),
                    ("provider-env-key", settings.api_key_source),
                )

            with patch.dict(
                "os.environ",
                {"CODEX_HOME": str(home), "CODEX_PROFILE": "alt"},
                clear=True,
            ):
                settings = generate_poster.discover_codex_settings()
                self.assertEqual(settings.base_url, "https://alternate.example/v1")
                self.assertEqual(settings.api_key, "auth-file-key")
                self.assertIn("codex-auth", settings.api_key_source)

            with patch.dict("sys.modules", {"tomllib": None}):
                fallback_config = generate_poster._load_codex_toml(home / "config.toml")
                self.assertEqual(fallback_config["model_provider"], "custom")
                self.assertEqual(
                    fallback_config["model_providers"]["custom"]["base_url"],
                    "https://custom.example/v1",
                )

    def test_codex_config_does_not_override_explicit_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "config.toml").write_text(
                """
model_provider = "custom"
[model_providers.custom]
base_url = "https://codex.example/v1"
env_key = "CODEX_TEST_IMAGE_KEY"
""".strip(),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "CODEX_HOME": str(home),
                    "IMAGE_API_BASE_URL": "https://env.example/v1",
                    "CODEX_TEST_IMAGE_KEY": "codex-provider-key",
                    "OPENAI_API_KEY": "generic-env-key",
                },
                clear=True,
            ):
                settings = generate_poster.discover_codex_settings()
                self.assertEqual(
                    generate_poster.resolve_base_url(None, settings),
                    ("https://env.example/v1", "IMAGE_API_BASE_URL"),
                )
                self.assertEqual(
                    generate_poster.resolve_api_key(None, settings),
                    ("codex-provider-key", settings.api_key_source),
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
        self.assertEqual(
            generate_poster.normalize_base_url("https://example.test/api"),
            "https://example.test/api/v1",
        )
        for unsafe in (
            "https://user:secret@example.test/v1",
            "https://example.test/v1?api_key=secret",
            "https://example.test/v1#fragment",
        ):
            with self.assertRaises(SystemExit):
                generate_poster.normalize_base_url(unsafe)

    def test_prompt_size_and_quality_inference(self):
        self.assertEqual(
            generate_poster.infer_size_from_prompt("输出分辨率 2160×3840"),
            ("2160x3840", "prompt"),
        )
        self.assertEqual(
            generate_poster.infer_size_from_prompt("画布 1536*1024"),
            ("1536x1024", "prompt"),
        )
        self.assertEqual(
            generate_poster.infer_quality_from_prompt("质量：高质量"),
            ("high", "prompt"),
        )
        self.assertEqual(
            generate_poster.infer_quality_from_prompt("quality medium"),
            ("medium", "prompt"),
        )
        self.assertEqual(
            generate_poster.infer_quality_from_prompt("高端产品摄影"),
            (generate_poster.DEFAULT_QUALITY, "skill-auto"),
        )

    def test_size_and_quality_precedence(self):
        with patch.dict(
            "os.environ",
            {
                "IMAGE_SIZE": "1024x1024",
                "OPENAI_IMAGE_SIZE": "1536x1024",
                "IMAGE_QUALITY": "low",
                "OPENAI_IMAGE_QUALITY": "medium",
            },
            clear=True,
        ):
            # A one-off prompt request overrides the configured system value.
            self.assertEqual(
                generate_poster.resolve_size(None, "尺寸 2160x3840"),
                ("2160x3840", "prompt"),
            )
            self.assertEqual(
                generate_poster.resolve_quality(None, "质量 high"),
                ("high", "prompt"),
            )
            # Without a prompt value, the current environment is used.
            self.assertEqual(
                generate_poster.resolve_size(None, "方形海报"),
                ("1024x1024", "IMAGE_SIZE"),
            )
            self.assertEqual(
                generate_poster.resolve_quality(None, "方形海报"),
                ("low", "IMAGE_QUALITY"),
            )
            # Explicit flags remain the highest-precedence source.
            self.assertEqual(
                generate_poster.resolve_size("1536x1024", "尺寸 2160x3840"),
                ("1536x1024", "--size"),
            )
            self.assertEqual(
                generate_poster.resolve_quality("medium", "质量 high"),
                ("medium", "--quality"),
            )

            with patch.dict("os.environ", {"IMAGE_QUALITY": "HIGH"}, clear=True):
                self.assertEqual(
                    generate_poster.resolve_quality(None, "方形海报"),
                    ("high", "IMAGE_QUALITY"),
                )

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                generate_poster.resolve_size(None, "没有指定尺寸"),
                (generate_poster.DEFAULT_SIZE, "skill-auto"),
            )
            self.assertEqual(
                generate_poster.resolve_quality(None, "没有指定质量"),
                (generate_poster.DEFAULT_QUALITY, "skill-auto"),
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

    def test_download_error_redacts_returned_url(self):
        signed_url = "https://cdn.example.test/image.png?token=secret-token"
        error = generate_poster.urllib.error.URLError(signed_url)
        output = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=error):
            with redirect_stderr(output):
                with self.assertRaises(SystemExit):
                    generate_poster.download_image(signed_url, 1, "secret-token")
        message = output.getvalue()
        self.assertNotIn(signed_url, message)
        self.assertNotIn("secret-token", message)
        self.assertIn("[REDACTED_URL]", message)


if __name__ == "__main__":
    unittest.main()
