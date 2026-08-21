#!/usr/bin/env python3
"""Call a compatible GPT Image relay and save one poster image.

The script intentionally uses the standard library so it can be copied with the
skill. Credentials are read from the environment and are never included in the
request preview, metadata, or error messages.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import os
from pathlib import Path
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Iterable, List, NoReturn, Optional, Tuple


DEFAULT_BASE_URL = "https://api1.feizhiyx.com/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "high"
DEFAULT_FORMAT = "png"
DEFAULT_RETRIES = 0
MAX_REFERENCE_BYTES = 50 * 1024 * 1024

# Prefer skill-specific settings, then common OpenAI-compatible settings, and
# finally the generic names some local shells use. Empty variables are ignored.
BASE_URL_ENV_NAMES = (
    "IMAGE_API_BASE_URL",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_URL",
    "API_URL",
)
MODEL_ENV_NAMES = ("CODEX_IMAGE_MODEL", "IMAGE_MODEL", "OPENAI_IMAGE_MODEL")
API_KEY_ENV_NAMES = (
    "OPENAI_API_KEY",
    "IMAGE_API_KEY",
    "CODEX_IMAGE_API_KEY",
    "API_KEY",
)


def fail(message: str, code: int = 1) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def first_nonempty_env(names: Iterable[str]) -> Tuple[Optional[str], Optional[str]]:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value, name
    return None, None


def resolve_base_url(explicit: Optional[str]) -> Tuple[str, str]:
    if explicit:
        return explicit, "--base-url"
    value, source = first_nonempty_env(BASE_URL_ENV_NAMES)
    if value is not None and source is not None:
        return value, source
    return DEFAULT_BASE_URL, "skill-default"


def resolve_model(explicit: Optional[str]) -> Tuple[str, str]:
    if explicit:
        return explicit, "--model"
    value, source = first_nonempty_env(MODEL_ENV_NAMES)
    if value is not None and source is not None:
        return value, source
    return DEFAULT_MODEL, "skill-default"


def resolve_api_key_env(explicit: Optional[str]) -> Tuple[str, str]:
    selector = explicit or os.getenv("IMAGE_API_KEY_ENV", "").strip()
    if selector:
        return selector, "--api-key-env" if explicit else "IMAGE_API_KEY_ENV"
    _, source = first_nonempty_env(API_KEY_ENV_NAMES)
    if source is not None:
        return source, source
    return API_KEY_ENV_NAMES[0], "no-key-found"


def read_prompt(prompt: Optional[str], prompt_file: Optional[str]) -> str:
    if bool(prompt) == bool(prompt_file):
        fail("provide exactly one of --prompt or --prompt-file")
    if prompt_file:
        path = Path(prompt_file)
        if not path.is_file():
            fail(f"prompt file not found: {path}")
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = (prompt or "").strip()
    if not value:
        fail("prompt cannot be empty")
    return value


def normalize_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        fail("API base URL cannot be empty")
    if re.search(r"/images/(?:edits|generations)$", value):
        fail("--base-url must stop at the API version, for example https://host/v1")
    if not re.search(r"/v1$", value):
        value += "/v1"
    return value


def validate_size(size: str) -> None:
    if size == "auto":
        return
    match = re.fullmatch(r"(\d+)x(\d+)", size)
    if not match:
        fail("--size must be auto or WIDTHxHEIGHT")
    width, height = (int(part) for part in match.groups())
    if width <= 0 or height <= 0:
        fail("--size dimensions must be positive")
    if width % 16 or height % 16:
        fail("gpt-image-2 dimensions must be multiples of 16")
    if max(width, height) > 3840:
        fail("gpt-image-2 dimensions cannot exceed 3840px")
    if max(width, height) / min(width, height) > 3:
        fail("gpt-image-2 aspect ratio cannot exceed 3:1")
    pixels = width * height
    if pixels < 655_360 or pixels > 8_294_400:
        fail("gpt-image-2 output must contain between 655,360 and 8,294,400 pixels")


def requested_dimensions(size: str) -> Optional[Tuple[int, int]]:
    if size == "auto":
        return None
    width, height = (int(part) for part in size.split("x"))
    return width, height


def output_format(path: Path, requested: Optional[str]) -> str:
    value = (requested or path.suffix.lstrip(".") or DEFAULT_FORMAT).lower()
    if value == "jpg":
        value = "jpeg"
    if value not in {"png", "jpeg", "webp"}:
        fail("--output-format must be png, jpeg, or webp")
    return value


def mime_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def multipart_body(
    fields: Dict[str, str], files: Iterable[Tuple[str, Path]],
) -> Tuple[bytes, str]:
    boundary = "----CodexProductPoster" + uuid.uuid4().hex
    chunks: List[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, path in files:
        filename = path.name.replace('"', "")
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {mime_for(path)}\r\n\r\n".encode(),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def scrub(value: str, secret: Optional[str]) -> str:
    if secret:
        value = value.replace(secret, "[REDACTED]")
    return value


def request_bytes(
    url: str,
    body: bytes,
    content_type: str,
    api_key: str,
    timeout: float,
    retries: int,
) -> Tuple[bytes, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": content_type,
        "Accept": "application/json, image/*",
        "User-Agent": "codex-product-poster/1.0",
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            transient = exc.code == 429 or exc.code >= 500
            last_error = RuntimeError(
                f"HTTP {exc.code}: {scrub(response_body[:1200], api_key)}"
            )
            if not transient or attempt >= retries:
                raise last_error
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= retries:
                raise RuntimeError(f"network error: {scrub(str(exc), api_key)}") from exc
        delay = min(10.0, 2.0 ** attempt)
        print(
            f"Transient API failure; retrying in {delay:.1f}s "
            f"({attempt + 1}/{retries})",
            file=sys.stderr,
        )
        time.sleep(delay)
    raise RuntimeError(str(last_error) if last_error else "request failed")


def decode_data_url(value: str) -> bytes:
    if not value.startswith("data:") or "," not in value:
        fail("unsupported image URL format")
    header, payload = value.split(",", 1)
    if ";base64" not in header:
        fail("only base64 data URLs are supported")
    try:
        return base64.b64decode(payload)
    except (ValueError, binascii.Error) as exc:
        fail(f"invalid base64 image data: {exc}")


def download_image(url: str, timeout: float) -> bytes:
    if url.startswith("data:"):
        return decode_data_url(url)
    request = urllib.request.Request(
        url,
        headers={"Accept": "image/*", "User-Agent": "codex-product-poster/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as exc:
        fail(f"could not download returned image URL: {exc}")


def response_image(payload: bytes, content_type: str, timeout: float) -> bytes:
    if content_type.lower().startswith("image/") or payload.startswith(
        (b"\x89PNG", b"\xff\xd8\xff", b"RIFF")
    ):
        return payload
    try:
        data: Any = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"API returned neither JSON nor an image: {exc}")
    entries = data.get("data") if isinstance(data, dict) else None
    if isinstance(entries, list) and entries:
        item = entries[0]
    elif isinstance(data, dict):
        item = data
    else:
        item = None
    if not isinstance(item, dict):
        fail("API response did not contain an image")
    encoded = item.get("b64_json") or item.get("base64")
    if isinstance(encoded, str):
        try:
            return base64.b64decode(encoded)
        except (ValueError, binascii.Error) as exc:
            fail(f"API returned invalid base64 image data: {exc}")
    url = item.get("url") or item.get("image_url")
    if isinstance(url, str):
        return download_image(url, timeout)
    fail("API response contained no b64_json or url image")


def image_dimensions(raw: bytes) -> Optional[Tuple[int, int]]:
    if raw.startswith(b"\x89PNG") and len(raw) >= 24:
        return struct.unpack(">II", raw[16:24])
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP" and len(raw) >= 30:
        chunk = raw[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(raw[24:27], "little")
            height = 1 + int.from_bytes(raw[27:30], "little")
            return width, height
    if raw.startswith(b"\xff\xd8\xff"):
        index = 2
        while index + 9 < len(raw):
            if raw[index] != 0xFF:
                index += 1
                continue
            marker = raw[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(raw):
                break
            segment_length = int.from_bytes(raw[index : index + 2], "big")
            if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(
                range(0xC9, 0xCC)
            ) | set(range(0xCD, 0xD0)):
                if index + 7 <= len(raw):
                    height = int.from_bytes(raw[index + 3 : index + 5], "big")
                    width = int.from_bytes(raw[index + 5 : index + 7], "big")
                    return width, height
            index += max(segment_length, 2)
    return None


def image_format(raw: bytes) -> Optional[str]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return "webp"
    return None


def normalize_image(raw: bytes, dimensions: Tuple[int, int], fmt: str) -> bytes:
    try:
        from PIL import Image
    except ImportError:
        warn(
            "Pillow is not installed; output normalization may fail. "
            "Install it with `python -m pip install Pillow` to normalize output."
        )
        return raw
    from io import BytesIO

    try:
        image = Image.open(BytesIO(raw))
        image.load()
        if image.size != dimensions:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image = image.resize(dimensions, resampling)
        if fmt == "jpeg" and image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format={"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}[fmt])
        return buffer.getvalue()
    except Exception as exc:  # Pillow can reject unusual relay formats.
        warn(f"could not normalize image with Pillow: {exc}")
        return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate one product poster through a GPT Image-compatible relay"
    )
    parser.add_argument("--reference", help="reference image path; selects /images/edits")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--output", required=True, help="output image path")
    parser.add_argument("--base-url", help="override auto-discovered API base URL")
    parser.add_argument("--model", help="override auto-discovered image model")
    parser.add_argument(
        "--operation",
        choices=("auto", "edit", "generate"),
        default=os.getenv("IMAGE_API_OPERATION", "auto") or "auto",
        help="auto selects edit with --reference and generate without it",
    )
    parser.add_argument(
        "--size",
        default=(os.getenv("IMAGE_SIZE") or os.getenv("OPENAI_IMAGE_SIZE") or DEFAULT_SIZE),
    )
    parser.add_argument(
        "--quality",
        default=(os.getenv("IMAGE_QUALITY") or os.getenv("OPENAI_IMAGE_QUALITY") or DEFAULT_QUALITY),
        choices=("low", "medium", "high", "auto"),
    )
    parser.add_argument(
        "--output-format",
        default=(os.getenv("IMAGE_OUTPUT_FORMAT") or None),
        choices=("png", "jpeg", "jpg", "webp"),
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help="additional retries for transient failures; default 0 avoids duplicate billable POSTs",
    )
    parser.add_argument("--api-key-env", help="name of the environment variable containing the API key")
    parser.add_argument("--normalize-size", action="store_true", help="resize to --size when Pillow is available")
    parser.add_argument(
        "--strict-size",
        action="store_true",
        help="fail when the returned dimensions differ from --size",
    )
    parser.add_argument("--metadata", help="optional JSON metadata path (never contains the API key)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    prompt = read_prompt(args.prompt, args.prompt_file)
    base_url_raw, base_url_source = resolve_base_url(args.base_url)
    model, model_source = resolve_model(args.model)
    api_key_env, api_key_source = resolve_api_key_env(args.api_key_env)
    validate_size(args.size)
    if args.retries < 0 or args.retries > 8:
        fail("--retries must be between 0 and 8")
    if args.timeout <= 0:
        fail("--timeout must be positive")

    output = Path(args.output)
    if output.exists() and not args.force:
        fail(f"output already exists: {output} (use --force to overwrite)")
    output_format_value = output_format(output, args.output_format)
    reference: Optional[Path] = None
    if args.reference:
        reference = Path(args.reference)
        if not reference.is_file():
            fail(f"reference image not found: {reference}")
        if reference.stat().st_size > MAX_REFERENCE_BYTES:
            fail("reference image exceeds the 50MB API limit")

    base_url = normalize_base_url(base_url_raw)
    if args.operation == "edit" and not reference:
        fail("--operation edit requires --reference")
    if args.operation == "generate" and reference:
        fail("--operation generate cannot use --reference; use --operation edit")
    use_edit = args.operation == "edit" or (args.operation == "auto" and reference is not None)
    endpoint = "/images/edits" if use_edit else "/images/generations"
    fields = {
        "model": model,
        "prompt": prompt,
        "size": args.size,
        "quality": args.quality,
        "output_format": output_format_value,
        "n": "1",
    }
    if use_edit:
        if reference is None:  # guarded above; keeps the type checker honest.
            fail("edit operation requires a reference image")
        body, content_type = multipart_body(fields, [("image", reference)])
    else:
        body = json.dumps({**fields, "n": 1}, ensure_ascii=False).encode("utf-8")
        content_type = "application/json"

    if args.dry_run:
        print(
            json.dumps(
                {
                    "endpoint": base_url + endpoint,
                    "operation": "edit" if use_edit else "generate",
                    "model": model,
                    "configuration_sources": {
                        "base_url": base_url_source,
                        "model": model_source,
                        "api_key": api_key_source,
                    },
                    "size": args.size,
                    "quality": args.quality,
                    "output_format": output_format_value,
                    "reference": str(reference) if reference else None,
                    "output": str(output),
                    "prompt": prompt,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        fail(
            f"no API key found; set one of {', '.join(API_KEY_ENV_NAMES)} "
            f"or configure IMAGE_API_KEY_ENV (selected variable: {api_key_env})"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Calling {base_url + endpoint} with model {model} ...", file=sys.stderr)
    try:
        response, content_type_response = request_bytes(
            base_url + endpoint,
            body,
            content_type,
            api_key,
            args.timeout,
            args.retries,
        )
    except RuntimeError as exc:
        fail(str(exc))
    image = response_image(response, content_type_response, args.timeout)
    dimensions = image_dimensions(image)
    actual_format = image_format(image)
    target = requested_dimensions(args.size)
    if args.normalize_size and target:
        image = normalize_image(image, target, output_format_value)
        dimensions = image_dimensions(image) or target
        actual_format = image_format(image) or actual_format
    if not image:
        fail("API returned an empty image")
    if actual_format is None:
        fail("could not identify the returned image format")
    if actual_format != output_format_value:
        fail(
            f"API returned {actual_format} bytes but output format is {output_format_value}; "
            "retry with --normalize-size or use a matching output extension"
        )
    if target and dimensions != target:
        message = (
            f"API returned {dimensions or 'unknown dimensions'}, expected {target}; "
            "install Pillow and use --normalize-size for exact dimensions"
        )
        if args.strict_size:
            fail(message)
        warn(message)
    output.write_bytes(image)
    actual = f"{dimensions[0]}x{dimensions[1]}" if dimensions else "unknown dimensions"
    print(f"Wrote {output} ({actual}).")

    if args.metadata:
        metadata_path = Path(args.metadata)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(
                {
                    "endpoint": base_url + endpoint,
                    "operation": "edit" if use_edit else "generate",
                    "model": model,
                    "configuration_sources": {
                        "base_url": base_url_source,
                        "model": model_source,
                        "api_key": api_key_source,
                    },
                    "size_requested": args.size,
                    "size_actual": list(dimensions) if dimensions else None,
                    "quality": args.quality,
                    "output_format": output_format_value,
                    "reference": str(reference) if reference else None,
                    "output": str(output),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {metadata_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
