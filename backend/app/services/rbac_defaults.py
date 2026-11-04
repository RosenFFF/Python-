"""权限码和默认角色矩阵。启动时同步到数据库，超级管理员可在后台调整。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import LABELS, DataScope, RoleCode
from app.models import Permission, Role

PERMISSIONS: dict[str, tuple[str, str]] = {
    "elder:read": ("查看档案", "档案"), "elder:write": ("维护档案", "档案"), "elder:delete": ("删除档案", "档案"),
    "elder:import": ("批量导入", "档案"), "elder:reveal": ("查看敏感信息", "档案"), "elder:changelog": ("查看变更日志", "档案"),
    "catalog:read": ("查看服务目录", "服务目录"), "catalog:write": ("维护服务目录", "服务目录"),
    "order:create": ("提交预约", "预约工单"), "order:read": ("查看工单", "预约工单"), "order:accept": ("受理预约", "预约工单"),
    "order:cancel": ("取消预约", "预约工单"),
    "dispatch:manage": ("派单改派", "派单"), "offer:respond": ("接单拒单", "派单"),
    "exec:perform": ("执行上门服务", "服务执行"), "exception:handle": ("处置异常", "服务执行"), "exec:spotcheck": ("时长抽查", "服务执行"),
    "health:read": ("查看健康数据", "健康预警"), "health:write": ("录入随访数据", "健康预警"), "alert:read": ("查看预警", "健康预警"),
    "alert:handle": ("处置预警", "健康预警"), "rule:manage": ("配置预警规则", "健康预警"), "sos:trigger": ("一键呼救", "健康预警"),
    "device:manage": ("绑定设备", "健康预警"),
    "review:create": ("提交评价", "质量"), "followup:handle": ("处理回访", "质量"), "complaint:create": ("提交投诉", "质量"),
    "complaint:read": ("查看投诉", "质量"), "complaint:manage": ("指派与结论投诉", "质量"), "complaint:explain": ("提交投诉说明", "质量"),
    "perf:read": ("查看绩效榜", "质量"),
    "billing:read": ("查看账单", "结算"), "billing:generate": ("生成账单", "结算"), "billing:discrepancy": ("处理对账差异", "结算"),
    "billing:review": ("人工复核账单", "结算"), "billing:grid_confirm": ("网格核对账单", "结算"), "billing:org_confirm": ("机构确认账单", "结算"),
    "billing:sample": ("监管抽查", "结算"), "billing:payment": ("登记缴费", "结算"), "billing:export": ("导出账单", "结算"),
    "billing:manual_fee": ("人工补录费用", "结算"), "statement:read": ("查看个人账单", "结算"),
    "stats:community": ("社区看板", "统计"), "stats:org": ("机构看板", "统计"), "stats:street": ("街道看板", "统计"), "stats:export": ("导出统计", "统计"),
    "caregiver:read": ("查看护理员", "组织"), "caregiver:write": ("维护护理员", "组织"),
    "user:manage": ("用户管理", "系统"), "role:manage": ("角色权限", "系统"), "config:manage": ("业务参数", "系统"),
    "audit:read": ("查看审计日志", "系统"), "backup:manage": ("备份恢复", "系统"), "job:manage": ("定时任务", "系统"),
}

_GRID_BASE = {
    "elder:read", "elder:write", "elder:import", "elder:reveal", "elder:changelog", "catalog:read", "order:create",
    "order:read", "order:accept", "order:cancel", "dispatch:manage", "exception:handle", "exec:spotcheck", "health:read",
    "health:write", "alert:read", "alert:handle", "sos:trigger", "device:manage", "complaint:read", "billing:read",
    "billing:discrepancy", "billing:grid_confirm", "billing:export", "stats:community", "stats:export", "caregiver:read",
}

ROLE_DEFAULTS: dict[str, tuple[str, set[str]]] = {
    RoleCode.ELDER_FAMILY: (DataScope.SELF, {
        "elder:read", "catalog:read", "order:create", "order:read", "order:cancel", "health:read", "alert:read",
        "sos:trigger", "review:create", "complaint:create", "complaint:read", "statement:read",
    }),
    RoleCode.CAREGIVER: (DataScope.SELF, {
        "elder:read", "catalog:read", "order:read", "offer:respond", "exec:perform", "health:read", "health:write",
        "sos:trigger", "caregiver:read",
    }),
    RoleCode.GRID_WORKER: (DataScope.GRID, set(_GRID_BASE)),
    RoleCode.COMMUNITY_ADMIN: (DataScope.COMMUNITY, _GRID_BASE | {
        "elder:delete", "followup:handle", "complaint:manage", "perf:read", "billing:generate", "billing:review",
        "billing:payment", "billing:manual_fee", "rule:manage",
    }),
    RoleCode.ORG_ADMIN: (DataScope.ORG, {
        "elder:read", "catalog:read", "catalog:write", "order:read", "complaint:read", "complaint:explain", "perf:read",
        "billing:read", "billing:org_confirm", "billing:export", "billing:manual_fee", "stats:org", "stats:export",
        "caregiver:read", "caregiver:write", "exception:handle",
    }),
    RoleCode.STREET_SUPERVISOR: (DataScope.STREET, {
        "elder:read", "catalog:read", "order:read", "alert:read", "alert:handle", "complaint:read", "perf:read",
        "billing:read", "billing:sample", "billing:export", "billing:generate", "stats:community", "stats:org",
        "stats:street", "stats:export", "audit:read", "caregiver:read", "health:read",
    }),
    RoleCode.SUPER_ADMIN: (DataScope.STREET, {
        "elder:read", "elder:changelog", "catalog:read", "catalog:write", "order:read", "alert:read", "rule:manage",
        "user:manage", "role:manage", "config:manage", "audit:read", "backup:manage", "job:manage", "caregiver:read",
        "caregiver:write", "billing:read", "billing:generate", "stats:community", "stats:org", "stats:street",
    }),
}


def sync_rbac(db: Session) -> None:
    """补齐缺失的权限码和角色。已有角色的权限不覆盖，保留后台的调整。"""
    existing = {p.code: p for p in db.execute(select(Permission)).scalars()}
    for code, (name, group) in PERMISSIONS.items():
        if code not in existing:
            existing[code] = Permission(code=code, name=name, group=group)
            db.add(existing[code])
    db.flush()
    roles = {r.code for r in db.execute(select(Role)).scalars()}
    for code, (scope, permissions) in ROLE_DEFAULTS.items():
        if str(code) in roles:
            continue
        db.add(Role(code=code, name=LABELS["RoleCode"][code], data_scope=scope,
                    permissions=[existing[p] for p in sorted(permissions)]))
    db.flush()
