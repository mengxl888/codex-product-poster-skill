# Codex Product Poster Skill

Reusable Codex instructions and small Python helpers for making product advertising posters from reference images. The default workflow preserves the reference product, asks `gpt-image-2` for a polished composition, and provides a deterministic local fallback for exact Chinese copy.

## Quick start

```powershell
# Uncomment only when no compatible API key is configured in the environment or Codex config.
# $env:OPENAI_API_KEY = "<your-key>"
python scripts/generate_poster.py `
  --reference .\reference.jpg `
  --prompt "Create a premium product poster. Preserve the reference product. Text (verbatim): ..." `
  --output .\outputs\poster.png `
  --normalize-size
```

The helper discovers settings at invocation time. Explicit flags and Skill-specific environment variables take precedence, then the active Codex provider in `CODEX_HOME/config.toml` (or `~/.codex/config.toml`), then generic aliases; if no URL is found it falls back to `https://api1.feizhiyx.com/v1` and `gpt-image-2`. It never contains a built-in API key. See [references/configuration.md](references/configuration.md) and [SKILL.md](SKILL.md) for the full precedence order.

The Codex provider uses `base_url` for the relay URL and `env_key` for the key variable. When that variable is unavailable, the canonical `auth.json` `OPENAI_API_KEY` is used only as a final Codex-config fallback; backup files and logs are never scanned. `CODEX_HOME`, `CODEX_PROFILE`, `CODEX_CONFIG_PATH`, and `CODEX_AUTH_PATH` can point discovery at an alternate Codex setup.

For size and quality, an explicit command-line flag wins, then a value in the prompt (for example, `尺寸 1536x1024，质量 high`), then `IMAGE_SIZE`/`OPENAI_IMAGE_SIZE` or `IMAGE_QUALITY`/`OPENAI_IMAGE_QUALITY` from the environment. If all are omitted, the helper passes `auto`; no fixed dimensions or quality are embedded in the Skill.

When model-rendered Chinese is not exact, use `scripts/overlay_text.py` with a CJK font; see [references/typography.md](references/typography.md).

## Requirements

- Python 3.9+
- An API key in the active environment or Codex configuration (`env_key`/canonical `auth.json`)
- Pillow only when `--normalize-size` is needed

The helper supports both reference-guided edits and new generations, handles base64 or URL image responses, and writes a PNG/JPEG/WEBP artifact without exposing credentials.

## Install as a local skill

Clone this repository into your Codex skills directory as `codex-product-poster` (for example, `$CODEX_HOME/skills/codex-product-poster`). Restart or refresh Codex skill discovery, then invoke it with `$codex-product-poster` or a natural-language product-poster request.

## Checks

```powershell
python -m unittest discover -s tests -v
python "$env:CODEX_HOME\skills\.system\skill-creator\scripts\quick_validate.py" .
```
