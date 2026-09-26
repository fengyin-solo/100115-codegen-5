"""岸电接入计量接口：接电/断电登记、抄表重试与用电量看板。

原有的人工用电抄表方式不受影响，本组接口只服务新增的岸电接入计量。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.shorepower import GROUP_OPTIONS, ShorePowerService

router = APIRouter(prefix="/api/shore-power", tags=["岸电接入计量"])

service = ShorePowerService()

LIST_FIELDS = [
    "记录编号", "船舶名称", "所属船公司", "接电泊位", "接电时刻", "断电时刻",
    "接电时长(h)", "起始读数(kWh)", "结束读数(kWh)", "用电量(kWh)",
    "计量方式", "计量状态", "抄表次数",
]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按船名或记录编号检索"),
    company: str | None = Query(default=None, description="按船公司过滤"),
    status: str | None = Query(default=None, description="接电中、已断电、重复接电"),
    missing_disconnect: bool = Query(default=False, description="只看断电时刻缺失的记录"),
    duplicated: bool | None = Query(default=None, description="是否只看重复接电记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列示接电记录；重复接电、缺失断电时刻都可单独筛出。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, company=company, status=status,
        missing_disconnect=missing_disconnect, duplicated=duplicated,
        page=page, size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/companies", response_model=dict)
def list_companies() -> dict[str, Any]:
    """船公司下拉选项，取自已登记的接电记录。"""
    return {"items": service.companies()}


@router.get("/dashboard", response_model=dict)
def get_dashboard(
    group_by: str = Query(default="day", description=f"分组方式：{'、'.join(GROUP_OPTIONS)}"),
    company: str | None = Query(default=None, description="只看某家船公司"),
) -> dict[str, Any]:
    """用电量看板：数字按用电记录实时汇总，刷新后与列表完全一致。"""
    if group_by not in GROUP_OPTIONS:
        raise HTTPException(status_code=400, detail=f"分组方式仅支持：{'、'.join(GROUP_OPTIONS)}")
    return service.dashboard(group_by=group_by, company=company)


@router.get("/rules", response_model=dict)
def get_rules() -> dict[str, Any]:
    """接电时长与用电量的换算口径说明。"""
    return service.rules()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出岸电接电记录全量清单，供月底电费与碳排放核算取数。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "shorepower", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条接电记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"接电记录 {entry_id} 不存在")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记接电船舶与接电时刻；断电时刻可留空，后续补登。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条记录执行登记断电、重试抄表；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    # 重试抄表后仍失败不算接口错误，由 ok=False 提示原因并允许继续重试。
    ok = not (action == "重试抄表" and entry.get("计量状态") == "读表失败")
    return ActionResult(ok=ok, message=message, entry=entry)
