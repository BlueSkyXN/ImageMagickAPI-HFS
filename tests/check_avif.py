"""Exercise AVIF outputs against a running service using real image tools."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request
import uuid


def run(*command):
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    return result.stdout + result.stderr


def request(url, data=None, headers=None):
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers or {}), timeout=180)


def upload(base_url, source, output, mode, setting):
    boundary = 'avif-check-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{source.name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body += source.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    with request(f'{base_url}/convert/avif/{mode}/{setting}', body,
                 {'Content-Type': f'multipart/form-data; boundary={boundary}'}) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == 'image/avif'
        data = response.read()
    assert data[4:8] == b'ftyp', 'AVIF output is missing ftyp'
    size = int.from_bytes(data[:4], 'big')
    assert 16 <= size <= len(data) and (size - 16) % 4 == 0
    brands = {data[8:12]} | {data[index:index + 4] for index in range(16, size, 4)}
    assert b'avif' in brands or b'avis' in brands
    output.write_bytes(data)


def main():
    base_url = sys.argv[1].rstrip('/')
    with request(base_url + '/health') as response:
        health = json.load(response)
    assert health['status'] == 'healthy'
    assert 'ImageMagick 7.1.2-32 ' in health['imagemagick'], health['imagemagick']
    print('ImageMagick:', health['imagemagick'])
    print('libheif:', health['dependencies']['heif_enc']['version'])
    print('Resource settings:', health['resource_limits'])
    print('AOM parameters:\n' + run('heif-enc', '--avif', '--encoder', 'aom', '--params'))
    formats = run('magick', '-list', 'format')
    for name in ('AVIF', 'HEIC', 'PNG', 'JPEG', 'WEBP'):
        assert re.search(r'^\s+' + name + r'\*?\s+\S+\s+rw', formats, re.MULTILINE), name

    with tempfile.TemporaryDirectory(prefix='avif-check-') as directory:
        root = Path(directory)
        rgb = root / 'rgb.png'
        rgba = root / 'rgba.png'
        deep = root / 'deep.png'
        run('magick', '-size', '32x24', 'gradient:red-blue', '-depth', '8', 'PNG24:' + str(rgb))
        run('magick', '-size', '32x24', 'xc:rgba(255,0,0,0.5)', '-depth', '8', 'PNG32:' + str(rgba))
        run('magick', '-size', '32x24', 'gradient:red-blue', '-depth', '16', 'PNG48:' + str(deep))
        jpeg, webp, heic = (root / name for name in ('input.jpg', 'input.webp', 'input.heic'))
        run('magick', str(rgb), str(jpeg))
        run('magick', str(rgb), str(webp))
        run('heif-enc', '--encoder', 'x265', '-p', 'preset=ultrafast', '-p', 'x265:pools=1',
            '-p', 'x265:frame-threads=1', '--output', str(heic), str(rgb))
        cases = [(rgb, 'lossy', 80, 8, False), (rgb, 'lossless', 0, 8, False),
                 (rgb, 'lossless', 75, 8, False), (rgb, 'lossless', 100, 8, False),
                 (rgba, 'lossless', 75, 8, True), (deep, 'lossless', 75, 10, False),
                 (jpeg, 'lossless', 75, 8, False), (webp, 'lossless', 75, 8, False),
                 (heic, 'lossless', 75, 8, False)]
        for index, (source, mode, setting, bits, alpha) in enumerate(cases):
            output = root / f'output-{index}.avif'
            upload(base_url, source, output, mode, setting)
            info = run('heif-info', str(output))
            assert 'MIME type: image/avif' in info, info
            assert re.search(r'image: 32x24\b', info), info
            assert f'bit depth: {bits}' in info, info
            assert 'alpha channel: ' + ('yes' if alpha else 'no') in info, info
            if mode == 'lossless':
                assert 'matrix coefficients: 0 (RGB/GBR)' in info, info
            if source in (rgb, rgba) and mode == 'lossless':
                decoded = root / f'decoded-{index}.png'
                run('magick', str(output), str(decoded))
                comparison = subprocess.run(['magick', 'compare', '-metric', 'AE', str(source),
                                             str(decoded), 'null:'], capture_output=True, text=True, timeout=30)
                assert comparison.returncode == 0, comparison.stderr
            print(f'{source.name} -> AVIF {mode}/{setting}: {bits} bit, alpha={alpha}, '
                  f'{output.stat().st_size} bytes; passed')
        print('16-bit PNG currently becomes 10-bit AVIF; source-depth losslessness is NOT asserted.')
    print('AVIF input/parameter checks passed')


if __name__ == '__main__':
    main()
