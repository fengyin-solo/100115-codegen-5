"""岸电接入计量接口。

登记接电船舶与接电/断电时刻，读取电表或按时长折算用电量，并提供按船公司、
接电时段汇总的用电量看板。原有「人工抄表」方式以手工补录读数的形式保留。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.shorepower import shorepower_service as service

router = APIRouter(prefix="/api/shorepower", tags=["岸电接入计量"])

LIST_FIELDS = [
    "接电单号", "船舶名称", "船公司", "接电时刻", "断电时刻",
    "接电时长(h)", "起始读数", "结束读数", "用电量(kWh)",
    "碳排放(kgCO2)", "计量口径", "计量状态",
]
STATUSES = ["接电中", "待抄表", "已计量", "重复接电"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按接电单号/船舶/船公司/表计编号检索"),
    status: str | None = Query(default=None, description="接电中、待抄表、已计量、重复接电"),
    company: str | None = Query(default=None, description="按船公司名称检索"),
    month: str | None = Query(default=None, description="按接电月份过滤，格式 YYYY-MM"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """按船公司、状态、月份过滤岸电接电记录；缺断电时刻的记录单独有标记列。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"计量状态仅支持：{'、'.join(STATUSES)}")
    service.bootstrap()
    items, total = service.list_entries(
        keyword=keyword, status=status, company=company, month=month, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/dashboard")
def dashboard(
    month: str | None = Query(default=None, description="按接电月份汇总，格式 YYYY-MM"),
) -> dict[str, Any]:
    """用电量看板：总用电量/碳排放卡片、按船公司、按日期、按接电时段的分布。"""
    service.bootstrap()
    return service.dashboard(month=month)


@router.get("/consistency")
def consistency(
    month: str | None = Query(default=None, description="按接电月份对账，格式 YYYY-MM"),
) -> dict[str, Any]:
    """看板合计与逐笔用电记录的对账结果，供刷新后核对数字一致性。"""
    service.bootstrap()
    return service.consistency(month=month)


@router.get("/rules")
def rules() -> dict[str, Any]:
    """接电时长与用电量的换算口径说明，看板与登记页共用。"""
    return service.rules()


@router.get("/export")
def export_entries(month: str | None = None) -> dict[str, Any]:
    """导出岸电接电计量全量记录（含缺断电、重复、抄表失败等标记）。"""
    service.bootstrap()
    items, total = service.list_entries(month=month, page=1, size=10000)
    return {"module": "shorepower", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条接电记录明细；不存在时给出可读的错误说明。"""
    service.bootstrap()
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"接电单 {entry_id} 不存在")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记接电船舶、接电时刻（断电时刻可后补）；重复接电会被单独标出且不计费。"""
    service.bootstrap()
    entry, missing, note = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if entry is None:
        return ActionResult(ok=False, message=note or "接电登记未通过校验")
    return ActionResult(ok=True, message=note or "接电记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行断电登记、远程抄表（失败报因可重试）、手工抄录、时长折算。"""
    service.bootstrap()
    action = str(payload.values.get("action") or "").strip()
    if action == "登记断电":
        entry, message = service.record_disconnect(entry_id, payload.values)
    elif action == "远程抄表":
        entry, message = service.read_meter(entry_id)
    elif action == "手工抄录":
        entry, message = service.manual_reading(entry_id, payload.values)
    elif action == "时长折算":
        entry, message = service.estimate_by_duration(entry_id)
    else:
        return ActionResult(
            ok=False,
            message="动作仅支持：登记断电、远程抄表、手工抄录、时长折算",
        )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
