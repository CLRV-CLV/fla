"""AI 联网搜索 (无需 API key)

默认使用 DuckDuckGo 的 HTML 结果页检索, 解析出 标题 / 链接 / 摘要。
搜索失败 (网络不通 / 页面结构变化) 时返回空列表, 不影响正常对话。
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; FLA-AI/1.0; +https://t.clrv.top)"
ENDPOINT = "https://html.duckduckgo.com/html/"


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _unwrap(href: str) -> str:
    """DuckDuckGo 的跳转链接 //duckduckgo.com/l/?uddg=... → 真实地址"""
    href = html.unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    q = urllib.parse.urlparse(href)
    if "duckduckgo.com" in q.netloc and q.path.startswith("/l/"):
        real = urllib.parse.parse_qs(q.query).get("uddg")
        if real:
            return real[0]
    return href


def search(query: str, n: int = 5, timeout: float = 8) -> list[dict]:
    q = (query or "").strip()[:200]
    if not q:
        return []
    url = ENDPOINT + "?" + urllib.parse.urlencode({"q": q, "kl": "cn-zh"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            page = r.read().decode("utf-8", "ignore")
    except Exception:
        return []
    out: list[dict] = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
        re.S)
    for m in pattern.finditer(page):
        href, title_html, rest = m.groups()
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        link = _unwrap(href)
        if not link.startswith(("http://", "https://")):
            continue
        out.append({"title": _clean(title_html)[:120], "url": link,
                    "snippet": _clean(sn.group(1) if sn else "")[:300]})
        if len(out) >= n:
            break
    return out


def as_context(results: list[dict]) -> str:
    """把搜索结果拼成给模型看的参考资料 (要求模型引用编号)"""
    if not results:
        return ""
    lines = ["以下是联网搜索得到的参考资料，回答时若用到请用 [编号] 标注来源；资料不足时请如实说明。"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}\n{r['url']}\n{r['snippet']}")
    return "\n\n".join(lines)
