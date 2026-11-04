"""把截屏帧和旁白合成 mp4。用法：python tools/video/mux.py <工作目录> <输出 mp4>"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main(work: Path, output: Path) -> None:
    timeline = json.loads((work / "timeline.json").read_text(encoding="utf-8"))["timeline"]
    inputs = ["-f", "concat", "-safe", "0", "-i", str(work / "frames.ffconcat")]
    filters, labels = [], []
    for index, item in enumerate(timeline, start=1):
        inputs += ["-i", str(work / f"{item['id']}.mp3")]
        delay = int(item["at"] * 1000)
        filters.append(f"[{index}:a]adelay={delay}|{delay}[a{index}]")
        labels.append(f"[a{index}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0[aout]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[aout]",
        "-vf", "fps=25,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=white,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, check=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True)
    print("输出", output, probe.stdout.strip())


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
