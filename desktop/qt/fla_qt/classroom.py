"""Native, offline classroom toolbox for the FLA desktop app.

The desktop surface deliberately uses Qt widgets and QPainter only: it never
opens the web classroom page, a browser engine, a CDN, or the FLA server.
"""
from __future__ import annotations

import ast
import json
import math
import random
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSlider, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .core import data_dir
from .stage import TimeWheel


# ------------------------------- Safe math parser -------------------------------
_FUNCTIONS: dict[str, Callable] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "asinh": math.asinh, "acosh": math.acosh, "atanh": math.atanh,
    "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "abs": abs, "ln": math.log, "log": math.log10, "log10": math.log10,
    "log2": math.log2, "exp": math.exp, "floor": math.floor, "ceil": math.ceil,
    "round": round, "sign": lambda x: (x > 0) - (x < 0),
    "min": min, "max": max, "hypot": math.hypot,
    "atan2": math.atan2, "mod": math.fmod,
}
_ALLOWED_NAMES = {"x", "y", "t", "theta", "pi", "e", "tau", "a", "b", "c", "d", "k", "m", "n"}
_TOKEN_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_πθτ]+|<=|>=|==|!=|[()+\-*/^,<>=]")


def _insert_implicit_multiplication(source: str) -> str:
    src = source.replace("π", "pi").replace("θ", "theta").replace("τ", "tau")
    src = src.replace("×", "*").replace("÷", "/").replace("−", "-").replace("·", "*")
    raw = _TOKEN_RE.findall(src.replace(" ", ""))
    if not raw or "".join(raw) != re.sub(r"\s+", "", src):
        raise ValueError("包含不支持的字符")
    out: list[str] = []
    numeric = lambda t: bool(re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", t))
    value_end = lambda t: t == ")" or numeric(t) or t in _ALLOWED_NAMES
    value_start = lambda t: t == "(" or numeric(t) or t in _ALLOWED_NAMES or t in _FUNCTIONS
    for i, token in enumerate(raw):
        if i:
            prev = raw[i - 1]
            is_call = prev in _FUNCTIONS and token == "("
            if not is_call and value_end(prev) and value_start(token):
                out.append("*")
        out.append(token)
    return "".join(out).replace("^", "**")


def compile_expression(source: str) -> Callable[[dict[str, float]], float]:
    """Compile a small whitelisted math language; never uses eval/exec."""
    prepared = _insert_implicit_multiplication(source)
    tree = ast.parse(prepared, mode="eval")

    def build(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            value = float(node.value)
            return lambda env: value
        if isinstance(node, ast.Name):
            name = node.id.lower()
            if name not in _ALLOWED_NAMES:
                raise ValueError("不认识的变量: " + name)
            if name == "pi": return lambda env: math.pi
            if name == "e": return lambda env: math.e
            if name == "tau": return lambda env: math.tau
            return lambda env: float(env.get(name, 0.0))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            a = build(node.operand)
            return (lambda env: +a(env)) if isinstance(node.op, ast.UAdd) else (lambda env: -a(env))
        if isinstance(node, ast.BinOp):
            a, b = build(node.left), build(node.right)
            if isinstance(node.op, ast.Add): return lambda env: a(env) + b(env)
            if isinstance(node.op, ast.Sub): return lambda env: a(env) - b(env)
            if isinstance(node.op, ast.Mult): return lambda env: a(env) * b(env)
            if isinstance(node.op, ast.Div): return lambda env: a(env) / b(env)
            if isinstance(node.op, ast.Mod): return lambda env: a(env) % b(env)
            if isinstance(node.op, ast.Pow):
                def power(env):
                    exponent = b(env)
                    if abs(exponent) > 1000: raise OverflowError("指数过大")
                    return a(env) ** exponent
                return power
            raise ValueError("不支持的运算符")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id.lower()
            fn = _FUNCTIONS.get(name)
            if fn is None or not 1 <= len(node.args) <= 2:
                raise ValueError("不支持的函数: " + name)
            args = [build(arg) for arg in node.args]
            return lambda env: fn(*(arg(env) for arg in args))
        raise ValueError("表达式包含不支持的结构")

    calc = build(tree.body)
    def safe(env):
        value = float(calc(env))
        if not math.isfinite(value) or abs(value) > 1e100:
            raise ArithmeticError("结果超出绘图范围")
        return value
    return safe


def _split_top_level(text: str, separator=",") -> list[str]:
    depth, start, out = 0, 0, []
    for i, ch in enumerate(text):
        if ch == "(": depth += 1
        elif ch == ")": depth -= 1
        elif ch == separator and depth == 0:
            out.append(text[start:i].strip()); start = i + 1
    out.append(text[start:].strip())
    return out


@dataclass
class PlotSpec:
    kind: str
    funcs: tuple[Callable, ...]
    relation: str = "="
    label: str = ""


def parse_plot(source: str) -> PlotSpec:
    """Parse Google-style 2D inputs: functions, implicit equations, inequalities,
    polar/parametric curves and points. Parameter values are supplied at render time.
    """
    text = source.strip()
    if not text:
        raise ValueError("请输入表达式")
    # Parametric pair: (x(t), y(t)) / param(x(t), y(t)); plain (x,y) is a point.
    pair = re.fullmatch(r"(?:param\s*)?\((.*)\)", text, flags=re.I)
    if pair:
        xy = _split_top_level(pair.group(1))
        if len(xy) == 2:
            funcs = (compile_expression(xy[0]), compile_expression(xy[1]))
            parametric = text.lower().startswith("param") or bool(re.search(r"\b(?:t|theta)\b", pair.group(1), re.I))
            kind = "parametric" if parametric else "point"
            return PlotSpec(kind, funcs, label=text)
    # Named point: A=(x,y)
    point = re.fullmatch(r"[A-Za-z_]\w*\s*=\s*\((.*)\)", text)
    if point:
        xy = _split_top_level(point.group(1))
        if len(xy) == 2:
            return PlotSpec("point", (compile_expression(xy[0]), compile_expression(xy[1])), label=text)
    if re.match(r"^(?:r|rho)\s*=", text, flags=re.I):
        _, rhs = text.split("=", 1)
        return PlotSpec("polar", (compile_expression(rhs),), label=text)
    # f(x)=... and y=... are explicit functions.
    function = re.fullmatch(r"[A-Za-z_]\w*\s*\(\s*x\s*\)\s*=\s*(.+)", text, flags=re.I)
    if function:
        return PlotSpec("explicit", (compile_expression(function.group(1)),), label=text)
    relation_match = re.search(r"(<=|>=|==|!=|=|<|>)", text)
    if relation_match:
        left = text[:relation_match.start()].strip()
        relation = relation_match.group(1)
        right = text[relation_match.end():].strip()
        if not left or not right:
            raise ValueError("等式两侧都要有表达式")
        if left.lower() == "y":
            return PlotSpec("explicit", (compile_expression(right),), relation, text)
        if right.lower() == "y":
            return PlotSpec("explicit", (compile_expression(left),), _reverse_relation(relation), text)
        if left.lower() == "x":
            return PlotSpec("horizontal", (compile_expression(right),), relation, text)
        if right.lower() == "x":
            return PlotSpec("horizontal", (compile_expression(left),), _reverse_relation(relation), text)
        # An arbitrary relation F(x,y) = 0 is an implicit curve.
        lhs, rhs = compile_expression(left), compile_expression(right)
        return PlotSpec("implicit", (lambda env: lhs(env) - rhs(env),), relation, text)
    return PlotSpec("explicit", (compile_expression(text),), label=text)


def _reverse_relation(rel: str) -> str:
    return {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "=", "==": "==", "!=": "!="}.get(rel, rel)


def _relation_holds(value: float, relation: str) -> bool:
    return {"<": value < 0, "<=": value <= 0, ">": value > 0, ">=": value >= 0,
            "=": abs(value) < 1e-8, "==": abs(value) < 1e-8, "!=": abs(value) >= 1e-8}.get(relation, False)


# ---------------------------------- Graph canvas ----------------------------------
class GraphCanvas(QWidget):
    statusChanged = pyqtSignal(str)
    COLORS = ("#2563eb", "#dc2626", "#059669", "#9333ea", "#d97706", "#0891b2", "#be123c", "#4d7c0f")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 380)
        self.setMouseTracking(True)
        self.expressions: list[tuple[str, bool]] = [("y=sin(x)", True), ("y=x^2-4", True)]
        self.params = {k: 1.0 for k in "abcdkmn"}
        self.cx, self.cy, self.scale = 0.0, 0.0, 56.0
        self._drag = None
        self._mouse = None
        self._analysis: list[tuple[float, float, str, str]] = []
        self._errors: dict[int, str] = {}
        self._specs: list[tuple[int, PlotSpec]] = []
        self.setFocusPolicy(Qt.StrongFocus)

    def setExpressions(self, expressions):
        self.expressions = list(expressions)
        self.update()

    def setParameter(self, name: str, value: float):
        self.params[name] = float(value)
        self.update()

    def resetView(self):
        self.cx, self.cy, self.scale = 0.0, 0.0, 56.0
        self.update()

    def _world_x(self, px): return self.cx + (px - self.width() / 2) / self.scale
    def _world_y(self, py): return self.cy - (py - self.height() / 2) / self.scale
    def _screen_x(self, x): return (x - self.cx) * self.scale + self.width() / 2
    def _screen_y(self, y): return self.height() / 2 - (y - self.cy) * self.scale
    def _env(self, x=0.0, y=0.0, t=0.0):
        return {"x": x, "y": y, "t": t, "theta": t, **self.params}

    @staticmethod
    def _nice_step(raw):
        if raw <= 0 or not math.isfinite(raw): return 1.0
        p = 10 ** math.floor(math.log10(raw))
        m = raw / p
        return (1 if m <= 1 else 2 if m <= 2 else 5 if m <= 5 else 10) * p

    def paintEvent(self, _event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#ffffff"))
        x_min, x_max = self._world_x(0), self._world_x(w)
        y_max, y_min = self._world_y(0), self._world_y(h)
        step = self._nice_step(78 / max(self.scale, 0.001))
        p.setPen(QPen(QColor("#e8ebef"), 1))
        gx = math.floor(x_min / step) * step
        while gx <= x_max:
            sx = self._screen_x(gx); p.drawLine(QPointF(sx, 0), QPointF(sx, h)); gx += step
        gy = math.floor(y_min / step) * step
        while gy <= y_max:
            sy = self._screen_y(gy); p.drawLine(QPointF(0, sy), QPointF(w, sy)); gy += step
        p.setPen(QPen(QColor("#747b85"), 1.5))
        if y_min <= 0 <= y_max: p.drawLine(QPointF(0, self._screen_y(0)), QPointF(w, self._screen_y(0)))
        if x_min <= 0 <= x_max: p.drawLine(QPointF(self._screen_x(0), 0), QPointF(self._screen_x(0), h))
        p.setFont(QFont("Segoe UI", 8)); p.setPen(QColor("#626a75"))
        gx = math.floor(x_min / step) * step
        while gx <= x_max:
            sx = self._screen_x(gx)
            if 18 < sx < w - 20 and abs(gx) > step * .1:
                p.drawText(QRectF(sx - 28, min(h - 18, max(2, self._screen_y(0) + 3)), 56, 16), Qt.AlignHCenter, _fmt(gx))
            gx += step
        gy = math.floor(y_min / step) * step
        while gy <= y_max:
            sy = self._screen_y(gy)
            if 12 < sy < h - 18 and abs(gy) > step * .1:
                p.drawText(QRectF(2, sy - 9, max(0, self._screen_x(0) - 7), 18), Qt.AlignRight | Qt.AlignVCenter, _fmt(gy))
            gy += step
        p.setPen(QColor("#505862")); p.drawText(QPointF(8, 18), "y")
        p.drawText(QPointF(w - 18, min(h - 4, max(16, self._screen_y(0) - 5))), "x")

        self._analysis.clear(); self._errors.clear(); self._specs.clear()
        for idx, (source, visible) in enumerate(self.expressions):
            if not visible or not source.strip(): continue
            try:
                spec = parse_plot(source)
                self._specs.append((idx, spec))
                self._paint_spec(p, spec, QColor(self.COLORS[idx % len(self.COLORS)]), x_min, x_max, y_min, y_max)
            except Exception as exc:
                self._errors[idx] = str(exc)
        self._find_intersections(x_min, x_max)
        self._paint_analysis(p)
        if self._mouse is not None:
            self._paint_trace(p, *self._mouse)

    def _paint_spec(self, p, spec, color, x_min, x_max, y_min, y_max):
        if spec.kind == "point":
            x, y = (f(self._env()) for f in spec.funcs)
            self._dot(p, x, y, color, spec.label)
            return
        if spec.kind in ("explicit", "horizontal"):
            f = spec.funcs[0]
            horizontal = spec.kind == "horizontal"
            lo, hi = (y_min, y_max) if horizontal else (x_min, x_max)
            n = max(400, min(2400, int((self.width() if not horizontal else self.height()) * 1.5)))
            path = QPainterPath(); started = False; values = []
            for i in range(n + 1):
                v = lo + (hi - lo) * i / n
                try:
                    yv = f(self._env(y=v) if horizontal else self._env(x=v))
                    a, b = (yv, v) if horizontal else (v, yv)
                    if not all(math.isfinite(z) and abs(z) < 1e8 for z in (a, b)):
                        started = False; values.append(None); continue
                    sx, sy = self._screen_x(a), self._screen_y(b)
                    if sy < -self.height() * 4 or sy > self.height() * 5 or sx < -self.width() * 4 or sx > self.width() * 5:
                        started = False; values.append((a, b)); continue
                    if not started: path.moveTo(sx, sy); started = True
                    else:
                        prev = path.currentPosition()
                        if abs(sy - prev.y()) > self.height() * 1.2: path.moveTo(sx, sy)
                        else: path.lineTo(sx, sy)
                    values.append((a, b))
                except Exception:
                    started = False; values.append(None)
            if spec.relation not in ("=", "=="):
                self._shade_explicit(p, values, spec.relation, color, horizontal)
            p.setPen(QPen(color, 2.1)); p.setBrush(Qt.NoBrush); p.drawPath(path)
            if not horizontal:
                self._find_roots(values, spec.label, color)
            return
        if spec.kind in ("polar", "parametric"):
            path = QPainterPath(); started = False
            count = 900
            for i in range(count + 1):
                t = (-4 * math.pi) + 8 * math.pi * i / count if spec.kind == "polar" else -10 + 20 * i / count
                env = self._env(t=t)
                try:
                    if spec.kind == "polar":
                        r = spec.funcs[0](env); x, y = r * math.cos(t), r * math.sin(t)
                    else: x, y = spec.funcs[0](env), spec.funcs[1](env)
                    sx, sy = self._screen_x(x), self._screen_y(y)
                    if not all(math.isfinite(q) for q in (sx, sy)) or abs(sx) > self.width() * 4 or abs(sy) > self.height() * 4:
                        started = False; continue
                    if not started: path.moveTo(sx, sy); started = True
                    else: path.lineTo(sx, sy)
                except Exception: started = False
            p.setPen(QPen(color, 2.1)); p.drawPath(path); return
        if spec.kind == "implicit":
            f = spec.funcs[0]
            if spec.relation not in ("=", "=="):
                cell = 7
                p.save(); p.setPen(Qt.NoPen); p.setBrush(QColor(color.red(), color.green(), color.blue(), 42))
                for sy in range(0, self.height(), cell):
                    for sx in range(0, self.width(), cell):
                        try:
                            val = f(self._env(self._world_x(sx + cell/2), self._world_y(sy + cell/2)))
                            if _relation_holds(val, spec.relation): p.drawRect(QRectF(sx, sy, cell + 1, cell + 1))
                        except Exception: pass
                p.restore(); return
            self._draw_contour(p, f, color); return

    def _shade_explicit(self, p, points, relation, color, vertical=False):
        first = next((i for i, v in enumerate(points) if v is not None), None)
        last = next((i for i in range(len(points) - 1, -1, -1) if points[i] is not None), None)
        if first is None or last is None: return
        path = QPainterPath()
        if vertical:
            boundary = 0 if relation in ("<", "<=") else self.width()
            path.moveTo(boundary, self._screen_y(points[first][1]))
            for point in points[first:last + 1]:
                if point is not None: path.lineTo(self._screen_x(point[0]), self._screen_y(point[1]))
            path.lineTo(boundary, self._screen_y(points[last][1]))
        else:
            baseline = self.height() if relation in ("<", "<=") else 0
            path.moveTo(self._screen_x(points[first][0]), baseline)
            for point in points[first:last + 1]:
                if point is not None: path.lineTo(self._screen_x(point[0]), self._screen_y(point[1]))
            path.lineTo(self._screen_x(points[last][0]), baseline)
        path.closeSubpath()
        p.save(); p.setPen(Qt.NoPen); p.setBrush(QColor(color.red(), color.green(), color.blue(), 34)); p.drawPath(path); p.restore()

    def _draw_contour(self, p, f, color):
        step = 8; lines = []
        for sy in range(0, self.height(), step):
            for sx in range(0, self.width(), step):
                pts = [(sx, sy), (sx + step, sy), (sx + step, sy + step), (sx, sy + step)]
                vals = []
                for px, py in pts:
                    try: vals.append(f(self._env(self._world_x(px), self._world_y(py))))
                    except Exception: vals.append(float("nan"))
                hits = []
                for a, b in ((0, 1), (1, 2), (2, 3), (3, 0)):
                    va, vb = vals[a], vals[b]
                    if not (math.isfinite(va) and math.isfinite(vb)) or (va > 0) == (vb > 0): continue
                    q = abs(va) / max(1e-15, abs(va) + abs(vb))
                    hits.append(QPointF(pts[a][0] + (pts[b][0] - pts[a][0]) * q,
                                        pts[a][1] + (pts[b][1] - pts[a][1]) * q))
                if len(hits) >= 2: lines.append((hits[0], hits[1]))
        p.setPen(QPen(color, 2.0))
        for a, b in lines: p.drawLine(a, b)

    def _find_roots(self, values, label, color):
        prev = None
        for item in values:
            if item is None: prev = None; continue
            x, y = item
            if abs(y) < 1e-8:
                if all(abs(x - q[0]) > 1e-4 for q in self._analysis):
                    self._analysis.append((x, 0.0, "根", color.name()))
            if prev and prev[1] * y < 0:
                a, b, fa = prev[0], x, prev[1]
                for _ in range(28):
                    m = (a + b) / 2
                    try: fm = parse_plot(label).funcs[0](self._env(x=m))
                    except Exception: break
                    if fa * fm <= 0: b = m
                    else: a, fa = m, fm
                root = (a + b) / 2
                if not self._analysis or all(abs(root - q[0]) > (x - prev[0]) * .25 for q in self._analysis):
                    self._analysis.append((root, 0.0, "根", color.name()))
            prev = item

    def _find_intersections(self, x_min, x_max):
        funcs = [(idx, spec) for idx, spec in self._specs if spec.kind == "explicit" and spec.relation in ("=", "==")]
        if len(funcs) < 2: return
        steps = 500
        for ai in range(len(funcs)):
            for bi in range(ai + 1, len(funcs)):
                ia, a = funcs[ai]; ib, b = funcs[bi]
                try: fa, fb = a.funcs[0], b.funcs[0]
                except Exception: continue
                last = None
                for j in range(steps + 1):
                    x = x_min + (x_max - x_min) * j / steps
                    try: d = fa(self._env(x=x)) - fb(self._env(x=x))
                    except Exception: last = None; continue
                    if last and d * last[1] < 0:
                        lo, hi, flo = last[0], x, last[1]
                        for _ in range(24):
                            mid = (lo + hi) / 2
                            try: fm = fa(self._env(x=mid)) - fb(self._env(x=mid))
                            except Exception: break
                            if flo * fm <= 0: hi = mid
                            else: lo, flo = mid, fm
                        ix = (lo + hi) / 2
                        try: iy = (fa(self._env(x=ix)) + fb(self._env(x=ix))) / 2
                        except Exception: continue
                        if all(math.hypot(ix - q[0], iy - q[1]) > .04 for q in self._analysis):
                            self._analysis.append((ix, iy, "交点", self.COLORS[ia % len(self.COLORS)]))
                    last = (x, d)

    def _paint_analysis(self, p):
        p.save(); p.setFont(QFont("Segoe UI", 8))
        for x, y, name, color in self._analysis:
            sx, sy = self._screen_x(x), self._screen_y(y)
            if not (-8 < sx < self.width() + 8 and -8 < sy < self.height() + 8): continue
            p.setPen(QPen(QColor("#ffffff"), 2)); p.setBrush(QColor(color)); p.drawEllipse(QPointF(sx, sy), 4.5, 4.5)
            p.setPen(QColor("#333333")); p.drawText(QPointF(sx + 6, sy - 6), f"{name} ({_fmt(x)}, {_fmt(y)})")
        p.restore()

    def _dot(self, p, x, y, color, label=""):
        sx, sy = self._screen_x(x), self._screen_y(y)
        p.setPen(QPen(QColor("#ffffff"), 2)); p.setBrush(color); p.drawEllipse(QPointF(sx, sy), 5, 5)
        p.setPen(color); p.drawText(QPointF(sx + 7, sy - 7), label or f"({_fmt(x)}, {_fmt(y)})")

    def _paint_trace(self, p, px, py):
        wx, wy = self._world_x(px), self._world_y(py)
        p.setPen(QPen(QColor(65, 72, 82, 100), 1, Qt.DashLine))
        p.drawLine(QPointF(px, 0), QPointF(px, self.height())); p.drawLine(QPointF(0, py), QPointF(self.width(), py))
        choices = []
        for idx, spec in self._specs:
            if spec.kind != "explicit": continue
            try:
                yy = spec.funcs[0](self._env(x=wx)); sy = self._screen_y(yy)
                choices.append((abs(sy - py), wx, yy, idx))
            except Exception: pass
        nearest = min(choices) if choices else None
        if nearest:
            _, x, y, idx = nearest
            sx, sy = self._screen_x(x), self._screen_y(y)
            p.setPen(QPen(QColor("#ffffff"), 2)); p.setBrush(QColor(self.COLORS[idx % len(self.COLORS)])); p.drawEllipse(QPointF(sx, sy), 5, 5)
        self.statusChanged.emit(f"x = {_fmt(wx)}   y = {_fmt(wy)}" + (f"   曲线 y = {_fmt(nearest[2])}" if nearest else ""))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = (event.pos(), self.cx, self.cy); self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag:
            point, cx, cy = self._drag
            self.cx = cx - (event.x() - point.x()) / self.scale
            self.cy = cy + (event.y() - point.y()) / self.scale
            self.update()
        else:
            self._mouse = (event.x(), event.y()); self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = None; self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, _event):
        self._mouse = None; self.statusChanged.emit("拖动平移 · 滚轮缩放 · 根与交点自动标记"); self.update()

    def wheelEvent(self, event):
        old = self.scale; factor = 1.16 if event.angleDelta().y() > 0 else 1 / 1.16
        new = min(10000, max(5, old * factor))
        x, y = self._world_x(event.x()), self._world_y(event.y())
        self.scale = new
        self.cx = x - (event.x() - self.width() / 2) / new
        self.cy = y + (event.y() - self.height() / 2) / new
        self.update(); event.accept()

    def mouseDoubleClickEvent(self, event):
        self.resetView()


def _fmt(x):
    if abs(x) < 1e-10: return "0"
    if abs(x) >= 1e5 or abs(x) < 1e-4: return f"{x:.3g}"
    return f"{x:.4f}".rstrip("0").rstrip(".")


# -------------------------------- Classroom tabs --------------------------------
class _RosterMixin:
    def _load_roster(self):
        try:
            data = json.loads(Path(data_dir(), "classroom.json").read_text(encoding="utf-8"))
            roster = data.get("roster", [])
            if isinstance(roster, list) and roster: return [str(x) for x in roster]
        except Exception: pass
        return ["张明", "李华", "王强", "赵敏", "钱程", "孙悦", "周杰", "吴桐", "郑浩", "陈晨"]

    def _save_roster(self, roster):
        path = Path(data_dir(), "classroom.json")
        try:
            try: data = json.loads(path.read_text(encoding="utf-8"))
            except Exception: data = {}
            data["roster"] = roster
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError: pass

    def _read_roster_editor(self, editor):
        names, seen = [], set()
        for raw in re.split(r"[\r\n,，;；\t]+", editor.toPlainText()):
            name = raw.strip()
            if name and name not in seen and len(name) <= 40 and len(names) < 5000:
                names.append(name); seen.add(name)
        return names


class PickerTab(QWidget, _RosterMixin):
    def __init__(self):
        super().__init__(); self.roster = self._load_roster(); self.pool = self.roster[:]
        self._rolling = False; self._frames = 0; self._chosen: list[str] = []
        self.timer = QTimer(self, interval=45); self.timer.timeout.connect(self._animate)
        root = QHBoxLayout(self); left = QVBoxLayout(); right = QVBoxLayout()
        self.result = QLabel("准备就绪"); self.result.setAlignment(Qt.AlignCenter); self.result.setMinimumHeight(210)
        self.result.setStyleSheet("font-size:44px;font-weight:700;background:#f4f6f8;border-radius:18px;color:#18202b")
        left.addWidget(self.result, 1)
        controls = QHBoxLayout(); self.count = QComboBox()
        for n in (1, 2, 3, 5): self.count.addItem(f"抽 {n} 人", n)
        self.no_repeat = QCheckBox("本轮不重复"); self.no_repeat.setChecked(True)
        self.go = QPushButton("开始抽选"); self.go.setMinimumHeight(40); self.go.clicked.connect(self.pick)
        self.reset = QPushButton("重置候选池"); self.reset.clicked.connect(self.reset_pool)
        controls.addWidget(self.count); controls.addWidget(self.no_repeat); controls.addWidget(self.go); controls.addWidget(self.reset)
        left.addLayout(controls)
        self.status = QLabel(""); left.addWidget(self.status)
        self.history = QPlainTextEdit(); self.history.setReadOnly(True); self.history.setMaximumHeight(100)
        left.addWidget(QLabel("最近抽取")); left.addWidget(self.history)
        self.names = QPlainTextEdit("\n".join(self.roster)); self.names.setPlaceholderText("每行一个学生姓名，也支持粘贴名单")
        right.addWidget(QLabel("本机名单（不上传服务器）")); right.addWidget(self.names, 1)
        row = QHBoxLayout(); imp = QPushButton("导入名单…"); imp.clicked.connect(self.import_list)
        save = QPushButton("保存名单"); save.clicked.connect(self.save_list)
        row.addWidget(imp); row.addWidget(save); right.addLayout(row)
        root.addLayout(left, 3); root.addLayout(right, 2); self._update_status()

    def import_list(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入名单", "", "文本或 CSV (*.txt *.csv);;所有文件 (*)")
        if path:
            try: self.names.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
            except OSError as exc: QMessageBox.warning(self, "读取失败", str(exc))

    def save_list(self):
        self.roster = self._read_roster_editor(self.names)
        if not self.roster: QMessageBox.warning(self, "名单为空", "请至少输入一个姓名"); return
        self.names.setPlainText("\n".join(self.roster)); self._save_roster(self.roster); self.reset_pool()

    def reset_pool(self):
        self.pool = self.roster[:]; self._chosen.clear(); self._update_status()

    def _update_status(self):
        remaining = len(self.pool) if self.no_repeat.isChecked() else len(self.roster)
        self.status.setText(f"候选 {remaining} / {len(self.roster)} 人 · 已抽 {len(self._chosen)} 人次")

    def pick(self):
        if self._rolling: return
        edited_roster = self._read_roster_editor(self.names)
        if edited_roster and edited_roster != self.roster:
            self.roster = edited_roster
            self.pool = self.roster[:]
            self._chosen.clear()
        candidates = self.pool if self.no_repeat.isChecked() else self.roster
        if not candidates:
            self.reset_pool(); candidates = self.pool
        if not candidates: return
        count = min(int(self.count.currentData()), len(candidates)) if self.no_repeat.isChecked() else int(self.count.currentData())
        if self.no_repeat.isChecked():
            sample = candidates[:]
            self._chosen = [sample.pop(secrets.randbelow(len(sample))) for _ in range(count)]
            self.pool = [n for n in self.pool if n not in self._chosen]
        else:
            self._chosen = [self.roster[secrets.randbelow(len(self.roster))] for _ in range(count)]
        self._rolling = True; self._frames = 0; self.go.setEnabled(False); self.reset.setEnabled(False)
        self.timer.start(); self._update_status()

    def _animate(self):
        self._frames += 1
        candidates = self.roster or ["名单为空"]
        if self._frames < 24:
            self.result.setText(candidates[secrets.randbelow(len(candidates))])
            if self._frames > 12: self.timer.setInterval(min(200, 45 + (self._frames - 12) * 12))
            return
        self.timer.stop(); self.timer.setInterval(45); self._rolling = False
        self.result.setText("  ·  ".join(self._chosen)); self.go.setEnabled(True); self.reset.setEnabled(True)
        self.history.appendPlainText("、".join(self._chosen)); self._update_status()
        try: self._save_roster(self.roster)
        except Exception: pass


class GroupTab(QWidget, _RosterMixin):
    def __init__(self, picker: PickerTab):
        super().__init__(); self.picker = picker
        root = QVBoxLayout(self); bar = QHBoxLayout()
        self.mode = QComboBox(); self.mode.addItems(["按组数", "按每组人数"])
        self.value = QSpinBox(); self.value.setRange(2, 100); self.value.setValue(4)
        self.run = QPushButton("随机分组"); self.run.clicked.connect(self.make_groups)
        self.copy = QPushButton("复制结果"); self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.output.toPlainText()))
        bar.addWidget(QLabel("方式")); bar.addWidget(self.mode); bar.addWidget(QLabel("数量")); bar.addWidget(self.value)
        bar.addWidget(self.run); bar.addWidget(self.copy); bar.addStretch(1); root.addLayout(bar)
        self.output = QPlainTextEdit(); self.output.setReadOnly(True); self.output.setPlaceholderText("分组结果会显示在这里")
        root.addWidget(self.output, 1)

    def make_groups(self):
        people = self._read_roster_editor(self.picker.names)
        if not people: self.output.setPlainText("请先在随机抽人页录入名单。"); return
        random.SystemRandom().shuffle(people); v = self.value.value(); groups = []
        if self.mode.currentIndex() == 0:
            n = min(v, len(people)); groups = [[] for _ in range(n)]
            for i, name in enumerate(people): groups[i % n].append(name)
        else:
            groups = [people[i:i + v] for i in range(0, len(people), v)]
        self.output.setPlainText("\n\n".join(f"第 {i+1} 组（{len(g)}人）：\n" + "、".join(g) for i, g in enumerate(groups)))


class TimerTab(QWidget):
    def __init__(self):
        super().__init__(); self.total = 300; self.left = 300; self.end_at = 0.0
        self._timer = QTimer(self, interval=100); self._timer.timeout.connect(self._tick)
        root = QHBoxLayout(self); left = QVBoxLayout(); right = QVBoxLayout()
        self.display = QLabel("00:05:00"); self.display.setAlignment(Qt.AlignCenter)
        self.display.setStyleSheet("font-size:64px;font-weight:700")
        self.note = QLabel("设置时长后开始"); self.note.setAlignment(Qt.AlignCenter)
        left.addStretch(1); left.addWidget(self.display); left.addWidget(self.note)
        row = QHBoxLayout(); self.start = QPushButton("开始"); self.start.clicked.connect(self.toggle)
        reset = QPushButton("重置"); reset.clicked.connect(self.reset)
        self.full = QPushButton("全屏投影"); self.full.clicked.connect(self.toggle_fullscreen)
        row.addWidget(self.start); row.addWidget(reset); row.addWidget(self.full); left.addLayout(row)
        self.wheel = TimeWheel(value=self.total); self.wheel.valueChanged.connect(self.set_duration)
        right.addWidget(QLabel("滚轮选择时、分、秒")); right.addWidget(self.wheel, 0, Qt.AlignCenter)
        presets = QGridLayout()
        for i, sec in enumerate((60, 180, 300, 600, 1500, 2700)):
            b = QPushButton(f"{sec//60} 分钟"); b.clicked.connect(lambda _=False, sec=sec: self.set_duration(sec))
            presets.addWidget(b, i // 3, i % 3)
        right.addLayout(presets); right.addStretch(1)
        root.addLayout(left, 3); root.addLayout(right, 2); self._paint()

    def _fmt(self, sec):
        h, r = divmod(max(0, int(sec)), 3600); m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def set_duration(self, sec):
        if self._timer.isActive(): return
        self.total = self.left = max(1, min(86399, int(sec)))
        self.wheel.setValue(self.total, emit=False); self.note.setText("时长已设定，可开始")
        self._paint()

    def toggle(self):
        if self._timer.isActive():
            self._tick(); self._timer.stop(); self.wheel.setDisabled(False); self.note.setText("已暂停")
        else:
            if self.left <= 0: self.left = self.total
            self.end_at = time.monotonic() + self.left
            self._timer.start(); self.wheel.setDisabled(True); self.note.setText("倒计时进行中")
        self._paint()

    def _tick(self):
        if not self._timer.isActive() and self.end_at <= 0: return
        left = max(0, math.ceil(self.end_at - time.monotonic()))
        self.left = left; self._paint()
        if left == 0:
            self._timer.stop(); self.wheel.setDisabled(False); self.end_at = 0
            self.note.setText("时间到！"); self.display.setStyleSheet("font-size:64px;font-weight:700;color:#d32f2f")
            from PyQt5.QtWidgets import QApplication
            QApplication.beep()

    def reset(self):
        self._timer.stop(); self.left = self.total; self.end_at = 0
        self.wheel.setDisabled(False); self.note.setText("已重置"); self._paint()

    def _paint(self):
        self.display.setText(self._fmt(self.left))
        self.start.setText("暂停" if self._timer.isActive() else ("继续" if self.left != self.total and self.left > 0 else "开始"))
        self.display.setStyleSheet("font-size:64px;font-weight:700;color:%s" % ("#c47b00" if self._timer.isActive() and self.left <= 10 else "#18202b"))

    def toggle_fullscreen(self):
        window = self.window()
        if window.isFullScreen(): window.showNormal()
        else: window.showFullScreen()


class ScoreTab(QWidget):
    def __init__(self):
        super().__init__(); root = QVBoxLayout(self); bar = QHBoxLayout()
        add = QPushButton("添加小组"); add.clicked.connect(self.add_team)
        reset = QPushButton("全部清零"); reset.clicked.connect(self.reset_scores)
        bar.addWidget(add); bar.addWidget(reset); bar.addStretch(1); root.addLayout(bar)
        self.grid = QGridLayout(); root.addLayout(self.grid); root.addStretch(1)
        self.teams = [(f"第{i}组", 0) for i in range(1, 5)]; self.render()

    def add_team(self): self.teams.append((f"第{len(self.teams)+1}组", 0)); self.render()
    def reset_scores(self): self.teams = [(n, 0) for n, _ in self.teams]; self.render()
    def render(self):
        while self.grid.count():
            item = self.grid.takeAt(0); w = item.widget()
            if w: w.deleteLater()
        for i, (name, score) in enumerate(sorted(self.teams, key=lambda x: -x[1])):
            card = QFrame(); card.setFrameShape(QFrame.StyledPanel); layout = QVBoxLayout(card)
            title = QLineEdit(name); title.setAlignment(Qt.AlignCenter); title.textChanged.connect(lambda s, i=i: self.rename(i, s))
            number = QLabel(str(score)); number.setAlignment(Qt.AlignCenter); number.setStyleSheet("font-size:42px;font-weight:700")
            ops = QHBoxLayout()
            for delta in (-1, 1, 2, 5):
                btn = QPushButton(f"{delta:+d}"); btn.clicked.connect(lambda _=False, i=i, d=delta: self.bump(i, d)); ops.addWidget(btn)
            layout.addWidget(title); layout.addWidget(number); layout.addLayout(ops); self.grid.addWidget(card, i // 3, i % 3)

    def rename(self, i, name):
        # i is the currently sorted visual position; resolve safely after rebuilds.
        ordered = sorted(self.teams, key=lambda x: -x[1])
        if 0 <= i < len(ordered):
            old = ordered[i]; idx = self.teams.index(old); self.teams[idx] = (name[:24], old[1])

    def bump(self, i, delta):
        ordered = sorted(self.teams, key=lambda x: -x[1])
        if 0 <= i < len(ordered):
            old = ordered[i]; idx = self.teams.index(old); self.teams[idx] = (old[0], old[1] + delta); self.render()


class VoteTab(QWidget):
    KEYS = (("支持", "A"), ("反对", "B"), ("弃权", "C"), ("选项 D", "D"), ("未表态", "E"))
    def __init__(self):
        super().__init__(); root = QVBoxLayout(self); self.counts = [0] * len(self.KEYS); self.rows = []
        for i, (label, key) in enumerate(self.KEYS):
            row = QHBoxLayout(); btn = QPushButton(f"{label}  [{key}]"); btn.clicked.connect(lambda _=False, i=i: self.add(i))
            value = QLabel("0  (0%)"); value.setMinimumWidth(100)
            row.addWidget(btn, 1); row.addWidget(value); root.addLayout(row); self.rows.append(value)
        reset = QPushButton("全部清零"); reset.clicked.connect(self.reset); root.addWidget(reset); root.addStretch(1)

    def add(self, i): self.counts[i] += 1; self.render()
    def reset(self): self.counts = [0] * len(self.counts); self.render()
    def render(self):
        total = sum(self.counts)
        for value, count in zip(self.rows, self.counts): value.setText(f"{count}  ({round(count / total * 100) if total else 0}%)")


class GraphTab(QWidget):
    def __init__(self):
        super().__init__(); root = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal); left = QWidget(); side = QVBoxLayout(left); side.setContentsMargins(4, 4, 4, 4)
        header = QHBoxLayout(); header.addWidget(QLabel("表达式")); self.add = QPushButton("+ 添加"); self.add.clicked.connect(lambda: self.add_row("y=a*x")); header.addWidget(self.add); side.addLayout(header)
        self.expression_scroll = QScrollArea(); self.expression_scroll.setWidgetResizable(True)
        self.expression_host = QWidget(); self.expression_layout = QVBoxLayout(self.expression_host); self.expression_layout.setContentsMargins(2, 2, 2, 2)
        self.expression_scroll.setWidget(self.expression_host); side.addWidget(self.expression_scroll, 1)
        examples = QComboBox(); examples.addItems(["示例：请选择", "抛物线与交点", "圆 x²+y²=25", "极坐标 r=1+cos(theta)", "参数曲线 (cos(t),sin(2t))", "不等式 y<=x^2", "参数滑块 y=a*sin(x)"])
        examples.activated.connect(self._example); side.addWidget(examples)
        tools = QHBoxLayout()
        for label, fn in (("−", lambda: self.zoom(.82)), ("+", lambda: self.zoom(1.22)), ("重置视图", None)):
            b = QPushButton(label)
            if fn: b.clicked.connect(fn)
            else: b.clicked.connect(self.reset_view)
            tools.addWidget(b)
        side.addLayout(tools)
        self.sliders = QVBoxLayout(); side.addLayout(self.sliders)
        right = QWidget(); right_lay = QVBoxLayout(right); right_lay.setContentsMargins(0, 0, 0, 0)
        self.canvas = GraphCanvas(); self.canvas.statusChanged.connect(self._set_status); right_lay.addWidget(self.canvas, 1)
        self.status = QLabel("拖动平移 · 滚轮缩放 · 双击重置 · 根和曲线交点自动标注"); right_lay.addWidget(self.status)
        split.addWidget(left); split.addWidget(right); split.setSizes([350, 800]); root.addWidget(split)
        self.rows = []; self.add_row("y=sin(x)"); self.add_row("y=x^2-4"); self.refresh_sliders()

    def _example(self, index):
        values = {1: ["y=x^2-4", "y=2x"], 2: ["x^2+y^2=25"],
                  3: ["r=1+cos(theta)"], 4: ["(cos(t),sin(2t))"],
                  5: ["y<=x^2"], 6: ["y=a*sin(x)"]}
        if index in values:
            for row in self.rows: row[0].deleteLater()
            self.rows.clear()
            for expr in values[index]: self.add_row(expr)
            self.refresh_sliders()

    def add_row(self, value=""):
        row = QWidget(); lay = QHBoxLayout(row); lay.setContentsMargins(0, 0, 0, 0)
        color = QColor(GraphCanvas.COLORS[len(self.rows) % len(GraphCanvas.COLORS)])
        dot = QLabel("●"); dot.setStyleSheet(f"color:{color.name()};font-size:18px")
        visible = QCheckBox(); visible.setChecked(True)
        edit = QLineEdit(value); edit.setPlaceholderText("y=sin(x), x^2+y^2=25, r=..., (x(t),y(t))")
        remove = QPushButton("×"); remove.setFixedWidth(30)
        lay.addWidget(dot); lay.addWidget(visible); lay.addWidget(edit, 1); lay.addWidget(remove)
        self.expression_layout.addWidget(row); self.rows.append((row, edit, visible, dot))
        edit.textChanged.connect(self.refresh_graph); edit.textChanged.connect(self.refresh_sliders)
        visible.stateChanged.connect(self.refresh_graph)
        remove.clicked.connect(lambda: self.remove_row(row)); self.refresh_graph(); self.refresh_sliders()

    def remove_row(self, widget):
        self.rows = [r for r in self.rows if r[0] is not widget]
        widget.deleteLater(); self.refresh_graph(); self.refresh_sliders()

    def refresh_graph(self, *_):
        values = [(e.text(), v.isChecked()) for _, e, v, _ in self.rows]
        self.canvas.setExpressions(values)
        for _, edit, _, _ in self.rows:
            try:
                parse_plot(edit.text())
                message = "笛卡尔、隐式方程、不等式、极坐标、参数曲线与点；拖动平移、滚轮缩放"
            except Exception as exc:
                message = str(exc)
            edit.setToolTip(message)

    def refresh_sliders(self):
        while self.sliders.count():
            item = self.sliders.takeAt(0); w = item.widget()
            if w: w.deleteLater()
        text = " ".join(e.text() for _, e, _, _ in self.rows)
        candidates = [n for n in "abcdkmn" if re.search(rf"\b{n}\b", text)]
        for name in candidates:
            row = QWidget(); lay = QHBoxLayout(row); lay.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(f"{name} = 1.0"); slider = QSlider(Qt.Horizontal); slider.setRange(-100, 100); slider.setValue(10)
            slider.valueChanged.connect(lambda value, name=name, lab=lab: (lab.setText(f"{name} = {value/10:g}"), self.canvas.setParameter(name, value / 10)))
            lay.addWidget(lab); lay.addWidget(slider); self.sliders.addWidget(row)

    def zoom(self, factor): self.canvas.scale = min(10000, max(5, self.canvas.scale * factor)); self.canvas.update()
    def reset_view(self): self.canvas.resetView()
    def _set_status(self, text): self.status.setText(text)


class ClassroomWindow(QDialog):
    """Self-contained desktop classroom tools, independent of web routes/server."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FLA · 课堂工具（本地）")
        self.setMinimumSize(900, 620); self.resize(1180, 780)
        root = QVBoxLayout(self); title = QLabel("课堂工具"); title.setStyleSheet("font-size:22px;font-weight:700")
        root.addWidget(title); self.tabs = QTabWidget(); root.addWidget(self.tabs, 1)
        self.picker = PickerTab(); self.groups = GroupTab(self.picker); self.timer = TimerTab()
        self.score = ScoreTab(); self.vote = VoteTab(); self.graph = GraphTab()
        for name, widget in (("随机抽人", self.picker), ("随机分组", self.groups), ("计时器", self.timer),
                             ("计分板", self.score), ("表决器", self.vote), ("函数图像", self.graph)):
            self.tabs.addTab(widget, name)
        self.setStyleSheet("QPushButton{padding:7px 12px} QLineEdit,QComboBox,QSpinBox{padding:6px}")

    def select_tool(self, tab=""):
        index = {"pick": 0, "group": 1, "timer": 2, "score": 3, "vote": 4, "graph": 5}.get(str(tab).lower(), 0)
        self.tabs.setCurrentIndex(index)
        self.show(); self.raise_(); self.activateWindow()


_instance: ClassroomWindow | None = None


def open_classroom(tab="", parent=None):
    global _instance
    try:
        if _instance is None or not _instance.isVisible():
            _instance = ClassroomWindow(parent)
    except RuntimeError:
        _instance = ClassroomWindow(parent)
    _instance.select_tool(tab)
    return _instance
