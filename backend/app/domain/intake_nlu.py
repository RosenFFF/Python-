"""自然语言预约解析（AI 辅助的本地兜底实现）。

老人不会填表单，可以说一句"帮我约明天下午两点助浴"。这里用确定性规则抽取
服务项目和时间，只负责预填表单，提交前必须由用户确认；没有大模型也能离线运行。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

ITEM_SYNONYMS: dict[str, list[str]] = {
    "助浴": ["助浴", "洗澡", "洗浴", "擦身"],
    "助餐": ["助餐", "送饭", "做饭", "吃饭", "送餐"],
    "助洁": ["助洁", "打扫", "保洁", "卫生", "收拾屋"],
    "助医": ["助医", "买药", "拿药", "取药"],
    "陪同就医": ["陪同就医", "陪诊", "看病", "去医院", "陪我去医院"],
    "康复护理": ["康复", "按摩", "理疗", "复健"],
    "代购代办": ["代购", "代办", "买菜", "跑腿"],
    "精神慰藉": ["精神慰藉", "聊天", "陪聊", "说说话", "陪我说话"],
}


def cn_to_int(text: str) -> int | None:
    """支持 0-59 的中文或阿拉伯数字：十、十二、二十、三十五、14。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_DIGITS:
        return _CN_DIGITS[text]
    return None


_NUM = r"[0-9]{1,2}|[零〇一二两三四五六七八九十]{1,3}"
_TIME_RE = re.compile(rf"({_NUM})\s*(?:点|时|:|：)\s*(半|一刻|三刻|{_NUM})?\s*分?")
_RELATIVE_DAYS = [("大后天", 3), ("后天", 2), ("明天", 1), ("明日", 1), ("今天", 0), ("今日", 0)]
_PERIODS = {"凌晨": (0, False), "早上": (6, False), "早晨": (6, False), "上午": (6, False), "中午": (11, False),
            "下午": (12, True), "傍晚": (12, True), "晚上": (12, True), "今晚": (12, True), "明晚": (12, True)}


@dataclass
class ParsedIntake:
    item_name: str | None = None
    scheduled_at: datetime | None = None
    weekly: bool = False
    weekday: int | None = None
    missing: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


def _parse_date(text: str, today: date) -> tuple[date | None, bool, int | None]:
    for word, days in _RELATIVE_DAYS:
        if word in text:
            return today + timedelta(days=days), False, None
    if "今晚" in text:
        return today, False, None
    if "明晚" in text:
        return today + timedelta(days=1), False, None
    match = re.search(r"(每周|每星期|每礼拜|下周|下星期|下礼拜|本周|这周|周|星期|礼拜)([一二三四五六日天])", text)
    if match:
        prefix, weekday = match.group(1), _WEEKDAYS[match.group(2)]
        weekly = prefix.startswith("每")
        base = today - timedelta(days=today.weekday())
        if prefix.startswith("下"):
            return base + timedelta(days=7 + weekday), weekly, weekday
        candidate = base + timedelta(days=weekday)
        if candidate <= today:
            candidate += timedelta(days=7)
        return candidate, weekly, weekday
    match = re.search(rf"(?:({_NUM})月)?({_NUM})(?:号|日)", text)
    if match:
        month = cn_to_int(match.group(1)) if match.group(1) else today.month
        day = cn_to_int(match.group(2))
        try:
            candidate = date(today.year, month, day)
        except (TypeError, ValueError):
            return None, False, None
        if candidate < today:
            if match.group(1):
                candidate = date(today.year + 1, month, day)
            else:
                candidate = date(today.year + (month == 12), month % 12 + 1, day)
        return candidate, False, None
    return None, False, None


def _parse_time(text: str) -> tuple[int, int] | None:
    match = _TIME_RE.search(text)
    if not match:
        if "中午" in text:
            return 12, 0
        return None
    hour = cn_to_int(match.group(1))
    if hour is None or hour > 24:
        return None
    tail = match.group(2)
    minute = {"半": 30, "一刻": 15, "三刻": 45}.get(tail or "")
    if minute is None:
        minute = cn_to_int(tail) if tail else 0
    if minute is None or minute >= 60:
        minute = 0
    head = text[: match.start()]
    for word, (_, add_noon) in _PERIODS.items():
        if word in head and add_noon and hour < 12:
            hour += 12
            break
    return hour % 24, minute


def parse_intake(text: str, now: datetime, known_items: list[str] | None = None) -> ParsedIntake:
    result = ParsedIntake()
    allowed = set(known_items) if known_items else None
    for name, words in ITEM_SYNONYMS.items():
        if allowed is not None and name not in allowed:
            continue
        if any(word in text for word in words):
            result.item_name = name
            break
    day, weekly, weekday = _parse_date(text, now.date())
    clock_time = _parse_time(text)
    result.weekly, result.weekday = weekly, weekday
    if day and clock_time:
        result.scheduled_at = datetime(day.year, day.month, day.day, clock_time[0], clock_time[1])
        if result.scheduled_at <= now:
            result.hints.append("解析出的时间已经过去，请重新选择")
    if result.item_name is None:
        result.missing.append("服务项目")
    if day is None:
        result.missing.append("日期")
    if clock_time is None:
        result.missing.append("时间")
    return result
