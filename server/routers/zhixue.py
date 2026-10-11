"""智学网数据接口 (仅管理员可用; 未配置时返回 503 与提示)"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..deps import require_admin
from .. import zhixue

router = APIRouter(prefix="/api/admin/zhixue", tags=["zhixue"])


def _call(fn, *a):
    try:
        return fn(*a)
    except zhixue.NotConfigured as e:
        raise HTTPException(503, str(e))
    except zhixue.ZhixueError as e:
        raise HTTPException(502, str(e))


@router.get("/status")
def z_status(request: Request):
    require_admin(request)
    return zhixue.status()


@router.get("/students")
def z_students(request: Request, class_id: str):
    require_admin(request)
    return {"items": _call(zhixue.list_students, class_id)}


@router.get("/exams")
def z_exams(request: Request, class_id: str = ""):
    require_admin(request)
    return {"items": _call(zhixue.list_exams, class_id)}


@router.get("/exams/{exam_id}/scores")
def z_scores(exam_id: str, request: Request):
    require_admin(request)
    return {"items": _call(zhixue.exam_scores, exam_id)}


@router.get("/students/{student_id}/report")
def z_report(student_id: str, request: Request):
    require_admin(request)
    return _call(zhixue.student_report, student_id)
