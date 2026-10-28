# Luck Society brand assets

Source: the public @lucksociety profile on X (display name "Luck Society", bio "Sports Betting • Crypto • Stocks • Predictions 🍀"), read 2026-10-28.
- `luck-society-avatar.jpg`: profile image (400x400), a gold coin with a green four-leaf clover. Used as the logo.
- `luck-society-banner.jpg`: profile banner (1500x500), a gold Art-Deco tower with the clover coin and "LUCK SOCIETY" serif caps over a Las Vegas skyline at night. Used as the header background under a dark overlay.

Palette from k-means (k=8, numpy) on each image (pixel share in brackets):

| token | hex | source |
|---|---|---|
| --bg | #01171a | banner, night sky (22.1%) |
| --card | #052220 | banner, dark teal sky (21.1%) |
| --line | #1f341c | banner, dark green (9.4%) |
| --gold | #d79d07 | avatar, coin gold (28.0%) |
| --gold-hi | #f6d12b | avatar, coin highlight (17.6%) |
| --bronze | #a9700a | avatar, coin shadow (5.6%) |
| --clover | #036718 | avatar, clover green (12.2%) |
| --clover-hi | #1b9625 | avatar, clover highlight (3.8%) |
| --black | #040402 | avatar, background (22.4%) |

Functional colors not taken from the images, chosen for WCAG contrast on --bg/--card:
- --ink #efe6cc (warm off-white text)
- --mut #9fb3a8 (muted text)
- --pos #3fbf4f (clover green lightened for text, 7.0:1)
- --neg #ff6b5b (red for negatives)

Headings use a system serif (Georgia) in spaced caps to echo the banner lettering. No font files are bundled.
