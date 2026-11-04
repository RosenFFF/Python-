"""访问控制的单一入口：RBAC（能做什么）+ 数据范围（能对谁做）。

服务层所有列表查询经过 scope_* 过滤，所有单条访问经过 ensure_*，
跨网格、跨机构的请求统一返回 404，不向调用方透露记录是否存在。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.errors import Forbidden, NotFound
from app.domain.enums import DataScope, RoleCode
from app.models import (
    Alert,
    Bill,
    Caregiver,
    Complaint,
    DispatchOffer,
    Elder,
    FamilyLink,
    Grid,
    ServiceException,
    ServiceOrder,
    User,
)
from app.services.audit_service import AuditContext


@dataclass
class Actor:
    user: User
    role: str
    scope: str
    permissions: set[str] = field(default_factory=set)
    ip: str | None = None
    caregiver_id: int | None = None

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def name(self) -> str:
        return self.user.display_name

    @property
    def ctx(self) -> AuditContext:
        return AuditContext(self.user.id, self.user.display_name, self.role, self.ip)

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise Forbidden(f"当前角色没有「{permission}」权限")


def build_actor(db: Session, user: User, ip: str | None = None) -> Actor:
    role = user.role
    caregiver_id = None
    if role.code == RoleCode.CAREGIVER:
        caregiver_id = db.execute(select(Caregiver.id).where(Caregiver.user_id == user.id)).scalar()
    return Actor(user=user, role=role.code, scope=role.data_scope,
                 permissions={p.code for p in role.permissions}, ip=ip, caregiver_id=caregiver_id)


def _family_elder_ids(actor: Actor):
    return select(FamilyLink.elder_id).where(FamilyLink.user_id == actor.id)


def _nothing():
    return ServiceOrder.id < 0


def _elder_condition(actor: Actor):
    if actor.scope == DataScope.STREET:
        return None
    if actor.scope == DataScope.GRID:
        return Elder.grid_id == actor.user.grid_id
    if actor.scope == DataScope.COMMUNITY:
        return Elder.grid_id.in_(select(Grid.id).where(Grid.community_id == actor.user.community_id))
    if actor.scope == DataScope.ORG:
        return Elder.id.in_(select(ServiceOrder.elder_id).where(ServiceOrder.org_id == actor.user.org_id))
    if actor.role == RoleCode.CAREGIVER:
        # 护理员只能看到自己承接过或正被邀约工单的老人
        return Elder.id.in_(
            select(ServiceOrder.elder_id).where(or_(
                ServiceOrder.caregiver_id == actor.caregiver_id,
                ServiceOrder.id.in_(select(DispatchOffer.order_id).where(
                    DispatchOffer.caregiver_id == actor.caregiver_id)),
            ))
        )
    return Elder.id.in_(_family_elder_ids(actor))


def scope_elders(actor: Actor, stmt: Select) -> Select:
    condition = _elder_condition(actor)
    return stmt if condition is None else stmt.where(condition)


def _order_condition(actor: Actor):
    if actor.scope == DataScope.STREET:
        return None
    if actor.scope == DataScope.GRID:
        return ServiceOrder.grid_id == actor.user.grid_id
    if actor.scope == DataScope.COMMUNITY:
        return ServiceOrder.community_id == actor.user.community_id
    if actor.scope == DataScope.ORG:
        return ServiceOrder.org_id == actor.user.org_id
    if actor.role == RoleCode.CAREGIVER:
        return or_(
            ServiceOrder.caregiver_id == actor.caregiver_id,
            ServiceOrder.id.in_(select(DispatchOffer.order_id).where(
                DispatchOffer.caregiver_id == actor.caregiver_id)),
        )
    return ServiceOrder.elder_id.in_(_family_elder_ids(actor))


def scope_orders(actor: Actor, stmt: Select) -> Select:
    condition = _order_condition(actor)
    return stmt if condition is None else stmt.where(condition)


def scope_alerts(actor: Actor, stmt: Select) -> Select:
    if actor.scope == DataScope.STREET:
        return stmt
    if actor.scope == DataScope.GRID:
        return stmt.where(Alert.grid_id == actor.user.grid_id)
    if actor.scope == DataScope.COMMUNITY:
        return stmt.where(Alert.grid_id.in_(select(Grid.id).where(Grid.community_id == actor.user.community_id)))
    if actor.scope == DataScope.ORG:
        return stmt.where(Alert.elder_id.in_(
            select(ServiceOrder.elder_id).where(ServiceOrder.org_id == actor.user.org_id)))
    if actor.role == RoleCode.CAREGIVER:
        return stmt.where(_nothing())
    return stmt.where(Alert.elder_id.in_(_family_elder_ids(actor)))


def scope_exceptions(actor: Actor, stmt: Select) -> Select:
    if actor.scope == DataScope.STREET:
        return stmt
    if actor.scope == DataScope.GRID:
        return stmt.where(ServiceException.grid_id == actor.user.grid_id)
    condition = _order_condition(actor)
    return stmt.where(ServiceException.order_id.in_(select(ServiceOrder.id).where(condition)))


def scope_complaints(actor: Actor, stmt: Select) -> Select:
    if actor.scope == DataScope.STREET:
        return stmt
    if actor.scope == DataScope.COMMUNITY:
        return stmt.where(Complaint.community_id == actor.user.community_id)
    if actor.scope == DataScope.GRID:
        return stmt.where(Complaint.community_id == actor.user.community_id)
    if actor.scope == DataScope.ORG:
        return stmt.where(Complaint.org_id == actor.user.org_id)
    if actor.role == RoleCode.CAREGIVER:
        return stmt.where(_nothing())
    return stmt.where(Complaint.order_id.in_(
        select(ServiceOrder.id).where(ServiceOrder.elder_id.in_(_family_elder_ids(actor)))))


def scope_bills(actor: Actor, stmt: Select) -> Select:
    if actor.scope == DataScope.STREET:
        return stmt
    if actor.scope in (DataScope.GRID, DataScope.COMMUNITY):
        return stmt.where(Bill.community_id == actor.user.community_id)
    if actor.scope == DataScope.ORG:
        from app.models import BillOrgConfirmation
        return stmt.where(Bill.id.in_(
            select(BillOrgConfirmation.bill_id).where(BillOrgConfirmation.org_id == actor.user.org_id)))
    return stmt.where(_nothing())


def scope_caregivers(actor: Actor, stmt: Select) -> Select:
    if actor.scope == DataScope.STREET:
        return stmt
    if actor.scope == DataScope.ORG:
        return stmt.where(Caregiver.org_id == actor.user.org_id)
    if actor.scope in (DataScope.GRID, DataScope.COMMUNITY):
        from app.models.org import community_orgs
        return stmt.where(Caregiver.org_id.in_(
            select(community_orgs.c.org_id).where(community_orgs.c.community_id == actor.user.community_id)))
    if actor.role == RoleCode.CAREGIVER:
        return stmt.where(Caregiver.id == actor.caregiver_id)
    return stmt.where(_nothing())


def ensure_elder(db: Session, actor: Actor, elder_id: int, include_deleted: bool = False) -> Elder:
    stmt = scope_elders(actor, select(Elder).where(Elder.id == elder_id))
    if not include_deleted:
        stmt = stmt.where(and_(Elder.is_deleted.is_(False)))
    elder = db.execute(stmt).scalar()
    if elder is None:
        raise NotFound("老人档案不存在或不在您的数据范围内")
    return elder


def ensure_order(db: Session, actor: Actor, order_id: int) -> ServiceOrder:
    order = db.execute(scope_orders(actor, select(ServiceOrder).where(ServiceOrder.id == order_id))).scalar()
    if order is None:
        raise NotFound("工单不存在或不在您的数据范围内")
    return order


def ensure_alert(db: Session, actor: Actor, alert_id: int) -> Alert:
    alert = db.execute(scope_alerts(actor, select(Alert).where(Alert.id == alert_id))).scalar()
    if alert is None:
        raise NotFound("预警不存在或不在您的数据范围内")
    return alert


def ensure_bill(db: Session, actor: Actor, bill_id: int) -> Bill:
    bill = db.execute(scope_bills(actor, select(Bill).where(Bill.id == bill_id))).scalar()
    if bill is None:
        raise NotFound("账单不存在或不在您的数据范围内")
    return bill


def ensure_complaint(db: Session, actor: Actor, complaint_id: int) -> Complaint:
    complaint = db.execute(scope_complaints(actor, select(Complaint).where(Complaint.id == complaint_id))).scalar()
    if complaint is None:
        raise NotFound("投诉单不存在或不在您的数据范围内")
    return complaint
