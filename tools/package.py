"""打包提交材料。用法：python tools/package.py <输出 zip> <设计文档> <演示视频>

压缩包结构：源码仓库/（含 .git）、设计文档.docx、演示视频.mp4。
运行时数据、依赖目录和本机配置不打包。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".venv", "node_modules", "data", "backups", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", ".claude", "htmlcov"}
EXCLUDED_NAMES = {".env", ".env.docker", ".coverage"}


def main(output: Path, design: Path, video: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if not path.is_file() or set(relative.parts) & EXCLUDED_DIRS or path.name in EXCLUDED_NAMES:
                continue
            archive.write(path, Path("源码仓库") / relative)
            count += 1
        archive.write(design, "设计文档.docx")
        archive.write(video, "演示视频.mp4", compress_type=zipfile.ZIP_STORED)
    print(f"{output}：源码 {count} 个文件，{output.stat().st_size / 1048576:.1f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
