#!/usr/bin/env python3
"""Call a compatible GPT Image relay and save one poster image.

The script intentionally uses the standard library so it can be copied with the
skill. Credentials are read from the environment and are never included in the
request preview, metadata, or error messages.
"""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
from dataclasses import dataclass
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
from urllib.parse import urlsplit, urlunsplit


DEFAULT_BASE_URL = "https://api1.feizhiyx.com/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "auto"
DEFAULT_FORMAT = "png"
DEFAULT_RETRIES = 0
MAX_REFERENCE_BYTES = 50 * 1024 * 1024

# Prefer skill-specific settings, then common OpenAI-compatible settings, and
# finally the generic names some local shells use. Empty variables are ignored.
BASE_URL_ENV_NAMES = (
    "IMAGE_API_BASE_URL",
    "IMAGE_API_URL",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_URL",
    "API_URL",
)
BASE_URL_SKILL_ENV_NAMES = ("IMAGE_API_BASE_URL", "IMAGE_API_URL")
BASE_URL_GENERIC_ENV_NAMES = (
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
API_KEY_SKILL_ENV_NAMES = ("IMAGE_API_KEY", "CODEX_IMAGE_API_KEY")
API_KEY_GENERIC_ENV_NAMES = ("OPENAI_API_KEY", "API_KEY")
SIZE_ENV_NAMES = ("IMAGE_SIZE", "OPENAI_IMAGE_SIZE")
QUALITY_ENV_NAMES = ("IMAGE_QUALITY", "OPENAI_IMAGE_QUALITY")
QUALITY_VALUES = {"low", "medium", "high", "auto"}
QUALITY_ALIASES = {
    "低": "low",
    "低质量": "low",
    "low": "low",
    "中": "medium",
    "中等": "medium",
    "中等质量": "medium",
    "medium": "medium",
    "高": "high",
    "高质量": "high",
    "高清": "high",
    "high": "high",
    "自动": "auto",
    "auto": "auto",
}
CODEX_CONFIG_PATH_ENV_NAMES = ("CODEX_CONFIG_PATH", "CODEX_CONFIG_FILE")
CODEX_AUTH_PATH_ENV_NAMES = ("CODEX_AUTH_PATH", "CODEX_AUTH_FILE")
CODEX_HOME_ENV = "CODEX_HOME"
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class CodexSettings:
    """Non-secret settings discovered from Codex's own config files."""

    base_url: Optional[str] = None
    base_url_source: Optional[str] = None
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    api_key_source: Optional[str] = None


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


def _codex_file_pairs() -> List[Tuple[Path, Path]]:
    """Return standard Codex config/auth pairs without scanning unrelated files."""
    explicit_config, _ = first_nonempty_env(CODEX_CONFIG_PATH_ENV_NAMES)
    explicit_auth, _ = first_nonempty_env(CODEX_AUTH_PATH_ENV_NAMES)
    homes: List[Path] = []
    configured_home = os.getenv(CODEX_HOME_ENV, "").strip()
    if configured_home:
        homes.append(Path(configured_home).expanduser())
    else:
        for candidate in (
            os.getenv("USERPROFILE", "").strip(),
            os.getenv("HOME", "").strip(),
        ):
            if candidate:
                homes.append(Path(candidate).expanduser() / ".codex")
        try:
            homes.append(Path.home() / ".codex")
        except RuntimeError:
            pass

    pairs: List[Tuple[Path, Path]] = []
    if explicit_config or explicit_auth:
        config_path = Path(explicit_config).expanduser() if explicit_config else None
        auth_path = Path(explicit_auth).expanduser() if explicit_auth else None
        if config_path is None and auth_path is not None:
            config_path = auth_path.parent / "config.toml"
        if auth_path is None and config_path is not None:
            auth_path = config_path.parent / "auth.json"
        if config_path is not None and auth_path is not None:
            pairs.append((config_path, auth_path))
        return pairs

    seen: set[Tuple[str, str]] = set()
    for home in homes:
        config_path = home / "config.toml"
        auth_path = home / "auth.json"
        key = (str(config_path).lower(), str(auth_path).lower())
        if key not in seen:
            seen.add(key)
            pairs.append((config_path, auth_path))
    return pairs


def _toml_scalar(raw: str) -> Any:
    value = raw.split("#", 1)[0].strip()
    if not value:
        return None
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value.strip("\"'")


def _load_codex_toml(path: Path) -> Dict[str, Any]:
    try:
        exists = path.is_file()
    except OSError:
        return {}
    if not exists:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    try:
        import tomllib

        parsed = tomllib.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (ImportError, ValueError, TypeError):
        # Python 3.9 has no tomllib. The fallback covers the Codex provider
        # fields while keeping the helper dependency-free.
        parsed: Dict[str, Any] = {}
        section: Optional[Dict[str, Any]] = parsed
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                name = line[1:-1].strip()
                section = parsed
                if name == "model_providers":
                    section = parsed.setdefault("model_providers", {})
                elif name.startswith("model_providers."):
                    provider_name = name.split(".", 1)[1].strip("\"'")
                    providers = parsed.setdefault("model_providers", {})
                    section = providers.setdefault(provider_name, {})
                elif name.startswith("profiles."):
                    profile_name = name.split(".", 1)[1].strip("\"'")
                    profiles = parsed.setdefault("profiles", {})
                    section = profiles.setdefault(profile_name, {})
                else:
                    # Ignore unrelated sections so profile-local keys cannot
                    # overwrite the top-level active provider in the fallback.
                    section = None
                continue
            match = re.match(r"^([A-Za-z0-9_.-]+)\s*=\s*(.+)$", line)
            if match and section is not None:
                section[match.group(1)] = _toml_scalar(match.group(2))
        return parsed


def _load_codex_auth(path: Path) -> Dict[str, Any]:
    try:
        exists = path.is_file()
    except OSError:
        return {}
    if not exists:
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _mapping_value(mapping: Dict[str, Any], names: Iterable[str]) -> Any:
    wanted = {name.casefold() for name in names}
    for key, value in mapping.items():
        if isinstance(key, str) and key.casefold() in wanted:
            return value
    return None


def _selected_codex_provider(config: Dict[str, Any]) -> Dict[str, Any]:
    profile_name = os.getenv("CODEX_PROFILE", "").strip() or _mapping_value(
        config, ("profile",)
    )
    profile: Dict[str, Any] = {}
    profiles = _mapping_value(config, ("profiles",))
    if isinstance(profiles, dict) and isinstance(profile_name, str):
        candidate = _mapping_value(profiles, (profile_name,))
        if isinstance(candidate, dict):
            profile = candidate
    provider_name = _mapping_value(profile, ("model_provider",)) or _mapping_value(
        config, ("model_provider",)
    )
    providers = _mapping_value(config, ("model_providers",))
    if not isinstance(providers, dict) or not isinstance(provider_name, str):
        return {}
    provider = _mapping_value(providers, (provider_name,))
    return provider if isinstance(provider, dict) else {}


def _auth_value(auth: Dict[str, Any], names: Iterable[str]) -> Optional[str]:
    wanted = {name.casefold() for name in names}
    for key, value in auth.items():
        if isinstance(key, str) and key.casefold() in wanted and isinstance(value, str):
            value = value.strip()
            if value:
                return value
    return None


def _codex_auth_allowed(provider: Dict[str, Any], env_key: Optional[str]) -> bool:
    if env_key:
        return True
    requires_auth = _mapping_value(provider, ("requires_openai_auth",))
    if requires_auth is False:
        return False
    return not (isinstance(requires_auth, str) and requires_auth.casefold() == "false")


def discover_codex_settings() -> CodexSettings:
    """Read the active Codex provider URL and its key without exposing secrets."""
    discovered_url: Optional[str] = None
    discovered_url_source: Optional[str] = None
    discovered_key: Optional[str] = None
    discovered_key_env: Optional[str] = None
    discovered_key_source: Optional[str] = None
    fallback_auth: Optional[Tuple[Path, Dict[str, Any]]] = None
    for config_path, auth_path in _codex_file_pairs():
        config = _load_codex_toml(config_path)
        provider = _selected_codex_provider(config)
        if not provider:
            # A few older Codex config snapshots put provider fields at the
            # root. Accept that shape only at the explicitly discovered path.
            provider = config
        auth = _load_codex_auth(auth_path) if discovered_key is None else {}
        if discovered_url is None:
            for field in ("base_url", "api_url", "url", "endpoint"):
                candidate = _mapping_value(provider, (field,)) or _mapping_value(
                    config, (field,)
                )
                if isinstance(candidate, str) and candidate.strip():
                    discovered_url = candidate.strip()
                    discovered_url_source = f"codex-config:{config_path}:{field}"
                    break

        env_key = _mapping_value(provider, ("env_key", "api_key_env", "key_env"))
        if isinstance(env_key, str):
            env_key = env_key.strip()
            if not ENV_NAME_PATTERN.fullmatch(env_key):
                env_key = None
        else:
            env_key = None
        allow_auth = _codex_auth_allowed(provider, env_key)
        if auth and allow_auth and fallback_auth is None:
            fallback_auth = (auth_path, auth)
        if discovered_key is None and env_key:
            env_value = os.getenv(env_key, "").strip()
            if env_value:
                discovered_key = env_value
                discovered_key_env = env_key
                discovered_key_source = f"codex-config:{config_path}:env_key"

        if discovered_key is None:
            direct_key = _mapping_value(provider, ("api_key", "apiKey"))
            if isinstance(direct_key, str) and direct_key.strip():
                discovered_key = direct_key.strip()
                discovered_key_env = None
                discovered_key_source = f"codex-config:{config_path}:api_key"

        if discovered_key is None and allow_auth:
            auth_names = [
                name for name in (env_key, "OPENAI_API_KEY") if name
            ]
            auth_value = _auth_value(auth, auth_names)
            if auth_value:
                discovered_key = auth_value
                discovered_key_env = None
                discovered_key_source = f"codex-auth:{auth_path}:OPENAI_API_KEY"

        if discovered_url is not None and discovered_key is not None:
            break

    if discovered_key is None and fallback_auth is not None:
        auth_path, auth = fallback_auth
        auth_value = _auth_value(auth, ("OPENAI_API_KEY",))
        if auth_value:
            discovered_key = auth_value
            discovered_key_env = None
            discovered_key_source = f"codex-auth:{auth_path}:OPENAI_API_KEY"

    return CodexSettings(
        base_url=discovered_url,
        base_url_source=discovered_url_source,
        api_key=discovered_key,
        api_key_env=discovered_key_env,
        api_key_source=discovered_key_source,
    )


def resolve_base_url(
    explicit: Optional[str], codex: Optional[CodexSettings] = None
) -> Tuple[str, str]:
    if explicit:
        return explicit, "--base-url"
    value, source = first_nonempty_env(BASE_URL_SKILL_ENV_NAMES)
    if value is not None and source is not None:
        return value, source
    codex = codex or discover_codex_settings()
    if codex.base_url and codex.base_url_source:
        return codex.base_url, codex.base_url_source
    value, source = first_nonempty_env(BASE_URL_GENERIC_ENV_NAMES)
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


def infer_size_from_prompt(prompt: str) -> Tuple[str, str]:
    """Extract an explicit WIDTHxHEIGHT request, otherwise leave sizing to API auto."""
    labeled = re.search(
        r"(?:size|尺寸|分辨率|画布|输出(?:尺寸|分辨率))\s*"
        r"(?:is|为|是|设为|设置为|要求|要求为|:|：|=)?\s*"
        r"(\d{3,4})\s*[x×*]\s*(\d{3,4})",
        prompt,
        re.IGNORECASE,
    )
    match = labeled or re.search(
        r"(?<!\d)(\d{3,4})\s*[x×*]\s*(\d{3,4})(?!\d)",
        prompt,
        re.IGNORECASE,
    )
    if match:
        return f"{int(match.group(1))}x{int(match.group(2))}", "prompt"
    return DEFAULT_SIZE, "skill-auto"


def normalize_size_value(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower().replace("×", "x").replace("*", "x"))


def normalize_quality_value(value: str) -> str:
    cleaned = value.strip().lower()
    return QUALITY_ALIASES.get(cleaned, cleaned)


def infer_quality_from_prompt(prompt: str) -> Tuple[str, str]:
    """Extract an explicit quality request without treating generic praise as quality."""
    match = re.search(
        r"(?:quality|质量|画质|输出质量)\s*"
        r"(?:is|为|是|设为|设置为|要求|要求为|:|：|=)?\s*"
        r"(low|medium|high|auto|低质量|中等质量|中等|高质量|高清|低|中|高|自动)",
        prompt,
        re.IGNORECASE,
    )
    if not match:
        return DEFAULT_QUALITY, "skill-auto"
    return normalize_quality_value(match.group(1)), "prompt"


def resolve_size(explicit: Optional[str], prompt: str) -> Tuple[str, str]:
    if explicit:
        return normalize_size_value(explicit), "--size"
    prompt_value, prompt_source = infer_size_from_prompt(prompt)
    if prompt_source == "prompt":
        return prompt_value, prompt_source
    value, source = first_nonempty_env(SIZE_ENV_NAMES)
    if value is not None and source is not None:
        return normalize_size_value(value), source
    return prompt_value, prompt_source


def resolve_quality(explicit: Optional[str], prompt: str) -> Tuple[str, str]:
    if explicit:
        return normalize_quality_value(explicit), "--quality"
    prompt_value, prompt_source = infer_quality_from_prompt(prompt)
    if prompt_source == "prompt":
        return prompt_value, prompt_source
    value, source = first_nonempty_env(QUALITY_ENV_NAMES)
    if value is not None and source is not None:
        return normalize_quality_value(value), source
    return prompt_value, prompt_source


def resolve_api_key_env(
    explicit: Optional[str], codex: Optional[CodexSettings] = None
) -> Tuple[str, str]:
    if explicit:
        return explicit, "--api-key-env"
    selector = os.getenv("IMAGE_API_KEY_ENV", "").strip()
    if selector and os.getenv(selector, "").strip():
        return selector, "IMAGE_API_KEY_ENV"
    _, source = first_nonempty_env(API_KEY_SKILL_ENV_NAMES)
    if source is not None:
        return source, source
    codex = codex or discover_codex_settings()
    if codex.api_key_env and os.getenv(codex.api_key_env, "").strip():
        return codex.api_key_env, codex.api_key_source or "codex-config"
    _, source = first_nonempty_env(API_KEY_GENERIC_ENV_NAMES)
    if source is not None:
        return source, source
    return API_KEY_ENV_NAMES[0], "no-key-found"


def resolve_api_key(
    explicit: Optional[str], codex: Optional[CodexSettings] = None
) -> Tuple[Optional[str], str]:
    """Resolve a key from explicit/env settings, then the active Codex config."""
    if explicit:
        value = os.getenv(explicit, "").strip()
        return (value or None), ("--api-key-env" if value else "no-key-found")
    selector = os.getenv("IMAGE_API_KEY_ENV", "").strip()
    if selector:
        value = os.getenv(selector, "").strip()
        if value:
            return value, "IMAGE_API_KEY_ENV"
    value, source = first_nonempty_env(API_KEY_SKILL_ENV_NAMES)
    if value is not None and source is not None:
        return value, source
    codex = codex or discover_codex_settings()
    if codex.api_key and codex.api_key_source:
        return codex.api_key, codex.api_key_source
    value, source = first_nonempty_env(API_KEY_GENERIC_ENV_NAMES)
    if value is not None and source is not None:
        return value, source
    return None, "no-key-found"


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
    value = raw.strip()
    if not value:
        fail("API base URL cannot be empty")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        fail("API base URL must be an http(s) URL with a hostname")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        fail("API base URL cannot contain userinfo, query parameters, or fragments")
    path = parsed.path.rstrip("/")
    if re.search(r"/images/(?:edits|generations)$", path):
        fail("--base-url must stop at the API version, for example https://host/v1")
    if not path:
        path = "/v1"
    elif not re.search(r"/v1$", path):
        path += "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


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


def download_image(url: str, timeout: float, secret: Optional[str] = None) -> bytes:
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
        reason = getattr(exc, "reason", exc)
        safe_reason = re.sub(r"https?://[^\s)]+", "[REDACTED_URL]", str(reason))
        fail(
            "could not download returned image URL: "
            f"{scrub(safe_reason, secret)[:600]}"
        )


def response_image(
    payload: bytes,
    content_type: str,
    timeout: float,
    secret: Optional[str] = None,
) -> bytes:
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
        return download_image(url, timeout, secret)
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
        help="requested WIDTHxHEIGHT; otherwise infer from the prompt or use auto",
    )
    parser.add_argument(
        "--quality",
        type=normalize_quality_value,
        choices=("low", "medium", "high", "auto"),
        help="requested quality; otherwise infer from the prompt or use auto",
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
    codex_settings = discover_codex_settings()
    base_url_raw, base_url_source = resolve_base_url(args.base_url, codex_settings)
    model, model_source = resolve_model(args.model)
    api_key, api_key_source = resolve_api_key(args.api_key_env, codex_settings)
    size, size_source = resolve_size(args.size, prompt)
    quality, quality_source = resolve_quality(args.quality, prompt)
    if args.operation not in {"auto", "edit", "generate"}:
        fail("--operation must be auto, edit, or generate")
    if quality not in QUALITY_VALUES:
        fail("--quality must be low, medium, high, or auto")
    validate_size(size)
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
        "size": size,
        "quality": quality,
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
                        "size": size_source,
                        "quality": quality_source,
                    },
                    "size": size,
                    "quality": quality,
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

    if not api_key:
        fail(
            f"no API key found; set one of {', '.join(API_KEY_ENV_NAMES)} "
            "or configure IMAGE_API_KEY_ENV/Codex auth"
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
    image = response_image(response, content_type_response, args.timeout, api_key)
    dimensions = image_dimensions(image)
    actual_format = image_format(image)
    target = requested_dimensions(size)
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
                        "size": size_source,
                        "quality": quality_source,
                    },
                    "size_requested": size,
                    "size_actual": list(dimensions) if dimensions else None,
                    "quality": quality,
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
