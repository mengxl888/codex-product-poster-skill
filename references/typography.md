# Exact Copy Fallback

Image models can approximate Chinese lettering even when the prompt says “verbatim”. For legally or commercially important copy, ask the model to leave a clean text-safe band and then overlay the copy locally:

```powershell
python scripts/overlay_text.py `
  --input .\generated-no-text.png `
  --output .\outputs\poster.png `
  --title "飞智 VADER 5 PRO" `
  --subtitle "旗舰级电竞手柄" `
  --tagline "精准操控，决胜每一局" `
  --font "C:\Windows\Fonts\msyh.ttc" `
  --region top --force
```

Install Pillow with `python -m pip install Pillow`. On Windows the helper tries Microsoft YaHei (`msyh.ttc`); on Linux it tries Noto Sans CJK. Set `POSTER_FONT` or pass `--font` when the automatic search does not find a suitable CJK font. Use `--cover` only when the generated safe band contains unwanted model-rendered lettering.
