# SixPts design system

One identity, two surfaces (landing page and product). Everything reads from `web/src/tokens.css`.

## Identity
A stadium scoreboard: deep blue-black, field-paint white, one incandescent amber. The amber marks the
single most important thing on a screen — the brand mark, the active nav item, a Bet, the best price
you've selected — and nothing else. Dark only; a light variant would read as a different product.

## Colour
| Token | Use |
|---|---|
| `--ink` `--surface` `--raise` `--raise-2` | surfaces, back to front |
| `--line` `--line-strong` | hairlines; the strong one only on hover and menus |
| `--paint` `--dim` `--faint` | text, three levels |
| `--bulb` | brand and the one thing that matters on screen |
| `--gain` `--loss` | **meaning only** — a price is good or bad. Never decoration. |
| `--warn` | something needs checking (injury banner, hot streak) |

Green is reserved for positive edge and favourable matchups, red for negative. That is why amber is the
brand: it leaves green and red free to carry information.

## Type
`Archivo` (variable, width 118) for anything that behaves like a scoreboard — headings, the wordmark,
probabilities, prices, tier badges. `Inter` for everything you read as prose. Tabular numerals
everywhere numbers stack in a column.

## Motion
Three durations, one easing. Motion is only allowed to do one of four jobs:

| Token | Job | Where |
|---|---|---|
| `--quick` 120ms | answer something you just did | hovers, toggles, focus |
| `--settle` 260ms | content arriving | `rise` on panels, cards, first rows of a table |
| `--tell` 900ms | look here, this changed in the real world | `flash` when a price moves, `land` when a touchdown lands |
| — | waiting | `sweep` skeleton rows instead of the word "Loading" |

Rows stagger only across the first seven, then hold flat, so a 300-row table settles instead of
performing. `prefers-reduced-motion` reduces every duration to zero at the token level, so no component
has to remember.

## Rules
- A new colour needs a reason that isn't aesthetic. Add a token or use an existing one.
- If an animation doesn't answer an action, announce arrival, or report a real-world change, cut it.
- Empty states say what to do next; they never apologise.
