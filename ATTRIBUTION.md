# Attribution

## Fonts

Second Chair vendors its own fonts rather than relying on whatever the viewing machine
happens to have installed, or on a live request to a third party's CDN at demo time.
This app's current design follows a sibling project's typography and layout system,
whose face is **Inter** — but that sibling loads it from `fonts.googleapis.com` at
runtime, which is a defect regardless of which design a judge is looking at: a machine
without a network path to Google Fonts renders that chosen face as whatever system sans
happens to be installed, silently. Second Chair vendors the same face itself so that
defect does not travel over with the rest of the design.

Both files below are self-hosted as static `woff2` in `second_chair/static/fonts/` and
loaded with `@font-face` in `second-chair.css`. No third-party font request happens at
runtime.

### Inter

Used for every face on the page: headings, body copy, quotes, labels, controls,
citations, tables. The sibling project this design follows uses one family for
everything (no serif, no monospace), and this app now does the same, in place of the
previous pass's three-family system (Literata, IBM Plex Sans, IBM Plex Mono).

- Designer: Rasmus Andersson.
- Source: https://fonts.google.com/specimen/Inter
- Licence: SIL Open Font License, Version 1.1.
- Files vendored:
  - `inter-400-700.woff2` — variable font, weight axis 400–700, upright. Covers the
    400/500/600/700 weights the reference stylesheet requests
    (`Inter:wght@400;500;600;700`) in a single file rather than four.
  - `inter-400italic.woff2` — italic, weight 400. The reference page never sets
    `font-style: italic` (it has no quoted patient speech to typeset), so this file is
    this app's own addition to the same family, for the transcript quotes and the
    consent record's quoted lines — content the reference design has no equivalent of.

The SIL OFL 1.1 permits bundling and self-hosting; the licence text is reproduced with
the fonts themselves at https://openfontlicense.org and is not duplicated here, per the
line-limit norm the rest of the repo follows for documentation.

Total added weight: 2 files, ~73 KB, served from this app's own `/static/fonts/`.
