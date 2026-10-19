#!/usr/bin/env python3
"""Render the supplied SnowCrash SVG; synchronize frontend/backend static assets.

From the repo root:
  uv pip install --python .venv/bin/python -r scripts/snowcrash/requirements.txt
  .venv/bin/python scripts/snowcrash/generate-assets.py [--check]
"""

import argparse
import io
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'static/static'
BACKEND = ROOT / 'backend/open_webui/static'


def assets():
    logo = ET.fromstring((SOURCE / 'snowcrash-logo.svg').read_bytes())
    # Keep the complete source mark within the safe zone of maskable app icons.
    svg = ET.Element(
        'svg',
        xmlns='http://www.w3.org/2000/svg',
        viewBox='0 0 512 512',
        width='512',
        height='512',
    )
    ET.SubElement(svg, 'rect', width='512', height='512', rx='64', fill='#0F1729')
    group = ET.SubElement(svg, 'g', transform='translate(144 108) scale(0.78)')
    for path in logo:
        # White shield with the red internal mark on a navy tile.
        path.set('fill', '#FFFFFF' if path.get('fill') == '#000000' else '#E5322B')
        group.append(path)
    icon_svg = ET.tostring(svg, encoding='utf-8', xml_declaration=True)
    result = {'favicon.svg': icon_svg}
    for name, size in {
        'favicon.png': 64,
        'favicon-96x96.png': 96,
        'apple-touch-icon.png': 180,
        'web-app-manifest-192x192.png': 192,
        'web-app-manifest-512x512.png': 512,
        'logo.png': 500,
        'splash.png': 192,
        'splash-dark.png': 192,
    }.items():
        result[name] = cairosvg.svg2png(bytestring=icon_svg, output_width=size, output_height=size)
    ico = io.BytesIO()
    Image.open(io.BytesIO(result['web-app-manifest-512x512.png'])).save(
        ico, format='ICO', sizes=[(16, 16), (32, 32), (48, 48), (64, 64)]
    )
    result['favicon.ico'] = ico.getvalue()
    manifest = {
        'name': 'SnowCrash',
        'short_name': 'SnowCrash',
        'description': 'Chat with SnowCrash.',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'theme_color': '#0F1729',
        'background_color': '#0F1729',
        'icons': [
            {
                'src': f'/static/web-app-manifest-{size}x{size}.png',
                'sizes': f'{size}x{size}',
                'type': 'image/png',
                'purpose': 'any maskable',
            }
            for size in (192, 512)
        ],
        'share_target': {'action': '/', 'method': 'GET', 'params': {'text': 'shared'}},
    }
    result['site.webmanifest'] = (json.dumps(manifest, indent=2) + '\n').encode()
    for name in ('custom.css', 'snowcrash-logo.svg'):
        result[name] = (SOURCE / name).read_bytes()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if generated assets differ')
    args = parser.parse_args()
    outputs = {}
    for name, data in assets().items():
        outputs[SOURCE / name] = data
        outputs[BACKEND / name] = data
    outputs[ROOT / 'static/favicon.png'] = outputs[SOURCE / 'favicon.png']
    outputs[ROOT / 'static/manifest.json'] = outputs[SOURCE / 'site.webmanifest']
    stale = []
    for path, data in outputs.items():
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_bytes(data)
    if stale:
        parser.exit(1, 'Regenerate assets: ' + ', '.join(stale) + '\n')
    print(f'{"Checked" if args.check else "Generated"} {len(outputs)} small static assets.')


if __name__ == '__main__':
    main()
