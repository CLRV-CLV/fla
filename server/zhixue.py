"""智学网数据接入 (API 层先行, 便于后期对接)

- 所有外部地址、应用凭证放在环境变量 / 管理员设置里, 代码中不写死。
- 未配置时各接口返回 NotConfigured, 不影响其它功能。
- 统一封装: 登录换取 token、学生/班级、考试列表、成绩、学情报告。
- 具体的接口路径与字段映射集中在 ENDPOINTS / 各 _map_* 函数, 对接时只改这里。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request




class NotConfigured(RuntimeError):
    pass


class ZhixueError(RuntimeError):
    pass


# 默认路径 (占位, 以智学网开放平台文档为准后修改)
ENDPOINTS = {
    "token": "/oauth/token",
    "students": "/api/v1/students",
    "exams": "/api/v1/exams",
    "scores": "/api/v1/exams/{exam_id}/scores",
    "report": "/api/v1/students/{student_id}/report",
}


def _cfg() -> dict:
    base = os.environ.get("ZHIXUE_BASE_URL", "").rstrip("/")
    app_id = os.environ.get("ZHIXUE_APP_ID", "")
    secret = os.environ.get("ZHIXUE_APP_SECRET", "")
    if not (base and app_id and secret):
        raise NotConfigured("智学网未配置: 请设置 ZHIXUE_BASE_URL / ZHIXUE_APP_ID / ZHIXUE_APP_SECRET")
    return {"base": base, "app_id": app_id, "secret": secret}


_token: dict = {"value": "", "exp": 0.0}


def _request(method: str, path: str, params: dict | None = None, body: dict | None = None,
             timeout: float = 15) -> dict:
    c = _cfg()
    url = c["base"] + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json"}
    if path != ENDPOINTS["token"]:
        headers["Authorization"] = "Bearer " + get_token()
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise ZhixueError(f"智学网返回 {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ZhixueError(f"无法连接智学网: {e}") from e


def get_token() -> str:
    """应用级 token, 过期前自动刷新"""
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    c = _cfg()
    d = _request("POST", ENDPOINTS["token"], body={"app_id": c["app_id"], "app_secret": c["secret"],
                                                  "grant_type": "client_credentials"})
    _token["value"] = d.get("access_token", "")
    _token["exp"] = time.time() + int(d.get("expires_in", 3600))
    if not _token["value"]:
        raise ZhixueError("智学网 token 获取失败")
    return _token["value"]


def status() -> dict:
    try:
        _cfg()
        return {"configured": True}
    except NotConfigured as e:
        return {"configured": False, "hint": str(e)}


def list_students(class_id: str) -> list[dict]:
    d = _request("GET", ENDPOINTS["students"], params={"class_id": class_id})
    return [_map_student(x) for x in d.get("items", [])]


def list_exams(class_id: str = "") -> list[dict]:
    d = _request("GET", ENDPOINTS["exams"], params={"class_id": class_id} if class_id else None)
    return [_map_exam(x) for x in d.get("items", [])]


def exam_scores(exam_id: str) -> list[dict]:
    d = _request("GET", ENDPOINTS["scores"].format(exam_id=urllib.parse.quote(exam_id)))
    return [_map_score(x) for x in d.get("items", [])]


def student_report(student_id: str) -> dict:
    return _request("GET", ENDPOINTS["report"].format(student_id=urllib.parse.quote(student_id)))


# ---- 字段映射: 把智学网原始字段转成 FLA 内部统一结构 (对接时按真实字段修改)
def _map_student(x: dict) -> dict:
    return {"id": str(x.get("id", "")), "name": x.get("name", ""), "number": x.get("number", "")}


def _map_exam(x: dict) -> dict:
    return {"id": str(x.get("id", "")), "name": x.get("name", ""), "date": x.get("date", "")}


def _map_score(x: dict) -> dict:
    return {"student_id": str(x.get("student_id", "")), "subject": x.get("subject", ""),
            "score": x.get("score"), "rank": x.get("rank")}
