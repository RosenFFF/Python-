"""汇总导入所有模型，确保 Base.metadata 完整。"""
from app.models.billing import (
    Bill,
    BillOrgConfirmation,
    Discrepancy,
    ElderStatement,
    FeeDetail,
    SampleCheck,
    SubsidyPolicy,
)
from app.models.catalog import PriceVersion, ServiceItem
from app.models.elder import Device, Elder, ElderChangeLog, FamilyLink, SensitiveAccessLog
from app.models.health import Alert, AlertEvent, AlertRule, HealthRecord
from app.models.order import (
    CaregiverExclusion,
    CaregiverSlot,
    DispatchOffer,
    DispatchRound,
    OrderEvent,
    RecurringPlan,
    ServiceException,
    ServiceExecution,
    ServiceOrder,
    ServicePhoto,
)
from app.models.org import Caregiver, Community, Doctor, Grid, Organization, Permission, Role, Street, User
from app.models.quality import CaregiverPenalty, Complaint, FollowupTask, Review
from app.models.system import AuditLog, JobRun, Notification, SyncOp, SystemConfig

__all__ = [
    "Alert", "AlertEvent", "AlertRule", "AuditLog", "Bill", "BillOrgConfirmation", "Caregiver",
    "CaregiverExclusion", "CaregiverPenalty", "CaregiverSlot", "Community", "Complaint", "Device", "Discrepancy",
    "DispatchOffer", "DispatchRound", "Doctor", "Elder", "ElderChangeLog", "ElderStatement",
    "FamilyLink", "FeeDetail", "FollowupTask", "Grid", "HealthRecord", "JobRun", "Notification",
    "OrderEvent", "Organization", "Permission", "PriceVersion", "RecurringPlan", "Review", "Role",
    "SampleCheck", "SensitiveAccessLog", "ServiceException", "ServiceExecution", "ServiceItem",
    "ServiceOrder", "ServicePhoto", "Street", "SubsidyPolicy", "SyncOp", "SystemConfig", "User",
]
