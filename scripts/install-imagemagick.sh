#!/bin/sh
set -eu

version=7.1.2-32
sha256=d456cab221b5fc1c396768a026d0a33ee8665f7119c7fea7151b960c69058b21
if [ "$(uname -m)" != x86_64 ]; then
    printf '%s\n' "The pinned official ImageMagick AppImage requires Linux x86_64" >&2
    exit 1
fi
install_dir=$(mktemp -d)
trap 'rm -rf "$install_dir"' EXIT HUP INT TERM

curl --fail --location --retry 3 --output "$install_dir/ImageMagick.AppImage" \
    "https://github.com/ImageMagick/ImageMagick/releases/download/$version/ImageMagick-$version-gcc-x86_64.AppImage"
printf '%s  %s\n' "$sha256" "$install_dir/ImageMagick.AppImage" | sha256sum --check --strict
chmod +x "$install_dir/ImageMagick.AppImage"
cd "$install_dir"
./ImageMagick.AppImage --appimage-extract >/dev/null
mkdir -p /opt/imagemagick
cp -a squashfs-root/. /opt/imagemagick/
mkdir -p /opt/imagemagick/bin
ln -s ../AppRun /opt/imagemagick/bin/magick
printf '%s\n' "Installed official ImageMagick $version GCC x86_64 AppImage (SHA-256 verified)"
