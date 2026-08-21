# Helper Configuration

`scripts/generate_poster.py` uses only the Python standard library for API calls. Size normalization and deterministic text overlay use Pillow.

## Environment and fallback order

```powershell
# Only set these when the current system environment does not already provide them.
# $env:OPENAI_API_KEY = "<local secret>"
# $env:IMAGE_API_BASE_URL = "https://api1.feizhiyx.com/v1"
```

At every invocation the helper first reads the current process environment. URL precedence is `IMAGE_API_BASE_URL`, `IMAGE_API_URL`, `OPENAI_BASE_URL`, `OPENAI_API_BASE`, `OPENAI_API_URL`, `API_URL`, then the Skill fallback `https://api1.feizhiyx.com/v1`. Key precedence is a non-empty variable named by `IMAGE_API_KEY_ENV` (if set), then the first non-empty variable among `OPENAI_API_KEY`, `IMAGE_API_KEY`, `CODEX_IMAGE_API_KEY`, and `API_KEY`. If the selector is empty or stale, automatic key discovery continues. If no key exists, the helper stops with a setup error; it never contains a built-in secret. Model precedence is `--model`, then `CODEX_IMAGE_MODEL`, `IMAGE_MODEL`, `OPENAI_IMAGE_MODEL`, then `gpt-image-2`.

`IMAGE_API_BASE_URL` may include or omit the `/v1` suffix, but must stop at the API version (not at `/images/edits` or `/images/generations`) and must not contain credentials, query parameters, or fragments. The helper normalizes it. `IMAGE_API_KEY_ENV` can name a different environment variable when the local setup uses one. Command-line flags such as `--base-url`, `--model`, and `--api-key-env` override automatic discovery. `IMAGE_API_OPERATION`, `IMAGE_SIZE`/`OPENAI_IMAGE_SIZE`, `IMAGE_QUALITY`/`OPENAI_IMAGE_QUALITY`, and `IMAGE_OUTPUT_FORMAT` are also recognized when their command-line flags are omitted.

Size and quality use this order: explicit command-line flag, value stated in the prompt, current system environment, then `auto`. The prompt parser recognizes forms such as `尺寸 1536x1024`, `输出分辨率 2160×3840`, `quality high`, and `质量：高`. A prompt value intentionally overrides an environment value so a one-off request can change the system default. Invalid requested dimensions or quality values stop with a validation error instead of silently falling back.

## Common commands

```powershell
# Reference-guided edit
python scripts/generate_poster.py --reference .\reference.jpg `
  --prompt-file .\prompt.txt --output .\outputs\poster.png --normalize-size

# Fresh generation without a reference
python scripts/generate_poster.py --prompt "..." --output .\outputs\poster.png

# Validate the payload without network access
python scripts/generate_poster.py --reference .\reference.jpg `
  --prompt "..." --output .\outputs\poster.png --dry-run
```

The helper accepts `--model`, `--operation`, `--size`, `--quality`, `--output-format`, `--timeout`, `--retries`, `--strict-size`, and `--force`. It accepts either `--prompt` or `--prompt-file`, never both. A reference image selects `/images/edits`; no reference selects `/images/generations`. Retries default to `0` to avoid unintentionally duplicating a billable POST. `--dry-run` prints `configuration_sources` so you can verify which environment variable or fallback was selected without exposing the key.

If the relay returns dimensions or a format different from the request, install Pillow in the active environment (`python -m pip install Pillow`) and use `--normalize-size`. Add `--strict-size` when the output must match the requested dimensions exactly; without it, a compatible but different relay size is reported as a warning. Format mismatches always fail rather than writing misleading bytes under the wrong extension. References larger than 50 MB are rejected before upload.
