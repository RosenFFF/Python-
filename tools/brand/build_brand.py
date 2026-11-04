"""生成品牌资产：把思源宋体（SIL OFL）里的字形转成 SVG 路径，输出 logo 和前端用的展示字体子集。

用法：python tools/brand/build_brand.py <fonttools 所在目录>
产物：frontend/src/brand.js、frontend/public/icon.svg、frontend/src/assets/display.woff2 不需要联网运行。
"""
from __future__ import annotations

import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
WORDMARK = "颐护通"
DISPLAY_TEXT = "谁上门、几点到、做了什么，家人都看得见。颐护通社区智慧养老服务平台欢迎回来"
INDIGO, SUN, WHITE = "#25518F", "#F2A93B", "#FFFFFF"


def fetch_font(text: str, weight: int, target: Path) -> Path:
    if target.exists():
        return target
    query = urllib.parse.urlencode({"family": f"Noto Serif SC:wght@{weight}", "text": text})
    # 不带浏览器 UA 时 Google Fonts 返回 TTF
    css = urllib.request.urlopen(f"https://fonts.googleapis.com/css2?{query}", timeout=20).read().decode()  # noqa: S310
    url = css.split("url(")[1].split(")")[0]
    target.write_bytes(urllib.request.urlopen(url, timeout=30).read())  # noqa: S310
    return target


def wordmark_paths(font: TTFont, text: str, size: float = 40, tracking: float = 3.0) -> tuple[list[str], float]:
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    cursor, paths = 0.0, []
    for char in text:
        name = cmap[ord(char)]
        pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, size * 0.86)))
        paths.append(pen.getCommands())
        cursor += glyphs[name].width * scale + tracking
    return paths, cursor - tracking


def mark_svg(size: int = 64) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{size}" height="{size}">'
        f'<circle cx="32" cy="32" r="31" fill="{INDIGO}"/>'
        f'<path d="M13 30 32 14l19 16" fill="none" stroke="{WHITE}" stroke-width="4.6" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="32" cy="33" r="7" fill="{SUN}"/>'
        f'<path d="M15 41c4 7.5 10 11 17 11s13-3.500 17-11" fill="none" stroke="{WHITE}" stroke-width="4.6" stroke-linecap="round"/>'
        "</svg>\n"
    )


def main() -> None:
    cache = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "tools" / "brand" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    bold = TTFont(fetch_font(WORDMARK, 700, cache / "wordmark.ttf"))
    paths, width = wordmark_paths(bold, WORDMARK)

    display_ttf = fetch_font(DISPLAY_TEXT, 600, cache / "display.ttf")
    display = TTFont(display_ttf)
    display.flavor = "woff2"
    out_font = ROOT / "frontend" / "src" / "assets" / "display.woff2"
    out_font.parent.mkdir(parents=True, exist_ok=True)
    display.save(out_font)

    (ROOT / "frontend" / "src" / "brand.js").write_text(
        "// 由 tools/brand/build_brand.py 生成：思源宋体字形轮廓，不依赖运行环境的字体。\n"
        f"export const WORDMARK_PATHS = {paths!r}\n"
        f"export const WORDMARK_WIDTH = {width:.1f}\n",
        encoding="utf-8",
    )
    (ROOT / "frontend" / "public" / "icon.svg").write_text(mark_svg(), encoding="utf-8")

    # 完整标志，放进文档和封面
    gap, mark_h = 14, 64
    glyphs = "".join(f'<path d="{d}" fill="{INDIGO}"/>' for d in paths)
    lockup = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {mark_h + gap + width:.0f} 64">'
        + mark_svg().split(">", 1)[1].rsplit("</svg>", 1)[0]
        + f'<g transform="translate({mark_h + gap},12)">{glyphs}</g></svg>\n'
    )
    brand_dir = ROOT / "docs" / "brand"
    brand_dir.mkdir(parents=True, exist_ok=True)
    (brand_dir / "logo.svg").write_text(lockup, encoding="utf-8")
    (brand_dir / "mark.svg").write_text(mark_svg(), encoding="utf-8")
    print("wordmark width", round(width, 1), "display font", out_font.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
