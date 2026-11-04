"""把旁白合成为音频，并写出每段时长。用法：python tools/video/tts.py <输出目录>

依赖 edge-tts（联网）和 ffprobe。旁白文本发送到微软语音服务，内容只有 scenes.json 的解说词。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import edge_tts

HERE = Path(__file__).parent


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    config = json.loads((HERE / "scenes.json").read_text(encoding="utf-8"))
    durations = {}
    for scene in config["scenes"]:
        target = out / f"{scene['id']}.mp3"
        if not target.exists():
            await edge_tts.Communicate(scene["text"], config["voice"], rate="+6%").save(str(target))
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(target)],
            capture_output=True, text=True, check=True)
        durations[scene["id"]] = float(probe.stdout.strip())
    (out / "durations.json").write_text(json.dumps(durations, indent=1), encoding="utf-8")
    print(f"{len(durations)} 段，共 {sum(durations.values()):.0f} 秒")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
