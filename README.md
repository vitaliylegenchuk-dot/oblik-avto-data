# oblik-avto-data

Відкриті дані для застосунку «Облік авто».

## fuel.json

Середні ціни на пальне по Україні (грн за літр). Оновлюються щоп'ятниці.

- `updated` — дата, на яку зібрано ціни
- `checked` — коли перевіряли востаннє
- `prices` — поточні ціни: `a95p` (А-95 преміум), `a95`, `a92`, `dp` (дизель), `gas` (автогаз)
- `history` — ціни по тижнях (до 104 записів)
- `source`, `source_url` — звідки взято цифри

Застосунок читає файл за адресою:
https://raw.githubusercontent.com/vitaliylegenchuk-dot/oblik-avto-data/main/fuel.json
