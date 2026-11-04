"""内置预警规则和补贴政策。首次启动写入，之后由后台维护，不覆盖已有配置。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AlertRule, SubsidyPolicy

BUILTIN_RULES = [
    {"code": "BP-HIGH-RED", "name": "收缩压过高", "rule_type": "THRESHOLD", "metric": "SYSTOLIC", "op": ">=", "threshold": 180, "level": "RED"},
    {"code": "BP-LOW-RED", "name": "收缩压过低", "rule_type": "THRESHOLD", "metric": "SYSTOLIC", "op": "<=", "threshold": 90, "level": "RED"},
    {"code": "BP-HIGH-YELLOW", "name": "收缩压偏高", "rule_type": "CONSECUTIVE", "metric": "SYSTOLIC", "op": ">=", "threshold": 160, "window": 3, "level": "YELLOW"},
    {"code": "SPO2-LOW-RED", "name": "血氧过低", "rule_type": "THRESHOLD", "metric": "SPO2", "op": "<", "threshold": 90, "level": "RED"},
    {"code": "SPO2-LOW-YELLOW", "name": "血氧偏低", "rule_type": "CONSECUTIVE", "metric": "SPO2", "op": "<", "threshold": 94, "window": 3, "level": "YELLOW"},
    {"code": "GLU-HIGH-RED", "name": "血糖过高", "rule_type": "THRESHOLD", "metric": "GLUCOSE", "op": ">=", "threshold": 16.7, "level": "RED"},
    {"code": "GLU-LOW-RED", "name": "低血糖", "rule_type": "THRESHOLD", "metric": "GLUCOSE", "op": "<=", "threshold": 3.9, "level": "RED"},
    {"code": "HR-HIGH-RED", "name": "心率过快", "rule_type": "THRESHOLD", "metric": "HEART_RATE", "op": ">=", "threshold": 150, "level": "RED"},
    {"code": "HR-LOW-RED", "name": "心率过缓", "rule_type": "THRESHOLD", "metric": "HEART_RATE", "op": "<=", "threshold": 40, "level": "RED"},
    {"code": "HR-CONSEC-YELLOW", "name": "心率连续偏快", "rule_type": "CONSECUTIVE", "metric": "HEART_RATE", "op": ">", "threshold": 100, "window": 3, "level": "YELLOW"},
    {"code": "TEMP-HIGH-YELLOW", "name": "体温偏高", "rule_type": "THRESHOLD", "metric": "TEMPERATURE", "op": ">=", "threshold": 38.5, "level": "YELLOW"},
    {"code": "MOTION-NONE-YELLOW", "name": "连续无体动", "rule_type": "CONSECUTIVE", "metric": "BODY_MOTION", "op": "<=", "threshold": 0, "window": 6, "level": "YELLOW"},
    {"code": "NO-DATA-BLUE", "name": "长时间无数据", "rule_type": "NO_DATA", "hours": 48, "level": "BLUE"},
    {"code": "HR-BASELINE-BLUE", "name": "心率偏离个人基线", "rule_type": "BASELINE", "metric": "HEART_RATE", "threshold": 3.5, "window": 10, "level": "BLUE"},
    {"code": "SBP-BASELINE-BLUE", "name": "收缩压偏离个人基线", "rule_type": "BASELINE", "metric": "SYSTOLIC", "threshold": 3.5, "window": 10, "level": "BLUE"},
    {"code": "SOS", "name": "老人一键呼救", "rule_type": "MANUAL", "level": "RED", "is_system": True},
    {"code": "SITE-UNWELL", "name": "服务现场突发不适", "rule_type": "MANUAL", "level": "RED", "is_system": True},
]

# (失能等级, 困难类型, 政府比例, 长护险比例, 月度补贴上限/分)
BUILTIN_POLICIES = [
    ("INTACT", "NONE", 0.10, 0.00, 10000), ("INTACT", "LOW_INCOME", 0.40, 0.00, 30000), ("INTACT", "EXTREME", 0.60, 0.00, 50000),
    ("MILD", "NONE", 0.20, 0.00, 20000), ("MILD", "LOW_INCOME", 0.50, 0.00, 40000), ("MILD", "EXTREME", 0.70, 0.00, 60000),
    ("MODERATE", "NONE", 0.30, 0.30, 40000), ("MODERATE", "LOW_INCOME", 0.50, 0.30, 60000), ("MODERATE", "EXTREME", 0.70, 0.30, 80000),
    ("SEVERE", "NONE", 0.40, 0.40, 60000), ("SEVERE", "LOW_INCOME", 0.50, 0.40, 80000), ("SEVERE", "EXTREME", 0.60, 0.40, 100000),
]


def sync_builtin_rules(db: Session) -> None:
    existing = set(db.execute(select(AlertRule.code)).scalars())
    for rule in BUILTIN_RULES:
        if rule["code"] not in existing:
            db.add(AlertRule(**rule))
    db.flush()


def sync_subsidy_policies(db: Session) -> None:
    existing = {(p.disability_level, p.difficulty_type) for p in db.execute(select(SubsidyPolicy)).scalars()}
    for level, difficulty, gov, ltci, cap in BUILTIN_POLICIES:
        if (level, difficulty) not in existing:
            db.add(SubsidyPolicy(disability_level=level, difficulty_type=difficulty, gov_ratio=gov, ltci_ratio=ltci, monthly_cap=cap))
    db.flush()
