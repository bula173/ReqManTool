#!/usr/bin/env bash
# Generate icons for Tauri from a source SVG
# Usage: ./generate-icons.sh <source-svg> <output-dir>

set -euo pipefail

SOURCE="${1:-../frontend/public/icon.svg}"
OUTPUT="${2:-icons}"

if [ ! -f "$SOURCE" ]; then
    echo "Source SVG not found: $SOURCE"
    echo "Creating placeholder icons instead..."
    
    mkdir -p "$OUTPUT"
    
    # Create simple placeholder PNGs using ImageMagick if available
    if command -v convert &> /dev/null; then
        for size in 16 32 48 64 128 256 512 1024; do
            convert -size ${size}x${size} xc:#2c3e50 -fill white -gravity center \
                -font DejaVu-Sans -pointsize $((size/2)) \
                -annotate +0+0 "RM" "$OUTPUT/${size}x${size}.png"
        done
        
        # Create ICO
        convert "$OUTPUT/16x16.png" "$OUTPUT/32x32.png" "$OUTPUT/48x48.png" "$OUTPUT/64x64.png" "$OUTPUT/128x128.png" "$OUTPUT/256x256.png" "$OUTPUT/icon.ico"
        
        # Create ICNS (macOS)
        mkdir -p "$OUTPUT/icon.iconset"
        for size in 16 32 64 128 256 512; do
            cp "$OUTPUT/${size}x${size}.png" "$OUTPUT/icon.iconset/icon_${size}x${size}.png"
            if [ $size -le 512 ]; then
                cp "$OUTPUT/$((size*2))x$((size*2)).png" "$OUTPUT/icon.iconset/icon_${size}x${size}@2x.png" 2>/dev/null || true
            fi
        done
        if command -v iconutil &> /dev/null; then
            iconutil -c icns "$OUTPUT/icon.iconset" -o "$OUTPUT/icon.icns"
        fi
        rm -rf "$OUTPUT/icon.iconset"
    else
        echo "ImageMagick not available. Please install it or provide proper icons."
        echo "You can also use: https://icon.kitchen/ or similar tools to generate icons from an SVG."
    fi
else
    echo "Generating icons from $SOURCE..."
    # Use a tool like tauri-icon or similar to generate from SVG
    if command -v tauri-icon &> /dev/null; then
        tauri-icon -i "$SOURCE" -o "$OUTPUT"
    elif command -v cargo &> /dev/null; then
        cargo install tauri-icon --locked
        tauri-icon -i "$SOURCE" -o "$OUTPUT"
    else
        echo "tauri-icon not available. Please install it or generate icons manually."
    fi
fi

echo "Done. Icons in $OUTPUT/"