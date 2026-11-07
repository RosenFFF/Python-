"""把截屏帧、旁白和字幕合成 mp4。用法：python tools/video/mux.py <工作目录> <输出 mp4>

- 桌面帧原样使用；手机帧放进“浏览器调试工具 · 设备模式”的画面里（顶部设备工具栏、左侧手机视口、右侧解说区）。
- 字幕按旁白时间轴生成 ASS 后烧录：桌面场景在底部居中，手机场景在右侧解说区。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
W, H = 1280, 720
FONT = Path("C:/Windows/Fonts/msyh.ttc")
STAGE_BG = (226, 230, 236)
PHONE_H = 640
PHONE_W = round(PHONE_H * 390 / 844)
PHONE_X, PHONE_Y = 190, 58
PANEL_X = 600


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def build_stage() -> Image.Image:
    """设备模式的底图：灰色画布、设备工具栏、手机视口的投影。"""
    stage = Image.new("RGB", (W, H), STAGE_BG)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rectangle(
        (PHONE_X - 2, PHONE_Y + 4, PHONE_X + PHONE_W + 2, PHONE_Y + PHONE_H + 8), fill=90)
    stage.paste((150, 160, 175), mask=shadow.filter(ImageFilter.GaussianBlur(12)))
    draw = ImageDraw.Draw(stage)
    draw.rectangle((0, 0, W, 34), fill=(248, 249, 250))
    draw.line((0, 34, W, 34), fill=(208, 213, 219))
    small = font(14)
    x = PHONE_X - 70
    for text, gap, menu in (("尺寸：iPhone 13", 34, True), ("390", 12, False), ("×", 12, False), ("844", 34, False),
                            ("100%", 34, True), ("无节流", 34, True)):
        color = (60, 64, 67) if text != "×" else (128, 134, 139)
        draw.text((x, 8), text, font=small, fill=color)
        x += draw.textlength(text, font=small)
        if menu:  # 下拉箭头
            draw.polygon(((x + 6, 15), (x + 14, 15), (x + 10, 20)), fill=(95, 99, 104))
            x += 14
        x += gap
    # 工具栏右侧的“旋转”“更多”图标位，用简单几何代替
    draw.rounded_rectangle((W - 74, 10, W - 60, 24), radius=2, outline=(95, 99, 104), width=2)
    for i in range(3):
        draw.ellipse((W - 34, 9 + i * 6, W - 30, 13 + i * 6), fill=(95, 99, 104))
    return stage


def compose(work: Path, frames: list[dict]) -> Path:
    out = work / "composed"
    out.mkdir(exist_ok=True)
    stage = build_stage()
    for frame in frames:
        source = work / "frames" / frame["file"]
        if frame["src"] != "phone":
            frame["path"] = source
            continue
        target = out / frame["file"]
        if not target.exists():
            shot = Image.open(source).convert("RGB").resize((PHONE_W, PHONE_H), Image.LANCZOS)
            canvas = stage.copy()
            canvas.paste(shot, (PHONE_X, PHONE_Y))
            canvas.save(target, quality=92)
        frame["path"] = target
    return out


def split_lines(text: str, limit: int = 42) -> list[str]:
    parts = [p for s in re.split(r"(?<=[。！？])", text) if s
             for p in (re.split(r"(?<=[，；：])", s) if len(s) > limit else [s]) if p]
    lines: list[str] = []
    for part in parts:
        if lines and len(lines[-1] + part) <= limit:
            lines[-1] += part
        else:
            lines.append(part)
    return lines


def wrap(line: str, width: int = 17) -> str:
    """libass 不会在汉字之间自动换行，右侧解说区手工折行；标点不放到行首。"""
    rows, row = [], ""
    for char in line:
        if len(row) >= width and char not in "，。！？；：、）”":
            rows.append(row)
            row = ""
        row += char
    rows.append(row)
    return r"\N".join(rows)


def ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def build_subtitles(timeline: list[dict], texts: dict[str, str]) -> str:
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bottom,Microsoft YaHei,25,&H00FFFFFF,&H00FFFFFF,&H203A2010,&H203A2010,0,0,0,0,100,100,0,0,3,9,0,2,90,90,20,1
Style: Side,Microsoft YaHei,31,&H0033271C,&H0033271C,&H00FFFFFF,&H00000000,0,0,0,0,100,100,1,0,1,0,0,4,{PANEL_X},90,0,1
Style: Label,Microsoft YaHei,22,&H008F5125,&H008F5125,&H00FFFFFF,&H00000000,1,0,0,0,100,100,2,0,1,0,0,7,{PANEL_X},90,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for index, item in enumerate(timeline):
        start, seconds = item["at"], item["seconds"]
        end_limit = timeline[index + 1]["at"] if index + 1 < len(timeline) else start + seconds + 0.6
        lines = split_lines(texts[item["id"]])
        total = sum(len(line) for line in lines)
        style = "Side" if item["device"] == "phone" else "Bottom"
        acc = start
        for number, line in enumerate(lines):
            begin = acc
            acc += len(line) / total * seconds
            end = end_limit if number == len(lines) - 1 else acc
            text = wrap(line) if style == "Side" else line
            events.append(f"Dialogue: 0,{ass_time(begin)},{ass_time(min(end, end_limit))},{style},,0,0,0,,{text}")
        if item["device"] == "phone":
            who = "护理员端" if item["id"].startswith("cg_") or item["id"] == "offline" else "家属端"
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end_limit)},Label,,0,0,0,,{who} · 手机 H5（浏览器设备模拟）")
    return head + "\n".join(events) + "\n"


def main(work: Path, output: Path) -> None:
    data = json.loads((work / "frames.json").read_text(encoding="utf-8"))
    timeline = json.loads((work / "timeline.json").read_text(encoding="utf-8"))["timeline"]
    config = json.loads((HERE / "scenes.json").read_text(encoding="utf-8"))
    texts = {s["id"]: s["text"] for s in config["scenes"]}

    frames = data["frames"]
    compose(work, frames)
    lines = ["ffconcat version 1.0"]
    for index, frame in enumerate(frames):
        begin = 0 if index == 0 else frame["t"]
        end = frames[index + 1]["t"] if index + 1 < len(frames) else max(data["end"], frame["t"] + 0.5)
        lines += [f"file '{frame['path'].as_posix()}'", f"duration {max(end - begin, 0.001):.3f}"]
    # concat 会忽略最后一项的 duration，所以末帧再列一次，总长再用 -t 截齐
    lines.append(f"file '{frames[-1]['path'].as_posix()}'")
    total = max(data["end"], frames[-1]["t"] + 0.5)
    (work / "frames.ffconcat").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (work / "captions.ass").write_text(build_subtitles(timeline, texts), encoding="utf-8")
    fonts = work / "fonts"
    fonts.mkdir(exist_ok=True)
    if not (fonts / FONT.name).exists():
        shutil.copy(FONT, fonts / FONT.name)

    inputs = ["-f", "concat", "-safe", "0", "-i", "frames.ffconcat"]
    filters, labels = [], []
    for index, item in enumerate(timeline, start=1):
        inputs += ["-i", f"{item['id']}.mp3"]
        delay = int(item["at"] * 1000)
        filters.append(f"[{index}:a]adelay={delay}|{delay}[a{index}]")
        labels.append(f"[a{index}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0[aout]")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    video = (f"fps=25,scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=white,"
             "subtitles=captions.ass:fontsdir=fonts,format=yuv420p")
    command = [
        "ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[aout]",
        "-vf", video, "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
        "-t", f"{total:.2f}", "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, check=True, cwd=work)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True)
    print("输出", output, probe.stdout.strip())


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
