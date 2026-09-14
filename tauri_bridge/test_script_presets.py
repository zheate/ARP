"""Persist named parameter combinations without changing built-in execution IDs."""

from typing import Any

from .command_safety import finite_number
from .station_support import apply_aging_script_configuration


def normalize_script_presets(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 200:
        raise ValueError("测试脚本必须是列表，最多保存 200 个")
    result = []
    ids: set[str] = set()
    names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("测试脚本必须是对象")
        identity = item.get("id")
        name = item.get("name")
        if not isinstance(identity, str) or not identity.startswith("preset-") or identity in ids:
            raise ValueError("测试脚本 ID 无效或重复")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60 or name.strip().casefold() in names:
            raise ValueError("脚本名称不能为空、重复或超过 60 个字符")
        raw_params = item.get("parameters")
        if not isinstance(raw_params, dict):
            raise ValueError("测试脚本缺少参数")
        script_id = raw_params.get("selectedScriptId")
        if script_id not in {"standard", "hold-points", "aging"}:
            raise ValueError("未知的测试流程")
        mode = "range" if script_id == "standard" else "custom"
        if raw_params.get("currentPlanMode") != mode:
            raise ValueError("测试流程与电流计划不匹配")
        params = {"selectedScriptId": script_id, "currentPlanMode": mode}
        for key in ("initialCurrentA", "targetCurrentA", "currentStepA", "pointTimeoutS", "rampDownStepA", "rampDownIntervalS"):
            value = finite_number(raw_params.get(key), key)
            if key not in {"initialCurrentA", "targetCurrentA"} and value <= 0:
                raise ValueError(f"{key} 必须大于 0")
            params[key] = value
        if params["targetCurrentA"] < params["initialCurrentA"]:
            raise ValueError("目标电流不能小于起始电流")
        points = raw_params.get("customCurrentPoints")
        if not isinstance(points, list) or len(points) > 1000:
            raise ValueError("指定停留点列表无效")
        cleaned = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("指定停留点必须是对象")
            current = finite_number(point.get("currentA"), "currentA")
            dwell = finite_number(point.get("dwellS"), "dwellS")
            if mode == "custom" and (dwell <= 0 or not params["initialCurrentA"] <= current <= params["targetCurrentA"]):
                raise ValueError("指定停留点必须在起始和目标电流之间，停留时间必须大于 0")
            cleaned.append({"currentA": current, "dwellS": dwell})
        if mode == "custom" and not cleaned:
            raise ValueError("请至少设置一个指定停留点")
        params["customCurrentPoints"] = cleaned
        if script_id == "aging":
            apply_aging_script_configuration(params)
        ids.add(identity)
        names.add(name.strip().casefold())
        result.append({"id": identity, "name": name.strip(), "parameters": params})
    return result
