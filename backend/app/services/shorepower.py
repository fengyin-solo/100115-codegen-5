"""岸电接入计量业务规则。

口径要点：
- 用电量优先按计量表读数差（止码－起码）结算；读不到表时按接电时长估算，
  估算电量 = 接电时长(小时) × 约定受电功率(kW)，结果统一保留 2 位小数（kWh）。
- 同一艘船在同一时段（接电区间重叠）重复登记的，后一条标记为「重复接电」，
  计量只算最早的一条，重复记录保留可查、不进看板合计。
- 断电时刻缺失的记录状态为「接电中」，单独列出；其按时长估算的电量仅供临时匡算，
  不计入累计用电量。
- 看板的所有数字都由本服务根据用电记录实时汇总，刷新即与记录一致，不做缓存。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "shorepower"

# 接电登记必填：船、公司、接电时刻缺一不可；计量表与功率可在抄表失败后补录。
REQUIRED_FIELDS = ["船舶名称", "所属船公司", "接电时刻"]
# 断电登记只需补断电时刻。
DISCONNECT_FIELDS = ["断电时刻"]
STATUS_CONNECTED = "接电中"
STATUS_DISCONNECTED = "已断电"
STATUS_DUPLICATED = "重复接电"
ALL_STATUSES = [STATUS_CONNECTED, STATUS_DISCONNECTED, STATUS_DUPLICATED]

# 计量表抄表失败的常见原因；模拟表读不到数时从这里取，重试时逐条轮换。
METER_FAILURE_REASONS = [
    "计量表通讯超时（RS485 总线无应答）",
    "计量表离线（设备断电或网络中断）",
    "计量表读数异常（止码小于起码，疑似表计故障）",
]

# 看板分组方式：按时段（接电日期 或 接电小时）/按船公司。
GROUP_PERIODS = ["day", "hour"]
GROUP_OPTIONS = ["day", "hour", "company"]


def parse_dt(value: Any) -> datetime | None:
    """把前端传来的时间字符串解析成 datetime；认不出来时返回 None 交调用方提示。"""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    # datetime-local 给的是 2026-09-21T08:00，一并兼容空格分隔和带秒的写法。
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.hour or value.minute or value.second:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.strftime("%Y-%m-%d")


def _overlap(start_a: datetime, end_a: datetime | None, start_b: datetime, end_b: datetime | None) -> bool:
    """两个接电区间是否重叠。断电时刻缺失时，按「仍未断电」处理，右边界取当前时刻。"""
    now = datetime.now()
    a_end = end_a or now
    b_end = end_b or now
    return start_a <= b_end and start_b <= a_end


class ShorePowerService:
    # ---- 列表与明细 ----------------------------------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        company: str | None = None,
        status: str | None = None,
        missing_disconnect: bool | None = None,
        duplicated: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._decorate(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows
                    if keyword in str(row.get("船舶名称", ""))
                    or keyword in str(row.get("记录编号", ""))]
        if company:
            rows = [row for row in rows if row.get("所属船公司") == company]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if missing_disconnect:
            rows = [row for row in rows
                    if not row.get("断电时刻") and row.get("status") != STATUS_DUPLICATED]
        if duplicated is not None:
            rows = [row for row in rows if bool(row.get("duplicated")) == duplicated]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._decorate(row) if row else None

    # ---- 登记接电 / 断电 ------------------------------------------------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记一条接电记录。时间格式错误、断电早于接电等情况直接报明原因。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"

        start_dt = parse_dt(values.get("接电时刻"))
        if start_dt is None:
            return None, "接电时刻格式无法识别，请按 2026-09-21 08:00 的格式填写"
        end_text = str(values.get("断电时刻") or "").strip()
        end_dt = parse_dt(end_text) if end_text else None
        if end_text and end_dt is None:
            return None, "断电时刻格式无法识别，请按 2026-09-21 18:00 的格式填写"
        if end_dt is not None and end_dt < start_dt:
            return None, "断电时刻不能早于接电时刻，请核对后重新登记"

        power_kw = self._parse_power(values.get("约定受电功率(kW)"))
        if power_kw is None:
            return None, "约定受电功率需为不小于 0 的数字，单位 kW"

        rows = store.rows(MODULE)

        # 同一艘船同一时段重复接电：只认最早的一条，新登记的标重复、不参与计量。
        duplicated = any(
            str(row.get("船舶名称", "")).strip() == str(values.get("船舶名称", "")).strip()
            and not row.get("duplicated")
            and _overlap(
                parse_dt(row.get("接电时刻")) or start_dt,
                parse_dt(row.get("断电时刻") or ""),
                start_dt,
                end_dt,
            )
            for row in rows
        )

        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "记录编号": f"SP-{datetime.now():%Y%m%d}-{max((int(row.get('id', 0)) for row in rows), default=0) + 1:03d}",
            "船舶名称": str(values.get("船舶名称", "")).strip(),
            "所属船公司": str(values.get("所属船公司", "")).strip(),
            "接电泊位": str(values.get("接电泊位") or "").strip() or "未填写",
            "接电时刻": _fmt_dt(start_dt),
            "断电时刻": _fmt_dt(end_dt),
            "约定受电功率(kW)": power_kw,
            # 计量表读数：人工填写直接采信；没填则尝试向计量表抄取，抄不到给出失败原因。
            "起始读数(kWh)": None,
            "结束读数(kWh)": None,
            "抄表状态": "未抄表",
            "抄表失败原因": "",
            "抄表次数": 0,
            "status": STATUS_DUPLICATED if duplicated else (STATUS_DISCONNECTED if end_dt else STATUS_CONNECTED),
            "duplicated": duplicated,
            "pending": not duplicated and end_dt is None,
            "abnormal": False,
        }

        start_reading = self._parse_reading(values.get("起始读数(kWh)"))
        if start_reading is not None:
            entry["起始读数(kWh)"] = start_reading
            entry["抄表状态"] = "起码已录"
        elif not duplicated:
            self._read_meter(entry, "start")

        # 已断电的记录顺带尝试止码；接电中的不抄止码（电还没用完）。
        if not duplicated and entry["status"] == STATUS_DISCONNECTED:
            end_reading = self._parse_reading(values.get("结束读数(kWh)"))
            if end_reading is not None:
                entry["结束读数(kWh)"] = end_reading
            else:
                self._read_meter(entry, "end")

        rows.append(entry)
        return self._decorate(entry), (
            "同一艘船在该时段已有接电记录，本次登记标记为「重复接电」，计量只算一次"
            if duplicated else "接电记录已登记"
        )

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接电记录 {entry_id} 不存在"
        values = values or {}
        if action == "登记断电":
            return self._register_disconnect(entry, values)
        if action == "重试抄表":
            return self._retry_reading(entry, values)
        return None, f"动作「{action}」不属于岸电计量可执行范围"

    def _register_disconnect(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if entry.get("duplicated"):
            return entry, "重复接电记录不参与计量，无需登记断电"
        if entry.get("断电时刻"):
            return entry, "该记录已登记断电时刻，请勿重复操作"
        end_dt = parse_dt(values.get("断电时刻"))
        if end_dt is None:
            return entry, "断电时刻格式无法识别，请按 2026-09-21 18:00 的格式填写"
        start_dt = parse_dt(entry.get("接电时刻"))
        if start_dt is not None and end_dt < start_dt:
            return entry, "断电时刻不能早于接电时刻，请核对后重新登记"
        entry["断电时刻"] = _fmt_dt(end_dt)
        entry["status"] = STATUS_DISCONNECTED
        entry["pending"] = False

        # 有起码就接着抄止码；没有起码（一开始就抄失败）时把起、止一起补齐。
        manual_end = self._parse_reading(values.get("结束读数(kWh)"))
        if entry.get("起始读数(kWh)") is None:
            manual_start = self._parse_reading(values.get("起始读数(kWh)"))
            if manual_start is not None:
                entry["起始读数(kWh)"] = manual_start
            else:
                self._read_meter(entry, "start")
        if manual_end is not None:
            entry["结束读数(kWh)"] = manual_end
        if entry.get("起始读数(kWh)") is not None and entry.get("结束读数(kWh)") is None:
            self._read_meter(entry, "end")
        return self._decorate(entry), "断电时刻已登记"

    def _retry_reading(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if entry.get("duplicated"):
            return entry, "重复接电记录不参与计量，无需抄表"
        disconnected = bool(entry.get("断电时刻"))

        # 人工补录读数：现场核对后录入直接采信；止码需先有断电时刻才能补。
        manual_start = self._parse_reading(values.get("起始读数(kWh)"))
        manual_end = self._parse_reading(values.get("结束读数(kWh)"))
        if manual_end is not None and not disconnected:
            return self._decorate(entry), "该记录尚未登记断电时刻，暂不能补录止码"
        if manual_start is not None:
            entry["起始读数(kWh)"] = manual_start
            entry["抄表次数"] = int(entry.get("抄表次数", 0)) + 1
            entry["抄表失败原因"] = ""
        if manual_end is not None:
            entry["结束读数(kWh)"] = manual_end
            entry["抄表次数"] = int(entry.get("抄表次数", 0)) + 1
            entry["抄表失败原因"] = ""

        # 自动重试：缺起码先补起码（在接期间也可重试）；已断电的再补止码。
        retried = [manual_start is not None, manual_end is not None]
        if entry.get("起始读数(kWh)") is None:
            self._read_meter(entry, "start")
            retried[0] = True
        if disconnected and entry.get("起始读数(kWh)") is not None and entry.get("结束读数(kWh)") is None:
            self._read_meter(entry, "end")
            retried[1] = True
        if not any(retried):
            return self._decorate(entry), "读数已齐全，无需重试；如对读数有异议可人工补录覆盖"

        # 两次读取互相独立，止码可能在起码成功后仍失败：清掉陈旧原因后以最后结果为准。
        decorated = self._decorate(entry)
        start_v = entry.get("起始读数(kWh)")
        end_v = entry.get("结束读数(kWh)")
        if start_v is not None and end_v is not None and float(end_v) < float(start_v):
            # 止码小于起码属表计/录入异常：止码作废重新抄取，起码保留，避免卡死重试。
            entry["结束读数(kWh)"] = None
            entry["抄表失败原因"] = ""
            if disconnected:
                self._read_meter(entry, "end")
            decorated = self._decorate(entry)
        if decorated["抄表失败原因"]:
            return decorated, (
                f"重试后计量表仍读不到数：{decorated['抄表失败原因']}，可稍后再次重试或人工补录读数"
            )
        if not disconnected:
            return decorated, "起码已重新获取；止码待登记断电时刻后抄取"
        return decorated, "计量表读数已获取，用电量已按读数差结算"

    # ---- 看板 ----------------------------------------------------------------

    def companies(self) -> list[str]:
        names = {str(row.get("所属船公司", "")).strip()
                 for row in store.rows(MODULE) if row.get("所属船公司")}
        return sorted(names)

    def dashboard(self, group_by: str = "day", company: str | None = None) -> dict[str, Any]:
        """用电量看板：全部指标实时遍历用电记录汇总，刷新后必然与列表一致。"""
        if group_by not in GROUP_OPTIONS:
            group_by = "day"

        records = [self._decorate(row) for row in store.rows(MODULE)]
        if company:
            records = [row for row in records if row.get("所属船公司") == company]

        effective = [row for row in records if not row.get("duplicated")]
        settled = [row for row in effective if row["status"] == STATUS_DISCONNECTED]
        active = [row for row in effective if row["status"] == STATUS_CONNECTED]
        failed = [row for row in effective if row["抄表失败原因"]]
        duplicated_rows = [row for row in records if row.get("duplicated")]

        groups: dict[str, dict[str, Any]] = {}

        def bucket(row: dict[str, Any]) -> str:
            start_dt = parse_dt(row.get("接电时刻"))
            if group_by == "hour" and start_dt is not None:
                return start_dt.strftime("%m-%d %H:00")
            if group_by == "company":
                return str(row.get("所属船公司") or "未知船公司")
            return (start_dt.strftime("%Y-%m-%d") if start_dt else "未知日期")

        def add_bucket(row: dict[str, Any], *, tentative: bool) -> None:
            key = bucket(row)
            item = groups.setdefault(key, {
                "时段": key,
                "接电次数": 0,
                "表计电量(kWh)": 0.0,
                "估算电量(kWh)": 0.0,
                "用电量合计(kWh)": 0.0,
                "接电总时长(h)": 0.0,
                "临时匡算电量(kWh)": 0.0,
            })
            item["接电次数"] += 1
            item["接电总时长(h)"] += float(row["接电时长(h)"] or 0)
            if tentative:
                item["临时匡算电量(kWh)"] = round(
                    item["临时匡算电量(kWh)"] + float(row["估算用电量(kWh)"] or 0), 2)
                return
            if row["计量方式"] == "计量表读数差":
                item["表计电量(kWh)"] = round(
                    item["表计电量(kWh)"] + float(row["用电量(kWh)"] or 0), 2)
            else:
                item["估算电量(kWh)"] = round(
                    item["估算电量(kWh)"] + float(row["用电量(kWh)"] or 0), 2)
            item["用电量合计(kWh)"] = round(
                item["用电量合计(kWh)"] + float(row["用电量(kWh)"] or 0), 2)

        for row in settled:
            add_bucket(row, tentative=False)
        for row in active:
            add_bucket(row, tentative=True)

        groups_list = sorted(groups.values(), key=lambda item: str(item["时段"]))
        max_value = max((item["用电量合计(kWh)"] for item in groups_list), default=0)
        for item in groups_list:
            item["占比(%)"] = round(item["用电量合计(kWh)"] / max_value * 100, 1) if max_value else 0

        # 按船公司汇总：月底算电费、算碳排放直接取这一段。
        by_company: dict[str, dict[str, Any]] = {}
        for row in settled:
            name = str(row.get("所属船公司") or "未知船公司")
            item = by_company.setdefault(name, {
                "所属船公司": name,
                "接电次数": 0,
                "用电量合计(kWh)": 0.0,
                "表计电量(kWh)": 0.0,
                "估算电量(kWh)": 0.0,
                "接电总时长(h)": 0.0,
                "在接未断电(艘次)": 0,
                "临时匡算电量(kWh)": 0.0,
            })
            item["接电次数"] += 1
            item["接电总时长(h)"] = round(item["接电总时长(h)"] + float(row["接电时长(h)"] or 0), 2)
            item["用电量合计(kWh)"] = round(item["用电量合计(kWh)"] + float(row["用电量(kWh)"] or 0), 2)
            if row["计量方式"] == "计量表读数差":
                item["表计电量(kWh)"] = round(item["表计电量(kWh)"] + float(row["用电量(kWh)"] or 0), 2)
            else:
                item["估算电量(kWh)"] = round(item["估算电量(kWh)"] + float(row["用电量(kWh)"] or 0), 2)
        for row in active:
            name = str(row.get("所属船公司") or "未知船公司")
            item = by_company.setdefault(name, {
                "所属船公司": name, "接电次数": 0, "用电量合计(kWh)": 0.0,
                "表计电量(kWh)": 0.0, "估算电量(kWh)": 0.0, "接电总时长(h)": 0.0,
                "在接未断电(艘次)": 0, "临时匡算电量(kWh)": 0.0,
            })
            item["在接未断电(艘次)"] += 1
            item["临时匡算电量(kWh)"] = round(
                item["临时匡算电量(kWh)"] + float(row["估算用电量(kWh)"] or 0), 2)
        company_list = sorted(by_company.values(), key=lambda item: -item["用电量合计(kWh)"])

        total_kwh = round(sum(float(row["用电量(kWh)"] or 0) for row in settled), 2)
        metered_kwh = round(sum(
            float(row["用电量(kWh)"] or 0) for row in settled
            if row["计量方式"] == "计量表读数差"), 2)
        estimated_kwh = round(total_kwh - metered_kwh, 2)
        total_hours = round(sum(float(row["接电时长(h)"] or 0) for row in settled), 2)
        tentative_kwh = round(sum(float(row["估算用电量(kWh)"] or 0) for row in active), 2)

        return {
            "filters": {"group_by": group_by, "company": company or ""},
            "cards": [
                {"label": "累计用电量(kWh)", "value": total_kwh, "hint": "仅含已断电记录"},
                {"label": "表计电量(kWh)", "value": metered_kwh, "hint": "按止码－起码结算"},
                {"label": "时长估算电量(kWh)", "value": estimated_kwh, "hint": "抄表失败时按功率×时长估算"},
                {"label": "已结算艘次", "value": len(settled), "hint": "重复接电只算一次"},
                {"label": "在接未断电(艘次)", "value": len(active), "hint": "不计入累计用电量"},
                {"label": "在接临时匡算(kWh)", "value": tentative_kwh, "hint": "仅供当班参考"},
                {"label": "读表失败(条)", "value": len(failed), "hint": "可重试或人工补录"},
                {"label": "重复接电(条)", "value": len(duplicated_rows), "hint": "保留可查、不参与计量"},
            ],
            "groups": groups_list,
            "by_company": company_list,
            "missing_disconnect": [self._compact(row) for row in active],
            "meter_failures": [self._compact(row) for row in failed],
            "conversion": self.rules(),
        }

    def rules(self) -> dict[str, Any]:
        """接电时长与用电量的换算口径，前端看板原样展示。"""
        return {
            "duration": "接电时长 = 断电时刻 − 接电时刻，单位小时，保留 2 位小数；"
                        "断电时刻缺失时以当前时刻临时匡算，登记断电后改按实际时刻结算。",
            "metered": "用电量优先按计量表读数差结算：用电量(kWh) = 结束读数 − 起始读数。",
            "estimated": "计量表读不到数时，按接电时长估算：用电量(kWh) = 接电时长(h) × "
                         "约定受电功率(kW)，结果保留 2 位小数；计量方式标记为「时长估算」。",
            "dedup": "同一艘船、接电时段重叠的重复登记只计最早一条，后续记录标记「重复接电」，"
                     "不计入用电量与艘次，但保留可查。",
            "missing": "断电时刻缺失的记录标记「接电中」单独列出，临时匡算电量不计入累计用电量，"
                       "补登断电时刻后方可结算。",
            "retry": "计量表抄不到数时记录失败原因，可通过「重试抄表」重新读取，也可人工补录读数；"
                     "读数差为负等异常值按失败处理并报明原因。",
        }

    # ---- 派生字段：看板和列表都从这里算，保证两处口径一致 ----------------------

    def _decorate(self, row: dict[str, Any]) -> dict[str, Any]:
        """根据原始登记字段实时派生时长、电量等展示字段，不在记录里固化结果。"""
        item = dict(row)
        start_dt = parse_dt(row.get("接电时刻"))
        end_dt = parse_dt(row.get("断电时刻") or "")
        provisional = end_dt is None and start_dt is not None and not row.get("duplicated")
        if provisional:
            end_dt = datetime.now()

        hours = 0.0
        if start_dt is not None and end_dt is not None:
            hours = max((end_dt - start_dt).total_seconds() / 3600, 0)
        hours = round(hours, 2)
        item["接电时长(h)"] = hours

        start_reading = row.get("起始读数(kWh)")
        end_reading = row.get("结束读数(kWh)")
        power_kw = self._to_float(row.get("约定受电功率(kW)"), 0.0)

        kwh: float | None = None
        method = "暂未结算"
        state = "待结算"
        reason = str(row.get("抄表失败原因") or "")

        if row.get("duplicated"):
            method = "重复接电不计量"
            state = "重复接电"
        elif start_reading is not None and end_reading is not None:
            diff = round(float(end_reading) - float(start_reading), 2)
            if diff < 0:
                # 读数倒走视为表计异常：宁可估算也不结算负电量。
                reason = reason or "止码小于起码，读数异常"
                state = "读表失败"
                method = "时长估算(待读数修复)"
            else:
                kwh = diff
                method = "计量表读数差"
                state = "已结算"
        elif row.get("断电时刻"):
            # 已断电但读数没抄全：走时长估算兜底。
            kwh = round(hours * float(power_kw or 0), 2)
            method = "时长估算"
            state = "读表失败" if reason else "已估算"
        else:
            # 接电中：给临时匡算值，但不参与合计；起码抄失败时仍归入读表失败清单。
            kwh = None
            item["估算用电量(kWh)"] = round(hours * float(power_kw or 0), 2)
            state = "读表失败" if reason else "在接未断电"

        item["用电量(kWh)"] = kwh
        item["估算用电量(kWh)"] = item.get("估算用电量(kWh)", kwh if method == "时长估算" else None)
        item["计量方式"] = method
        item["计量状态"] = state
        item["缺失断电时刻"] = bool(not row.get("断电时刻") and not row.get("duplicated"))
        item["抄表失败原因"] = reason
        item.setdefault("抄表次数", 0)
        item.setdefault("抄表状态", "未抄表")
        item["abnormal"] = bool(state == "读表失败")
        return item

    def _compact(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "记录编号": row.get("记录编号"),
            "船舶名称": row.get("船舶名称"),
            "所属船公司": row.get("所属船公司"),
            "接电时刻": row.get("接电时刻"),
            "断电时刻": row.get("断电时刻") or "",
            "接电时长(h)": row.get("接电时长(h)"),
            "用电量(kWh)": row.get("用电量(kWh)"),
            "估算用电量(kWh)": row.get("估算用电量(kWh)"),
            "计量方式": row.get("计量方式"),
            "计量状态": row.get("计量状态"),
            "抄表失败原因": row.get("抄表失败原因"),
            "抄表次数": row.get("抄表次数", 0),
        }

    # ---- 计量表模拟与工具 -----------------------------------------------------

    def _read_meter(self, entry: dict[str, Any], which: str) -> None:
        """模拟向岸电计量表抄表：同一记录每次重试结果可能不同，失败原因逐条轮换。"""
        entry["抄表次数"] = int(entry.get("抄表次数", 0)) + 1
        entry_id = int(entry.get("id", 0))
        attempt = int(entry["抄表次数"])
        # 确定性伪随机：种子只与记录和重试次数有关，重试本身会改变结果。
        marker = (entry_id * 7 + attempt * 13 + (1 if which == "end" else 0)) % 10
        if marker in (0, 1, 2):
            entry["抄表状态"] = "读表失败"
            entry["抄表失败原因"] = METER_FAILURE_REASONS[(entry_id + attempt) % len(METER_FAILURE_REASONS)]
            if which == "start":
                entry["起始读数(kWh)"] = None
            else:
                entry["结束读数(kWh)"] = None
            return

        start_dt = parse_dt(entry.get("接电时刻"))
        end_dt = parse_dt(entry.get("断电时刻") or "")
        hours = 0.0
        if start_dt is not None:
            hours = max(((end_dt or datetime.now()) - start_dt).total_seconds() / 3600, 0)
        power = self._to_float(entry.get("约定受电功率(kW)"), 0.0)
        if which == "start":
            base = entry_id * 137.5
            entry["起始读数(kWh)"] = round(1200 + base + marker * 3.1, 1)
            entry["抄表状态"] = "起码已抄"
            entry["抄表失败原因"] = ""
        else:
            # 止码必须以已抄成功的起码为基准，真实表计不会无故倒走。
            start_reading = self._to_float(entry.get("起始读数(kWh)"), 0.0)
            entry["结束读数(kWh)"] = round(start_reading + hours * power, 1)
            entry["抄表状态"] = "起止均已抄"
            entry["抄表失败原因"] = ""

    @staticmethod
    def _parse_reading(value: Any) -> float | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return round(number, 1) if number >= 0 else None

    @staticmethod
    def _parse_power(value: Any) -> float | None:
        text = str(value if value is not None else "").strip()
        if not text:
            return 350.0  # 港口岸电常见接电容量，作为未填写时的默认约定功率
        try:
            number = float(text)
        except ValueError:
            return None
        return round(number, 2) if number >= 0 else None

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
