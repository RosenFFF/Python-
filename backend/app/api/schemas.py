"""请求体模型。响应由服务层的 *_view 函数组装，敏感字段的脱敏集中在那里。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Name = str


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class ElderBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=64)
    gender: Literal["男", "女"] | None = None
    birth_date: date | None = None
    id_card: str | None = Field(default=None, max_length=18)
    phone: str | None = Field(default=None, max_length=11)
    address: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    grid_id: int | None = None
    emergency_name: str | None = Field(default=None, max_length=64)
    emergency_relation: str | None = Field(default=None, max_length=32)
    emergency_phone: str | None = Field(default=None, max_length=11)
    medical_insurance: str | None = Field(default=None, max_length=32)
    ltc_insurance: str | None = Field(default=None, max_length=32)
    chronic_diseases: list[str] | None = None
    medications: list[str] | None = None
    allergies: str | None = Field(default=None, max_length=255)
    disability_level: str | None = None
    difficulty_type: str | None = None
    care_tags: list[str] | None = None
    doctor_id: int | None = None


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class RevealIn(BaseModel):
    password: str
    reason: str = Field(max_length=255)
    fields: list[str]


class FamilyLinkIn(BaseModel):
    user_id: int
    relation: str = Field(default="家属", max_length=32)


class ItemCreateIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    pricing_mode: Literal["PER_TIME", "PER_HOUR"]
    standard_minutes: int = Field(gt=0, le=1440)
    required_skills: list[str] = []
    applicable_levels: list[str] = []
    subsidy_eligible: bool = True
    checklist: list[str] = []
    unit_price: int = Field(gt=0, description="单价（分）")
    subsidy_ratio: float = Field(default=0.0, ge=0, le=1)


class ItemUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=64)
    pricing_mode: Literal["PER_TIME", "PER_HOUR"] | None = None
    standard_minutes: int | None = Field(default=None, gt=0, le=1440)
    required_skills: list[str] | None = None
    applicable_levels: list[str] | None = None
    subsidy_eligible: bool | None = None
    checklist: list[str] | None = None


class ShelfIn(BaseModel):
    on_shelf: bool


class PriceAdjustIn(BaseModel):
    unit_price: int = Field(gt=0)
    subsidy_ratio: float | None = Field(default=None, ge=0, le=1)
    effective_from: datetime | None = None
    reason: str = Field(min_length=1, max_length=255)


class OrderCreateIn(BaseModel):
    elder_id: int
    item_id: int
    scheduled_start: datetime
    address: str | None = Field(default=None, max_length=255)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    remark: str | None = Field(default=None, max_length=255)
    source: Literal["ONLINE", "PHONE", "VOICE"] = "ONLINE"


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class PlanCreateIn(BaseModel):
    elder_id: int
    item_id: int
    weekday: int = Field(ge=0, le=6)
    time_of_day: str = Field(pattern=r"^\d{2}:\d{2}$")
    start_date: date
    end_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)


class AssignIn(BaseModel):
    caregiver_id: int | None = None
    round_id: int | None = None


class ReassignIn(BaseModel):
    caregiver_id: int
    reason: str = Field(min_length=1, max_length=255)


class CheckInIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    note: str | None = Field(default=None, max_length=255)


class ChecklistItem(BaseModel):
    name: str
    done: bool


class ChecklistIn(BaseModel):
    items: list[ChecklistItem]


class MetricPoint(BaseModel):
    metric: str
    value: float
    measured_at: datetime | None = None


class CheckOutIn(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    metrics: list[MetricPoint] = []
    incomplete_reason: str | None = Field(default=None, max_length=255)


class ExceptionIn(BaseModel):
    exception_type: Literal["NOT_HOME", "REFUSED", "UNWELL", "OTHER"]
    description: str | None = Field(default=None, max_length=500)


class ExceptionCloseIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=1000)
    order_action: Literal["CONTINUE", "NO_SHOW", "CANCEL"]


class SpotCheckIn(BaseModel):
    passed: bool
    note: str | None = Field(default=None, max_length=255)


class FollowupRecordIn(BaseModel):
    points: list[MetricPoint] = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=255)


class WearableRecord(BaseModel):
    device_serial: str = Field(max_length=64)
    metric: str
    value: float
    measured_at: datetime


class WearableBatchIn(BaseModel):
    records: list[WearableRecord] = Field(max_length=2000)


class AlertRespondIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class AlertCloseIn(BaseModel):
    conclusion: Literal["HANDLED", "FALSE_ALARM", "REFERRED"]
    measures: str | None = Field(default=None, max_length=1000)
    false_reason: str | None = Field(default=None, max_length=255)
    hospital: str | None = Field(default=None, max_length=128)
    referred_at: datetime | None = None


class SosIn(BaseModel):
    elder_id: int
    note: str | None = Field(default=None, max_length=255)


class RuleCreateIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    rule_type: Literal["THRESHOLD", "CONSECUTIVE", "NO_DATA", "BASELINE"]
    metric: str | None = None
    op: Literal[">=", "<=", ">", "<"] | None = None
    threshold: float | None = None
    window: int | None = Field(default=None, ge=1, le=100)
    hours: float | None = Field(default=None, gt=0)
    level: Literal["BLUE", "YELLOW", "RED"]
    is_active: bool = True


class RuleUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=64)
    metric: str | None = None
    op: Literal[">=", "<=", ">", "<"] | None = None
    threshold: float | None = None
    window: int | None = Field(default=None, ge=1, le=100)
    hours: float | None = Field(default=None, gt=0)
    level: Literal["BLUE", "YELLOW", "RED"] | None = None
    is_active: bool | None = None


class DeviceBindIn(BaseModel):
    serial: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="智能手环", max_length=32)


class ReviewIn(BaseModel):
    punctuality: int = Field(ge=1, le=5)
    attitude: int = Field(ge=1, le=5)
    professionalism: int = Field(ge=1, le=5)
    effectiveness: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)


class FollowupHandleIn(BaseModel):
    outcome: Literal["IMPROVE", "TO_COMPLAINT"]
    note: str = Field(min_length=1, max_length=1000)


class ComplaintIn(BaseModel):
    order_id: int
    content: str = Field(min_length=1, max_length=1000)


class ExplainIn(BaseModel):
    explanation: str = Field(min_length=1, max_length=2000)


class ConcludeIn(BaseModel):
    conclusion: Literal["UPHELD", "REJECTED"]
    basis: str = Field(min_length=1, max_length=2000)
    actions: list[Literal["REFUND", "REPLACE", "PENALTY"]] = []
    penalty_points: int | None = Field(default=None, gt=0, le=100)


class GenerateBillIn(BaseModel):
    community_id: int
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class ManualFeeIn(BaseModel):
    order_id: int
    amount: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=255)


class ResolveIn(BaseModel):
    resolution: Literal["APPROVE", "EXCLUDE", "CLIP"]
    explanation: str = Field(min_length=1, max_length=1000)


class ReviewBillIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class SampleIn(BaseModel):
    passed: bool
    note: str | None = Field(default=None, max_length=500)


class PaymentIn(BaseModel):
    method: str = Field(default="现金", max_length=32)


class UserCreateIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    role: str
    phone: str | None = Field(default=None, max_length=11)
    community_id: int | None = None
    grid_id: int | None = None
    org_id: int | None = None


class UserUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=64)
    role: str | None = None
    phone: str | None = Field(default=None, max_length=11)
    community_id: int | None = None
    grid_id: int | None = None
    org_id: int | None = None
    is_active: bool | None = None


class PasswordResetIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class RolePermissionsIn(BaseModel):
    permissions: list[str]


class ConfigUpdateIn(BaseModel):
    value: dict | list | float | int | str | bool


class CaregiverIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=64)
    skills: list[str] | None = None
    base_lat: float | None = Field(default=None, ge=-90, le=90)
    base_lng: float | None = Field(default=None, ge=-180, le=180)
    is_active: bool | None = None


class PolicyUpdateIn(BaseModel):
    gov_ratio: float = Field(ge=0, le=1)
    ltci_ratio: float = Field(ge=0, le=1)
    monthly_cap: int = Field(ge=0)


class RestorePreviewIn(BaseModel):
    name: str | None = None
    target_time: datetime | None = None


class RestoreConfirmIn(BaseModel):
    confirm_token: str
    password: str
    confirm_word: str


class SyncOpIn(BaseModel):
    op_id: str = Field(min_length=8, max_length=64)
    kind: str
    order_id: int
    occurred_at: datetime
    # 客户端递增序号。同一秒内的多个操作靠它保持先后顺序
    seq: int = 0
    payload: dict = {}


class SyncBatchIn(BaseModel):
    ops: list[SyncOpIn] = Field(max_length=200)


class ParseBookingIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class ClockAdvanceIn(BaseModel):
    minutes: int = Field(ge=1, le=60 * 24 * 40)
