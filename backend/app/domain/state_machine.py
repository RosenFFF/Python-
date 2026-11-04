"""状态机。所有带生命周期的实体都从这里校验流转，流转不可跳跃。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Generic, TypeVar

from app.core.errors import InvalidTransition
from app.domain.enums import (
    AlertStatus,
    BillStatus,
    ComplaintStatus,
    ExceptionStatus,
    OrderStatus,
    label,
)

S = TypeVar("S", bound=str)


class StateMachine(Generic[S]):
    def __init__(self, name: str, enum_name: str, transitions: Mapping[S, frozenset[S]]):
        self.name = name
        self.enum_name = enum_name
        self.transitions = transitions

    def can(self, current: S, target: S) -> bool:
        return target in self.transitions.get(current, frozenset())

    def check(self, current: S, target: S) -> None:
        if not self.can(current, target):
            raise InvalidTransition(
                f"{self.name}状态不能从「{label(self.enum_name, current)}」"
                f"变更为「{label(self.enum_name, target)}」",
                details={"from": current, "to": target},
            )

    def is_terminal(self, state: S) -> bool:
        return not self.transitions.get(state)


_O = OrderStatus
ORDER_FLOW: StateMachine[OrderStatus] = StateMachine(
    "工单", "OrderStatus",
    {
        _O.PENDING_ACCEPT: frozenset({_O.ACCEPTED, _O.CANCELLED}),
        _O.ACCEPTED: frozenset({_O.PENDING_DISPATCH, _O.CANCELLED}),
        _O.PENDING_DISPATCH: frozenset({_O.DISPATCHED, _O.CANCELLED}),
        # DISPATCHED → PENDING_DISPATCH 用于改派
        _O.DISPATCHED: frozenset({_O.IN_SERVICE, _O.PENDING_DISPATCH, _O.CANCELLED, _O.NO_SHOW}),
        _O.IN_SERVICE: frozenset({_O.COMPLETED, _O.CANCELLED, _O.NO_SHOW}),
        _O.COMPLETED: frozenset(),
        _O.CANCELLED: frozenset(),
        _O.NO_SHOW: frozenset(),
    },
)

_E = ExceptionStatus
EXCEPTION_FLOW: StateMachine[ExceptionStatus] = StateMachine(
    "异常单", "ExceptionStatus",
    {
        _E.OPEN: frozenset({_E.PROCESSING, _E.CLOSED}),
        _E.PROCESSING: frozenset({_E.CLOSED}),
        _E.CLOSED: frozenset(),
    },
)

_A = AlertStatus
ALERT_FLOW: StateMachine[AlertStatus] = StateMachine(
    "预警", "AlertStatus",
    {
        _A.OPEN: frozenset({_A.RESPONDED, _A.CLOSED}),
        _A.RESPONDED: frozenset({_A.CLOSED}),
        _A.CLOSED: frozenset(),
    },
)

_C = ComplaintStatus
COMPLAINT_FLOW: StateMachine[ComplaintStatus] = StateMachine(
    "投诉单", "ComplaintStatus",
    {
        _C.OPEN: frozenset({_C.ASSIGNED}),
        _C.ASSIGNED: frozenset({_C.EXPLAINED}),
        _C.EXPLAINED: frozenset({_C.CONCLUDED}),
        _C.CONCLUDED: frozenset(),
    },
)

_B = BillStatus
BILL_FLOW: StateMachine[BillStatus] = StateMachine(
    "账单", "BillStatus",
    {
        _B.DRAFT: frozenset({_B.NEEDS_REVIEW, _B.GRID_CONFIRMED}),
        _B.NEEDS_REVIEW: frozenset({_B.GRID_CONFIRMED, _B.DRAFT}),
        _B.GRID_CONFIRMED: frozenset({_B.ORG_CONFIRMED, _B.DRAFT}),
        _B.ORG_CONFIRMED: frozenset({_B.EFFECTIVE, _B.DRAFT}),
        _B.EFFECTIVE: frozenset(),
    },
)
