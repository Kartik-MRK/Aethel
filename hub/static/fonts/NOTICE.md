# Fonts

Both faces are third-party, licensed under the SIL Open Font License 1.1, and
subset for this project by `scripts/build_fonts.py`. The licence texts are
beside this file.

## public-sans.woff2

Public Sans, from the United States Web Design System.
<https://github.com/uswds/public-sans>. Copyright 2015 The Public Sans Project
Authors. Licence: `public-sans-OFL.txt`.

Subset to Latin-1 and Latin Extended-A, weight axis limited to 400-700, and all
OpenType features dropped except `ccmp`, `kern`, `liga`, `locl`, `mark`, `mkmk`
and `tnum`. Public Sans declares no Reserved Font Name, so the subset keeps the
original family name.

## aethel-mono.woff2

Derived from IBM Plex Mono. <https://github.com/IBM/plex>. Copyright 2017 IBM
Corp. with Reserved Font Name "Plex". Licence: `aethel-mono-OFL.txt`.

Subset to ASCII plus a few identifier and numeric marks, weight axis limited to
400-600, and all OpenType features dropped except `ccmp`, `locl`, `mark`, `rvrn`
and `zero`.

**Renamed.** The OFL reserves "Plex" for IBM's own releases, and a subset with a
restricted axis is a modified version, so this file cannot ship under the
original name. The copyright, trademark and licence records inside the font are
unchanged; only the family and style names differ.
