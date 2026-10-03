#!/bin/sh
set -eu

version=7.1.2-32
sha256=abc38dad4adfbab671857822499e69c8745278a914f5d38517baecd400b820c2
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT HUP INT TERM

curl --fail --location --retry 3 --output "$build_dir/source.tar.xz" \
    "https://github.com/ImageMagick/ImageMagick/releases/download/$version/ImageMagick-$version.tar.xz"
printf '%s  %s\n' "$sha256" "$build_dir/source.tar.xz" | sha256sum --check --strict
tar -xJf "$build_dir/source.tar.xz" -C "$build_dir"
cd "$build_dir/ImageMagick-$version"
./configure --prefix=/opt/imagemagick --with-quantum-depth=16 --disable-hdri \
    --disable-docs --disable-pipes --without-magick-plus-plus --without-perl --without-x \
    --with-heic=yes --with-jpeg=yes --with-png=yes --with-webp=yes --with-tiff=yes \
    --with-lcms=yes --with-xml=yes --with-freetype=yes
make -j2
make install
LD_LIBRARY_PATH=/opt/imagemagick/lib /opt/imagemagick/bin/magick --version | grep -F "ImageMagick $version "
