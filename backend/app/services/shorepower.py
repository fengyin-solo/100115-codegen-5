"""岸电接入计量业务规则。

计量口径（与前端看板共用同一套计算，保证刷新后看板数字与用电记录一致）：
1. 接电时长(h) =（断电时刻 − 接电时刻）换算成小时，保留 2 位小数；
   断电时刻缺失时不计时长、不计用电量，并在列表中单独标出。
2. 用电量(kWh) 优先取表计读数差 = 结束读数 − 起始读数；
   计量表读不到数时先报明失败原因并允许重试，也可走原有的手工抄录补数；
   两者都不可得时，按 接电时长 × 参考功率(150 kWh/h) 折算兜底，口径列标注「时长折算」。
3. 碳排放(kgCO2) = 用电量(kWh) × 0.5703（全国电网平均排放因子，kgCO2/kWh，估算口径）。
4. 同一艘船接电时段重叠的重复接电记录只保留最早一条参与计量，其余标「重复接电」，不计费、不计碳。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.store import store

MODULE = "shorepower"

REQUIRED_FIELDS = ["船舶名称", "船公司", "接电时刻"]
READING_FIELDS = ["起始读数", "结束读数"]

STATUS_CONNECTED = "接电中"
STATUS_WAITING = "待抄表"
STATUS_METERED = "已计量"
STATUS_DUPLICATE = "重复接电"
STATUSES = [STATUS_CONNECTED, STATUS_WAITING, STATUS_METERED, STATUS_DUPLICATE]

# 时长折算兜底口径：参考功率 150 kW（船舶在港岸电常用负荷估算值，可按船型调整）
REFERENCE_POWER_KW = 150.0
# 全国电网平均二氧化碳排放因子（kgCO2/kWh），月度碳排放估算口径
CARBON_FACTOR = 0.5703

CALIBER_METER = "表计读数差"
CALIBER_MANUAL = "读数差（手工抄录）"
CALIBER_ESTIMATE = "时长折算"

RULES: dict[str, Any] = {
    "duration": "接电时长(h) =（断电时刻 − 接电时刻）/ 3600 秒，保留 2 位小数；断电时刻缺失时不计时长，记录单独标「缺断电时刻」。",
    "consumption": "用电量(kWh) 优先 = 结束读数 − 起始读数（表计读数差）；计量表读不到数时报明原因并允许重试，也可按原有方式手工抄录补数。",
    "estimate": f"兜底口径「时长折算」：用电量(kWh) = 接电时长(h) × 参考功率 {REFERENCE_POWER_KW:g} kWh/h（参考负荷 {REFERENCE_POWER_KW:g} kW）。",
    "carbon": f"碳排放估算(kgCO2) = 用电量(kWh) × {CARBON_FACTOR}（全国电网平均排放因子，kgCO2/kWh）。",
    "dedup": "同一船舶接电时段相互重叠的记录判定为重复接电，仅最早一条计入用电量与碳排放，其余标「重复接电」并不计费。",
    "referencePowerKw": REFERENCE_POWER_KW,
    "carbonFactor": CARBON_FACTOR,
}

SLOT_LABELS = ["00:00–06:00", "06:00–12:00", "12:00–18:00", "18:00–24:00"]


def _parse_dt(value: Any) -> datetime | None:
    """接受 'YYYY-MM-DD HH:MM'、'YYYY-MM-DDTHH:MM' 等写法；空值返回 None。"""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("时间格式应为 YYYY-MM-DD HH:MM")


def _fmt_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _to_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("表计读数必须是数字")


def _num(value: float) -> float:
    """整数度电不带小数点，其余保留 2 位，方便看板与表格直接展示。"""
    rounded = round(value, 2)
    return int(rounded) if float(rounded).is_integer() else rounded


def meter_digits(meter: str) -> int:
    """取出表计编号里的数字部分（MTR-103 → 103），用于演示型通信异常判定。"""
    digits = re.sub(r"\D", "", meter)
    return int(digits) if digits else 0


def _intervals_overlap(
    start_a: datetime,
    end_a: datetime | None,
    start_b: datetime,
    end_b: datetime | None,
) -> bool:
    """断电时刻缺失按「仍在接电」处理，右端点视为无限远；端点相接不算重叠。"""
    far_a = end_a is None
    far_b = end_b is None
    if far_a and far_b:
        return True
    if far_a:
        return start_a < end_b  # type: ignore[operator]
    if far_b:
        return start_b < end_a  # type: ignore[operator]
    return start_a < end_b and start_b < end_a


class ShorepowerService:
    def __init__(self) -> None:
        self._booted = False

    # ------------------------------------------------------------------ 初始化
    def bootstrap(self) -> None:
        """把种子数据按统一口径补齐派生字段；只跑一次。"""
        if self._booted:
            return
        rows = store.rows(MODULE)
        for row in rows:
            self._recompute(row)
        # 两遍扫描：重叠时段判重（保留最早一条）
        for index, row in enumerate(rows):
            if row.get("duplicate"):
                continue
            start = _parse_dt(row.get("接电时刻"))
            if start is None:
                continue
            end = _parse_dt(row.get("断电时刻"))
            for earlier in rows[:index]:
                if earlier.get("duplicate") or earlier.get("船舶名称") != row.get("船舶名称"):
                    continue
                earlier_start = _parse_dt(earlier.get("接电时刻"))
                earlier_end = _parse_dt(earlier.get("断电时刻"))
                if earlier_start is not None and _intervals_overlap(
                    earlier_start, earlier_end, start, end
                ):
                    self._mark_duplicate(row, earlier)
                    break
        self._booted = True

    # ------------------------------------------------------------------ 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        company: str | None = None,
        month: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._select_rows(keyword=keyword, status=status, company=company, month=month)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def rules(self) -> dict[str, Any]:
        return dict(RULES)

    # ------------------------------------------------------------------ 登记
    def create_entry(
        self, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str], str | None]:
        """登记一条接电记录；返回 (记录, 缺失字段, 提示/错误)。"""
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing, None
        try:
            start_dt = _parse_dt(values.get("接电时刻"))
            end_dt = _parse_dt(values.get("断电时刻"))
        except ValueError as exc:
            return None, [], str(exc)
        if start_dt is None:
            return None, ["接电时刻"], None
        if end_dt is not None and end_dt < start_dt:
            return None, [], "断电时刻不能早于接电时刻"
        try:
            start_reading = _to_float(values.get("起始读数"))
            end_reading = _to_float(values.get("结束读数"))
        except ValueError as exc:
            return None, [], str(exc)
        if (
            start_reading is not None
            and end_reading is not None
            and end_reading < start_reading
        ):
            return None, [], "结束读数不能小于起始读数"
        if end_dt is None and end_reading is not None:
            return None, [], "尚未登记断电时刻，不能先填结束读数"

        rows = store.rows(MODULE)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "接电单号": self._next_code(rows),
            "船舶名称": str(values["船舶名称"]).strip(),
            "船公司": str(values["船公司"]).strip(),
            "接电时刻": _fmt_dt(start_dt),
            "断电时刻": _fmt_dt(end_dt) if end_dt else None,
            "表计编号": str(values.get("表计编号") or "").strip() or None,
            "起始读数": _num(start_reading) if start_reading is not None else None,
            "结束读数": _num(end_reading) if end_reading is not None else None,
            "读数来源": "手工抄录" if start_reading is not None or end_reading is not None else None,
            "meter_error": None,
            "read_attempts": 0,
        }
        rows.append(entry)
        self._recompute(entry)

        note = None
        if end_dt is None:
            note = "接电记录已登记（接电中），断电后请补登断电时刻"
        if entry["计量状态"] == STATUS_METERED:
            note = "接电记录已登记，用电量按读数差一次结算完成"
        overlap = self._find_overlap(entry)
        if overlap is not None:
            self._mark_duplicate(entry, overlap)
            note = (
                f"与接电单 {overlap.get('接电单号')} 同一船舶且接电时段重叠，"
                "按重复接电处理：该记录标出但不计入用电量（只算一次）"
            )
        return entry, [], note

    def record_disconnect(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接电单 {entry_id} 不存在"
        if entry.get("duplicate"):
            return None, "重复接电记录不参与计量"
        raw_end = str(values.get("断电时刻") or "").strip()
        if not raw_end:
            return None, "请填写断电时刻"
        try:
            end_dt = _parse_dt(raw_end)
        except ValueError as exc:
            return None, str(exc)
        if end_dt is None:
            return None, "断电时刻格式应为 YYYY-MM-DD HH:MM"
        start_dt = _parse_dt(entry.get("接电时刻"))
        if start_dt is not None and end_dt < start_dt:
            return None, "断电时刻不能早于接电时刻"
        entry["断电时刻"] = _fmt_dt(end_dt)
        self._recompute(entry)

        overlap = self._find_overlap(entry)
        if overlap is not None:
            self._mark_duplicate(entry, overlap)
            return entry, (
                f"断电已登记，但该记录与 {overlap.get('接电单号')} 时段重叠，"
                "改判为重复接电，不计入用电量"
            )
        if entry["计量状态"] == STATUS_METERED:
            return entry, "断电已登记，已有读数差，用电量已结算"
        return entry, "断电已登记，可读取电表或按时长折算用电量"

    def read_meter(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """模拟远程抄表：读不到数时报明原因，同一条记录可反复重试。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接电单 {entry_id} 不存在"
        if entry.get("duplicate"):
            return None, "重复接电记录不参与计量"
        if not entry.get("断电时刻"):
            return None, "尚未登记断电时刻，无法抄表结算"
        if entry["计量状态"] == STATUS_METERED:
            return None, "用电量已结算，如需变更读数请走手工改数流程"

        meter = str(entry.get("表计编号") or "").strip()
        attempts = int(entry.get("read_attempts") or 0) + 1
        entry["read_attempts"] = attempts

        if not meter:
            reason = "未绑定计量表，无法远程抄表：请用原有方式手工抄录读数，或按时长折算"
        elif "ERR" in meter.upper():
            reason = f"计量表 {meter} 故障：返回数据校验码错误，已重试 {attempts} 次仍失败；请联系仪表班检修，或手工补录/按时长折算"
        elif meter_digits(meter) % 10 == 3 and attempts < 2:
            # 尾号 3 的表计第一次通信超时，重试可成功——用来演示「报明原因并允许重试」
            reason = f"第 {attempts} 次抄表失败：计量表 {meter} 通信超时（RS-485 总线无响应），请重试"
        else:
            duration = entry.get("接电时长(h)") or 0.0
            base = _to_float(entry.get("起始读数"))
            if base is None:
                base = float(1000 + meter_digits(meter) * 7)
            end_reading = base + float(duration) * REFERENCE_POWER_KW
            entry["起始读数"] = _num(base)
            entry["结束读数"] = _num(end_reading)
            entry["读数来源"] = "远程抄表"
            entry["meter_error"] = None
            self._recompute(entry)
            tip = "（首次失败后重试成功）" if attempts > 1 else ""
            return entry, f"抄表成功：读数差 {entry['用电量(kWh)']} kWh，用电量已结算{tip}"

        entry["meter_error"] = reason
        self._recompute(entry)
        return None, f"计量表读不到数：{reason}"

    def manual_reading(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """沿用原有抄表方式：人工填起始/结束读数，按读数差结算。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接电单 {entry_id} 不存在"
        if entry.get("duplicate"):
            return None, "重复接电记录不参与计量"
        if not entry.get("断电时刻"):
            return None, "尚未登记断电时刻，请先登记断电再补录读数"
        if entry["计量状态"] == STATUS_METERED:
            return None, "用电量已结算，如需变更读数请走改数流程"
        try:
            start_reading = _to_float(values.get("起始读数"))
            end_reading = _to_float(values.get("结束读数"))
        except ValueError as exc:
            return None, str(exc)
        if start_reading is None:
            start_reading = _to_float(entry.get("起始读数"))
        if end_reading is None:
            return None, "请填写结束读数"
        if start_reading is None:
            return None, "该记录没有起始读数，请同时补录起始读数与结束读数"
        if end_reading < start_reading:
            return None, "结束读数不能小于起始读数"
        entry["起始读数"] = _num(start_reading)
        entry["结束读数"] = _num(end_reading)
        entry["读数来源"] = "手工抄录"
        entry["meter_error"] = None
        entry.pop("强制口径", None)
        self._recompute(entry)
        return entry, f"手工抄录已保存，按读数差结算用电量 {entry['用电量(kWh)']} kWh"

    def estimate_by_duration(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """表计与人工读数都不可得时的兜底：接电时长 × 参考功率。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接电单 {entry_id} 不存在"
        if entry.get("duplicate"):
            return None, "重复接电记录不参与计量"
        if not entry.get("断电时刻"):
            return None, "尚未登记断电时刻，无法按时长折算"
        if entry["计量状态"] == STATUS_METERED:
            return None, "用电量已按读数差结算，不再重复折算"
        entry["强制口径"] = CALIBER_ESTIMATE
        entry["meter_error"] = None
        self._recompute(entry)
        return entry, (
            f"已按时长折算：{entry['接电时长(h)']} h × {REFERENCE_POWER_KW:g} kW "
            f"= {entry['用电量(kWh)']} kWh"
        )

    # ------------------------------------------------------------------ 看板
    def dashboard(self, month: str | None = None) -> dict[str, Any]:
        """按船公司、日期、接电时段汇总；所有数字都从用电记录逐笔算出。"""
        rows = [
            row
            for row in self._select_rows(month=month)
            if not row.get("duplicate")
        ]
        metered = [row for row in rows if row["计量状态"] == STATUS_METERED]

        total_kwh = _num(sum(float(row.get("用电量(kWh)") or 0) for row in metered))
        total_hours = _num(sum(float(row.get("接电时长(h)") or 0) for row in metered))
        total_carbon = _num(sum(float(row.get("碳排放(kgCO2)") or 0) for row in metered))

        company_map: dict[str, dict[str, Any]] = {}
        for row in metered:
            bucket = company_map.setdefault(
                row["船公司"],
                {"船公司": row["船公司"], "用电量(kWh)": 0.0, "接电时长(h)": 0.0,
                 "碳排放(kgCO2)": 0.0, "记录数": 0},
            )
            bucket["用电量(kWh)"] += float(row["用电量(kWh)"])
            bucket["接电时长(h)"] += float(row["接电时长(h)"])
            bucket["碳排放(kgCO2)"] += float(row["碳排放(kgCO2)"])
            bucket["记录数"] += 1
        by_company = [
            {
                **bucket,
                "用电量(kWh)": _num(bucket["用电量(kWh)"]),
                "接电时长(h)": _num(bucket["接电时长(h)"]),
                "碳排放(kgCO2)": _num(bucket["碳排放(kgCO2)"]),
            }
            for bucket in sorted(
                company_map.values(), key=lambda item: item["用电量(kWh)"], reverse=True
            )
        ]

        date_map: dict[str, float] = {}
        for row in metered:
            day = str(row["接电时刻"])[:10]
            date_map[day] = date_map.get(day, 0.0) + float(row["用电量(kWh)"])
        by_date = [
            {"日期": day, "用电量(kWh)": _num(date_map[day])}
            for day in sorted(date_map)
        ]

        slot_values = [0.0, 0.0, 0.0, 0.0]
        slot_counts = [0, 0, 0, 0]
        for row in rows:
            start_dt = _parse_dt(row.get("接电时刻"))
            if start_dt is None:
                continue
            index = min(start_dt.hour // 6, 3)
            slot_counts[index] += 1
            if row["计量状态"] == STATUS_METERED:
                slot_values[index] += float(row["用电量(kWh)"])
        by_slot = [
            {"时段": SLOT_LABELS[i], "用电量(kWh)": _num(slot_values[i]),
             "接电记录数": slot_counts[i]}
            for i in range(4)
        ]

        caliber_map: dict[str, float] = {}
        for row in metered:
            caliber = str(row.get("计量口径") or CALIBER_METER)
            caliber_map[caliber] = caliber_map.get(caliber, 0.0) + float(
                row["用电量(kWh)"]
            )
        by_caliber = [
            {"计量口径": caliber, "用电量(kWh)": _num(kwh)}
            for caliber, kwh in sorted(caliber_map.items(), key=lambda item: -item[1])
        ]

        open_count = sum(1 for row in rows if not row.get("断电时刻"))
        failed_count = sum(1 for row in rows if row.get("meter_error"))

        return {
            "month": month or "全部",
            "cards": [
                {"label": "总用电量(kWh)", "value": total_kwh},
                {"label": "碳排放估算(kgCO₂)", "value": total_carbon},
                {"label": "已计量记录", "value": len(metered)},
                {"label": "缺断电时刻", "value": open_count},
                {"label": "抄表失败", "value": failed_count},
                {"label": "重复接电(不计费)", "value": sum(
                    1 for row in self._select_rows(month=month) if row.get("duplicate")
                )},
            ],
            "totalKwh": total_kwh,
            "totalHours": total_hours,
            "totalCarbon": total_carbon,
            "byCompany": by_company,
            "byDate": by_date,
            "bySlot": by_slot,
            "byCaliber": by_caliber,
        }

    def consistency(self, month: str | None = None) -> dict[str, Any]:
        """看板合计与逐笔用电记录的对账：两边都从同一份记录算，应当完全一致。"""
        rows = [
            row
            for row in self._select_rows(month=month)
            if not row.get("duplicate") and row["计量状态"] == STATUS_METERED
        ]
        records_kwh = _num(sum(float(row.get("用电量(kWh)") or 0) for row in rows))
        board = self.dashboard(month)
        dashboard_kwh = board["totalKwh"]
        return {
            "month": month or "全部",
            "dashboardKwh": dashboard_kwh,
            "recordsKwh": records_kwh,
            "meteredCount": len(rows),
            "ok": abs(float(dashboard_kwh) - float(records_kwh)) < 0.01,
        }

    # ------------------------------------------------------------------ 内部
    def _select_rows(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        company: str | None = None,
        month: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        result = []
        for row in rows:
            if keyword:
                haystack = "".join(
                    str(row.get(field) or "")
                    for field in ("接电单号", "船舶名称", "船公司", "表计编号")
                )
                if keyword not in haystack:
                    continue
            if status and row.get("计量状态") != status:
                continue
            if company and company not in str(row.get("船公司") or ""):
                continue
            if month and not str(row.get("接电时刻") or "").startswith(month):
                continue
            result.append(row)
        return result

    def _next_code(self, rows: list[dict[str, Any]]) -> str:
        return f"SP-{max((int(row.get('id', 0)) for row in rows), default=0) + 1:04d}"

    def _find_overlap(self, target: dict[str, Any]) -> dict[str, Any] | None:
        target_start = _parse_dt(target.get("接电时刻"))
        if target_start is None:
            return None
        target_end = _parse_dt(target.get("断电时刻"))
        for row in store.rows(MODULE):
            if row.get("id") == target.get("id") or row.get("duplicate"):
                continue
            if row.get("船舶名称") != target.get("船舶名称"):
                continue
            other_start = _parse_dt(row.get("接电时刻"))
            other_end = _parse_dt(row.get("断电时刻"))
            if other_start is not None and _intervals_overlap(
                other_start, other_end, target_start, target_end
            ):
                return row
        return None

    def _mark_duplicate(self, row: dict[str, Any], earlier: dict[str, Any]) -> None:
        row["duplicate"] = True
        row["duplicate_of"] = earlier.get("id")
        row["duplicate_reason"] = (
            f"与 {earlier.get('接电单号')}（{earlier.get('接电时刻')} ~ "
            f"{earlier.get('断电时刻') or '未断电'}）接电时段重叠，重复接电只算一次"
        )
        row["用电量(kWh)"] = None
        row["碳排放(kgCO2)"] = None
        row["计量口径"] = None
        row["计量状态"] = STATUS_DUPLICATE
        row["缺断电时刻"] = False
        row["表计说明"] = row["duplicate_reason"]
        row["status"] = STATUS_DUPLICATE
        row["pending"] = False
        row["abnormal"] = False

    def _recompute(self, row: dict[str, Any]) -> None:
        """按统一口径重算时长、用电量、碳排放、状态与说明字段。"""
        row.setdefault("meter_error", None)
        row.setdefault("read_attempts", 0)
        row.setdefault("duplicate", False)

        start_dt = _parse_dt(row.get("接电时刻"))
        end_dt = _parse_dt(row.get("断电时刻"))
        missing_end = start_dt is not None and end_dt is None
        duration = (
            _num((end_dt - start_dt).total_seconds() / 3600.0)
            if start_dt is not None and end_dt is not None
            else None
        )
        row["接电时长(h)"] = duration
        row["缺断电时刻"] = missing_end

        start_reading = _to_float(row.get("起始读数"))
        end_reading = _to_float(row.get("结束读数"))

        kwh: float | None = None
        caliber: str | None = None
        if not row.get("duplicate") and end_dt is not None:
            if (
                start_reading is not None
                and end_reading is not None
                and end_reading >= start_reading
            ):
                kwh = _num(end_reading - start_reading)
                caliber = (
                    CALIBER_MANUAL
                    if row.get("读数来源") == "手工抄录"
                    else CALIBER_METER
                )
            elif row.get("强制口径") == CALIBER_ESTIMATE and duration is not None:
                kwh = _num(float(duration) * REFERENCE_POWER_KW)
                caliber = CALIBER_ESTIMATE

        row["用电量(kWh)"] = kwh
        row["碳排放(kgCO2)"] = _num(kwh * CARBON_FACTOR) if kwh is not None else None
        row["计量口径"] = caliber

        if row.get("duplicate"):
            status = STATUS_DUPLICATE
        elif missing_end:
            status = STATUS_CONNECTED
        elif kwh is not None:
            status = STATUS_METERED
        else:
            status = STATUS_WAITING
        row["计量状态"] = status
        row["status"] = status
        row["pending"] = status in (STATUS_CONNECTED, STATUS_WAITING)
        row["abnormal"] = bool(row.get("meter_error")) and status == STATUS_WAITING

        if row.get("duplicate"):
            row["表计说明"] = row.get("duplicate_reason")
        elif missing_end:
            row["表计说明"] = "接电中：断电时刻缺失，暂不计时长与用电量"
        elif status == STATUS_WAITING and row.get("meter_error"):
            row["表计说明"] = (
                f"第 {row.get('read_attempts', 0)} 次抄表失败：{row['meter_error']}"
            )
        elif status == STATUS_WAITING:
            row["表计说明"] = "已断电：待读取电表、手工补录读数或按时长折算"
        else:
            row["表计说明"] = f"已计量（{caliber}）"


shorepower_service = ShorepowerService()
store.register_bootstrap(MODULE, shorepower_service)
