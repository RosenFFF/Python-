"""模拟可穿戴设备上报。

用法：
    python scripts/simulate_wearable.py --url http://127.0.0.1:8000 --key <设备密钥> --devices WB-10000 WB-10001 \
        --rounds 10 --interval 2 --scenario tachycardia

场景：
    normal       正常波动
    tachycardia  心率持续偏快（连续 3 次 > 100，触发黄色预警）
    hypoxia      血氧骤降（< 90，触发红色预警）
    still        连续无体动（连续 6 次为 0，触发黄色预警）

只依赖标准库，方便部署到网关设备。默认读取 YIHU_DEVICE_API_KEY 环境变量。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))


def sample(scenario: str, round_index: int, rng: random.Random) -> dict[str, float]:
    heart_rate = rng.randint(66, 84)
    spo2 = rng.randint(95, 99)
    motion = rng.randint(5, 80)
    if scenario == "tachycardia":
        heart_rate = rng.randint(104, 122)
    elif scenario == "hypoxia" and round_index >= 2:
        spo2 = rng.randint(84, 89)
    elif scenario == "still":
        motion = 0
    return {"HEART_RATE": heart_rate, "SPO2": spo2, "BODY_MOTION": motion, "STEPS": rng.randint(0, 300)}


def post(url: str, key: str, records: list[dict]) -> dict:
    request = urllib.request.Request(
        url.rstrip("/") + "/api/health/wearable/batch", data=json.dumps({"records": records}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Device-Key": key}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="模拟可穿戴设备上报")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--key", default=os.environ.get("YIHU_DEVICE_API_KEY", ""))
    parser.add_argument("--devices", nargs="+", default=["WB-10000"])
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--interval", type=float, default=1.0, help="每轮间隔（秒）")
    parser.add_argument("--scenario", choices=["normal", "tachycardia", "hypoxia", "still"], default="normal")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if not args.key:
        parser.error("请通过 --key 或 YIHU_DEVICE_API_KEY 提供设备密钥")
    sys.stdout.reconfigure(encoding="utf-8")
    rng = random.Random(args.seed)
    for round_index in range(args.rounds):
        # 服务端用测量时间做幂等，这里取真实当前时间，并按设备错开毫秒
        base = datetime.now(TZ).replace(tzinfo=None)
        records = []
        for device_index, serial in enumerate(args.devices):
            at = base + timedelta(milliseconds=device_index)
            for metric, value in sample(args.scenario, round_index, rng).items():
                records.append({"device_serial": serial, "metric": metric, "value": value, "measured_at": at.isoformat()})
        try:
            result = post(args.url, args.key, records)
        except urllib.error.URLError as exc:
            print(f"第 {round_index + 1} 轮上报失败：{exc}")
            return 1
        print(f"第 {round_index + 1} 轮：写入 {result['stored']} 条，重复 {result['duplicates']} 条，触发预警 {result['alerts']}")
        if result["errors"]:
            print("  错误：", result["errors"][:3])
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
