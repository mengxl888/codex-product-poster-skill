# Codex Product Poster Skill

Reusable Codex instructions and small Python helpers for making product advertising posters from reference images. The default workflow preserves the reference product, asks `gpt-image-2` for a polished composition, and provides a deterministic local fallback for exact Chinese copy.

## Quick start

```powershell
$env:OPENAI_API_KEY = "<set locally>"
python scripts/generate_poster.py `
  --reference .\reference.jpg `
  --prompt "Create a premium product poster. Preserve the reference product. Text (verbatim): ..." `
  --output .\outputs\poster.png `
  --normalize-size
```

The default relay is `https://api1.feizhiyx.com/v1`; set `IMAGE_API_BASE_URL` to use another compatible relay. No API key is stored by this repository. See [references/configuration.md](references/configuration.md) and [SKILL.md](SKILL.md) for the Codex workflow.

When model-rendered Chinese is not exact, use `scripts/overlay_text.py` with a CJK font; see [references/typography.md](references/typography.md).

## Requirements

- Python 3.9+
- An API key in `OPENAI_API_KEY` (or a variable selected with `IMAGE_API_KEY_ENV`)
- Pillow only when `--normalize-size` is needed

The helper supports both reference-guided edits and new generations, handles base64 or URL image responses, and writes a PNG/JPEG/WEBP artifact without exposing credentials.

## Install as a local skill

Clone this repository into your Codex skills directory as `codex-product-poster` (for example, `$CODEX_HOME/skills/codex-product-poster`). Restart or refresh Codex skill discovery, then invoke it with `$codex-product-poster` or a natural-language product-poster request.

## Checks

```powershell
python -m unittest discover -s tests -v
python "$env:CODEX_HOME\skills\.system\skill-creator\scripts\quick_validate.py" .
```
