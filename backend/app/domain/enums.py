"""领域枚举与中文标签。数据库存英文编码，界面展示走 LABELS。"""
from __future__ import annotations

from enum import StrEnum


class DisabilityLevel(StrEnum):
    INTACT = "INTACT"
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


class DifficultyType(StrEnum):
    NONE = "NONE"
    LOW_INCOME = "LOW_INCOME"  # 低保
    EXTREME = "EXTREME"  # 特困


class PricingMode(StrEnum):
    PER_TIME = "PER_TIME"
    PER_HOUR = "PER_HOUR"


class OrderStatus(StrEnum):
    PENDING_ACCEPT = "PENDING_ACCEPT"
    ACCEPTED = "ACCEPTED"
    PENDING_DISPATCH = "PENDING_DISPATCH"
    DISPATCHED = "DISPATCHED"
    IN_SERVICE = "IN_SERVICE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class OrderSource(StrEnum):
    ONLINE = "ONLINE"
    PHONE = "PHONE"
    RECURRING = "RECURRING"
    VOICE = "VOICE"


class OfferStatus(StrEnum):
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    REVOKED = "REVOKED"


class ExceptionType(StrEnum):
    NOT_HOME = "NOT_HOME"
    REFUSED = "REFUSED"
    UNWELL = "UNWELL"
    OTHER = "OTHER"


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    PROCESSING = "PROCESSING"
    CLOSED = "CLOSED"


class SpotCheckStatus(StrEnum):
    NONE = "NONE"
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"


class Metric(StrEnum):
    SYSTOLIC = "SYSTOLIC"
    DIASTOLIC = "DIASTOLIC"
    GLUCOSE = "GLUCOSE"
    HEART_RATE = "HEART_RATE"
    SPO2 = "SPO2"
    STEPS = "STEPS"
    BODY_MOTION = "BODY_MOTION"
    TEMPERATURE = "TEMPERATURE"


class HealthSource(StrEnum):
    VISIT = "VISIT"  # 服务签退时附带
    FOLLOWUP = "FOLLOWUP"  # 上门随访录入
    WEARABLE = "WEARABLE"  # 可穿戴设备上报


class RuleType(StrEnum):
    THRESHOLD = "THRESHOLD"  # 单项越界
    CONSECUTIVE = "CONSECUTIVE"  # 连续异常
    NO_DATA = "NO_DATA"  # 长时间无数据
    BASELINE = "BASELINE"  # 个体基线偏离（AI 辅助）
    MANUAL = "MANUAL"  # 人工触发（SOS、现场异常）


class AlertLevel(StrEnum):
    BLUE = "BLUE"
    YELLOW = "YELLOW"
    RED = "RED"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    RESPONDED = "RESPONDED"
    CLOSED = "CLOSED"


class AlertConclusion(StrEnum):
    HANDLED = "HANDLED"
    FALSE_ALARM = "FALSE_ALARM"
    REFERRED = "REFERRED"


class FollowupStatus(StrEnum):
    OPEN = "OPEN"
    DONE = "DONE"


class FollowupOutcome(StrEnum):
    IMPROVE = "IMPROVE"
    TO_COMPLAINT = "TO_COMPLAINT"


class ComplaintStatus(StrEnum):
    OPEN = "OPEN"  # 已提交，待指派
    ASSIGNED = "ASSIGNED"  # 已指派机构，待说明
    EXPLAINED = "EXPLAINED"  # 机构已说明，待结论
    CONCLUDED = "CONCLUDED"  # 已结论归档


class ComplaintConclusion(StrEnum):
    UPHELD = "UPHELD"
    REJECTED = "REJECTED"


class FeeType(StrEnum):
    SERVICE = "SERVICE"
    CANCEL_FEE = "CANCEL_FEE"  # 迟取消、爽约费用
    REFUND = "REFUND"  # 红冲
    MANUAL = "MANUAL"  # 人工补录


class BillStatus(StrEnum):
    DRAFT = "DRAFT"  # 已生成，待处理差异
    NEEDS_REVIEW = "NEEDS_REVIEW"  # 差异超 1%，待人工复核
    GRID_CONFIRMED = "GRID_CONFIRMED"  # 网格员已核对
    ORG_CONFIRMED = "ORG_CONFIRMED"  # 机构已确认，抽查中
    EFFECTIVE = "EFFECTIVE"


class DiscrepancyType(StrEnum):
    DUPLICATE = "DUPLICATE"
    OVER_CAP = "OVER_CAP"
    NOT_COMPLETED = "NOT_COMPLETED"
    DURATION_ABNORMAL = "DURATION_ABNORMAL"
    SAMPLE_FAILED = "SAMPLE_FAILED"  # 监管抽查不通过


class DiscrepancyStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class DiscrepancyResolution(StrEnum):
    APPROVE = "APPROVE"  # 核实无误，照常计费
    EXCLUDE = "EXCLUDE"  # 剔除出账单
    CLIP = "CLIP"  # 补贴截断到上限，超出部分转个人自付


class SampleResult(StrEnum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"


class PaymentStatus(StrEnum):
    UNPAID = "UNPAID"
    PAID = "PAID"


class DataScope(StrEnum):
    SELF = "SELF"
    GRID = "GRID"
    COMMUNITY = "COMMUNITY"
    ORG = "ORG"
    STREET = "STREET"


class RoleCode(StrEnum):
    ELDER_FAMILY = "elder_family"
    CAREGIVER = "caregiver"
    GRID_WORKER = "grid_worker"
    COMMUNITY_ADMIN = "community_admin"
    ORG_ADMIN = "org_admin"
    STREET_SUPERVISOR = "street_supervisor"
    SUPER_ADMIN = "super_admin"


LABELS: dict[str, dict[str, str]] = {
    "DisabilityLevel": {"INTACT": "能力完好", "MILD": "轻度失能", "MODERATE": "中度失能", "SEVERE": "重度失能"},
    "DifficultyType": {"NONE": "无", "LOW_INCOME": "低保", "EXTREME": "特困"},
    "PricingMode": {"PER_TIME": "按次", "PER_HOUR": "按小时"},
    "OrderStatus": {
        "PENDING_ACCEPT": "待受理", "ACCEPTED": "已受理", "PENDING_DISPATCH": "待派单",
        "DISPATCHED": "已派单", "IN_SERVICE": "服务中", "COMPLETED": "已完成",
        "CANCELLED": "已取消", "NO_SHOW": "已爽约",
    },
    "OrderSource": {"ONLINE": "在线预约", "PHONE": "电话代办", "RECURRING": "周期预约", "VOICE": "语音预约"},
    "OfferStatus": {"OFFERED": "待接单", "ACCEPTED": "已接单", "REJECTED": "已拒单", "TIMEOUT": "超时未接", "REVOKED": "已撤回"},
    "ExceptionType": {"NOT_HOME": "老人不在家", "REFUSED": "拒绝服务", "UNWELL": "突发不适", "OTHER": "其他"},
    "ExceptionStatus": {"OPEN": "待处置", "PROCESSING": "处置中", "CLOSED": "已闭环"},
    "SpotCheckStatus": {"NONE": "无需抽查", "PENDING": "待抽查", "PASS": "抽查通过", "FAIL": "抽查不通过"},
    "Metric": {
        "SYSTOLIC": "收缩压", "DIASTOLIC": "舒张压", "GLUCOSE": "血糖", "HEART_RATE": "心率",
        "SPO2": "血氧", "STEPS": "步数", "BODY_MOTION": "体动", "TEMPERATURE": "体温",
    },
    "HealthSource": {"VISIT": "服务附带", "FOLLOWUP": "上门随访", "WEARABLE": "可穿戴设备"},
    "RuleType": {"THRESHOLD": "单项越界", "CONSECUTIVE": "连续异常", "NO_DATA": "长时间无数据", "BASELINE": "个体基线偏离", "MANUAL": "人工触发"},
    "AlertLevel": {"BLUE": "蓝色", "YELLOW": "黄色", "RED": "红色"},
    "AlertStatus": {"OPEN": "待响应", "RESPONDED": "处置中", "CLOSED": "已闭环"},
    "AlertConclusion": {"HANDLED": "已处置", "FALSE_ALARM": "误报", "REFERRED": "已转诊"},
    "FollowupStatus": {"OPEN": "待回访", "DONE": "已完成"},
    "FollowupOutcome": {"IMPROVE": "服务改进", "TO_COMPLAINT": "转投诉"},
    "ComplaintStatus": {"OPEN": "待指派", "ASSIGNED": "待机构说明", "EXPLAINED": "待结论", "CONCLUDED": "已归档"},
    "ComplaintConclusion": {"UPHELD": "投诉成立", "REJECTED": "投诉不成立"},
    "FeeType": {"SERVICE": "服务费", "CANCEL_FEE": "取消/爽约费", "REFUND": "退费红冲", "MANUAL": "人工补录"},
    "BillStatus": {
        "DRAFT": "待处理差异", "NEEDS_REVIEW": "待人工复核", "GRID_CONFIRMED": "网格员已核对",
        "ORG_CONFIRMED": "监管抽查中", "EFFECTIVE": "已生效",
    },
    "DiscrepancyType": {"DUPLICATE": "同时段重复计费", "OVER_CAP": "月度补贴超限", "NOT_COMPLETED": "未完成却计费", "DURATION_ABNORMAL": "服务时长异常", "SAMPLE_FAILED": "监管抽查不通过"},
    "DiscrepancyStatus": {"OPEN": "待处理", "RESOLVED": "已处理"},
    "DiscrepancyResolution": {"APPROVE": "核实无误", "EXCLUDE": "剔除", "CLIP": "截断至上限"},
    "SampleResult": {"PENDING": "待抽查", "PASS": "通过", "FAIL": "不通过"},
    "PaymentStatus": {"UNPAID": "未缴费", "PAID": "已缴费"},
    "DataScope": {"SELF": "本人", "GRID": "本网格", "COMMUNITY": "本社区", "ORG": "本机构", "STREET": "全街道"},
    "RoleCode": {
        "elder_family": "老人与家属", "caregiver": "护理员", "grid_worker": "社区网格员",
        "community_admin": "社区管理员", "org_admin": "服务机构管理员",
        "street_supervisor": "街道监管员", "super_admin": "系统超级管理员",
    },
}

CARE_TAGS = ["独居", "空巢", "失智", "卧床", "低保", "特困", "高龄", "残疾"]
MEDICAL_INSURANCE_TYPES = ["城镇职工医保", "城乡居民医保", "公费医疗", "无"]
LTC_INSURANCE_TYPES = ["未参保", "职工长护险", "居民长护险"]
SKILLS = ["助餐", "助浴", "助洁", "助医", "康复护理", "陪同就医", "代购代办", "精神慰藉", "失能照护", "失智照护"]


def label(enum_name: str, value: str | None) -> str:
    return LABELS.get(enum_name, {}).get(value or "", value or "")
