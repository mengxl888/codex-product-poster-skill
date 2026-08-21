# Helper Configuration

`scripts/generate_poster.py` uses only the Python standard library for API calls. Size normalization and deterministic text overlay use Pillow.

## Environment

```powershell
$env:OPENAI_API_KEY = "<local secret>"
$env:IMAGE_API_BASE_URL = "https://api1.feizhiyx.com/v1"
```

`IMAGE_API_BASE_URL` may include or omit the `/v1` suffix, but must stop at the API version (not at `/images/edits` or `/images/generations`). The helper normalizes it. `IMAGE_API_KEY_ENV` can name a different environment variable when the local setup uses one.

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

The helper accepts `--model`, `--operation`, `--size`, `--quality`, `--output-format`, `--timeout`, `--retries`, `--strict-size`, and `--force`. It accepts either `--prompt` or `--prompt-file`, never both. A reference image selects `/images/edits`; no reference selects `/images/generations`. Retries default to `0` to avoid unintentionally duplicating a billable POST.

If the relay returns dimensions or a format different from the request, install Pillow in the active environment (`python -m pip install Pillow`) and use `--normalize-size`. Add `--strict-size` when the output must match the requested dimensions exactly; without it, a compatible but different relay size is reported as a warning. Format mismatches always fail rather than writing misleading bytes under the wrong extension. References larger than 50 MB are rejected before upload.
