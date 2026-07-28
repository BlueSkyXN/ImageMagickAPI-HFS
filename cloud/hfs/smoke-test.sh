#!/bin/sh
# Exercise a running wrapper image without external fixtures or Python packages.
set -eu

base_url=${1:-"http://127.0.0.1:${PORT:-8000}"}
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/hfs-smoke.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

python3 -B - "$tmp_dir/input.png" <<'PY'
import struct
import sys
import zlib

path = sys.argv[1]
# A one-pixel opaque red PNG: standard-library-only fixture generation.
def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
row = b"\x00" + b"\xff\x00\x00" * 2
png += chunk(b"IDAT", zlib.compress(row * 2))
png += chunk(b"IEND", b"")
open(path, "wb").write(png)
PY

wait_for_health() {
    attempt=0
    while [ "$attempt" -lt 60 ]; do
        if curl --silent --show-error --fail "$base_url/health" >"$tmp_dir/health.json"; then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    printf '%s\n' "health endpoint did not become ready: $base_url" >&2
    return 1
}

verify_response() {
    format=$1
    expected_type=$2
    output="$tmp_dir/output.$format"
    headers="$tmp_dir/$format.headers"
    status=$(curl --silent --show-error --output "$output" --dump-header "$headers" \
        --write-out '%{http_code}' --request POST \
        --form "file=@$tmp_dir/input.png;type=image/png" \
        "$base_url/convert/$format/lossy/80")
    case "$status" in 2??) ;; *)
        printf '%s\n' "$format conversion returned HTTP $status" >&2
        return 1
    esac
    python3 -B - "$format" "$expected_type" "$headers" "$output" <<'PY'
import sys

format_name, expected_type, headers_path, output_path = sys.argv[1:]
headers = open(headers_path, "r", encoding="iso-8859-1").read().splitlines()
content_types = [
    line.split(":", 1)[1].strip().lower()
    for line in headers
    if line.lower().startswith("content-type:")
]
if content_types != [expected_type]:
    raise SystemExit(
        f"{format_name} response has unexpected Content-Type values: {content_types!r}"
    )

data = open(output_path, "rb").read()
if format_name == "webp":
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise SystemExit(
            f"WebP response lacks RIFF/WEBP magic "
            f"(size={len(data)}, prefix={data[:32].hex()})"
        )
else:
    if len(data) < 16 or data[4:8] != b"ftyp":
        raise SystemExit(
            f"{format_name} response lacks an ISO-BMFF ftyp box "
            f"(size={len(data)}, prefix={data[:32].hex()})"
        )
    ftyp_size = int.from_bytes(data[:4], "big")
    if ftyp_size < 16 or ftyp_size > len(data) or (ftyp_size - 16) % 4:
        raise SystemExit(f"{format_name} response has an invalid ftyp box length")
    major = data[8:12]
    compatible = {data[offset:offset + 4] for offset in range(16, ftyp_size, 4)}
    brands = compatible | {major}
    accepted = (
        {b"avif", b"avis"}
        if format_name == "avif"
        else {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}
    )
    if not brands & accepted:
        raise SystemExit(f"{format_name} response has incompatible ISO-BMFF brands: {brands!r}")
PY
}

wait_for_health
verify_response webp image/webp
verify_response avif image/avif
verify_response heif image/heif
printf '%s\n' "HFS format smoke test passed against $base_url"
