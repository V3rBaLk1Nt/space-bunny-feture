#!/usr/bin/env bash
#
# make_cyber_background.sh - build soulseek_cyber_background.jpg/.png from
# the existing assets in this directory (NO external downloads, NO network).
#
# Outputs (1280x800, sRGB):
#   soulseek_cyber_background.jpg/.png        primary (futuristic hands)
#   soulseek_cyber_background_dark.jpg/.png   different variant (dark silhouette hands)
#
# Requires: ImageMagick 7 ("magick") + a mono font (default Adwaita-Mono).
# Depends only on:
#   michelangelo_hands_futuristic.jpg  futuristic hands vector
#   michelangelo_hands_dark.jpg       dark Creation-of-Adam hands silhouette
#   pcb_cc0.jpg                       circuit-board texture
set -euo pipefail

HERE="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
cd "$HERE"

W=1280
H=800
MONO_FONT="${CYBER_FONT:-Adwaita-Mono}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

build_variant() {
    local hands="$1" out_jpg="$2" out_png="$3"
    local rain="$WORK/rain_$(basename "$out_jpg" .jpg).mvg"

    echo "-> building variant ($hands) -> $out_jpg / $out_png"

    # 1) near-black green base gradient
    magick -size "${W}x${H}" gradient:'#010302'-'#05130b' "$WORK/base.png"

    # 2) darkened, desaturated PCB texture pinned to the canvas
    magick pcb_cc0.jpg \
        -resize "${W}x${H}^" -gravity center -extent "${W}x${H}" \
        -modulate 60,40,110 "$WORK/pcb_dark.png"

    # 3) hands -> gold glowing overlay (luminance mask)
    magick "$hands" \
        -resize "${W}x${H}^" -gravity center -extent "${W}x${H}" \
        -colorspace Gray -level 4%,72% \
        \( -clone 0 -alpha extract -negate \) -alpha off -compose CopyOpacity -composite \
        -colorspace sRGB -fill '#e8c14a' -colorize 100 \
        -channel A -evaluate multiply 0.85 +channel \
        "$WORK/hands_gold.png"

    # 4) gold/green binary digital rain (MVG directives via stdlib python)
    python3 - "$W" "$H" "$((RANDOM % 100))" > "$rain" <<'PY'
import random, sys
W, H, seed = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
rng = random.Random(seed)
GOLD, GREEN = (255, 214, 71), (80, 255, 130)
points, out, colw = 15, [], 26
for x in range(rng.randint(0, colw), W, colw + rng.randint(3, 12)):
    start, length = rng.randint(-60, H), rng.randint(6, 22)
    rgb = GREEN if rng.random() < 0.22 else GOLD
    for i in range(length):
        y = start + i * points
        if not (0 <= y <= H):
            continue
        alpha = max(0.05, min(0.95, (0.9 - 0.85 * (i / max(1, length))) * rng.uniform(0.55, 1.15)))
        col = " ".join(str(int(c * alpha + (1 - alpha) * 6)) for c in rgb)
        out.append(f"fill 'rgba({col})'")
        out.append(f"text {x},{y} '{rng.choice('01')}'")
sys.stdout.write("\n".join(out) + "\n")
PY

    magick -size "${W}x${H}" xc:none -font "$MONO_FONT" -pointsize 15 \
        -draw @"$rain" -blur 0x1.2 \
        -channel A -evaluate multiply 3.5 +channel "$WORK/rain.png"

    # 5) composite: base+(pcb screen) -> hands gold overlay -> rain -> green glow
    magick "$WORK/base.png" \
        \( "$WORK/pcb_dark.png" -channel RGB -evaluate multiply 0.55 \) \
        -compose screen -composite "$WORK/bg1.png"

    magick "$WORK/bg1.png" "$WORK/hands_gold.png" \
        -gravity center -geometry +0-10 -compose screen -composite "$WORK/bg2.png"

    magick "$WORK/bg2.png" "$WORK/rain.png" -compose screen -composite "$WORK/bg3.png"

    magick -size "${W}x${H}" radial-gradient:'#0a3d1f'-'#000000' \
        -channel RGB -evaluate multiply 0.55 +channel "$WORK/vignette.png"

    magick "$WORK/bg3.png" "$WORK/vignette.png" -compose screen -composite \
        -channel RGB -evaluate multiply 0.9 +channel "$WORK/bg4.png"

    # 6) write deliverables
    magick "$WORK/bg4.png" -quality 88 "$out_jpg"
    magick "$WORK/bg4.png" "$out_png"

    magick "$out_jpg" -format "written: %w x %h type=%[type]\n" info:
}

build_variant "michelangelo_hands_futuristic.jpg" \
    "$HERE/soulseek_cyber_background.jpg" "$HERE/soulseek_cyber_background.png"
build_variant "michelangelo_hands_dark.jpg" \
    "$HERE/soulseek_cyber_background_dark.jpg" "$HERE/soulseek_cyber_background_dark.png"

echo "-> done: soulseek_cyber_background(.jpg/.png) + soulseek_cyber_background_dark(.jpg/.png)"