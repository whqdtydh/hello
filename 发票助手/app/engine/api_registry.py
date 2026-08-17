"""接口注册表：管理 QQ 邮箱网页 CGI 接口的路径，支持失效自动学习。

背景：
    接口地址是通过抓包逆向得到的（/list/maillist、/read/readmail、
    /fapiao/download）。腾讯改版时可能调整路径，导致硬编码失效。
    本模块把接口路径集中为注册表（JSON 持久化），并实现特征匹配：
    用户手动操作网页时，注入的 JS hook 记录网络请求，匹配器根据
    路径关键词 + 参数结构自动识别"同类接口"，更新注册表后重试。

设计：
    - 默认路径 = 逆向得到的初始值（仍可正常使用）
    - 学习流程：接口失效 -> 标记 failed -> 用户手动操作网页 ->
      JS 记录请求 -> 特征匹配 -> 更新路径 -> 清除 failed
    - 纯逻辑模块，不依赖 Qt，可独立单测
"""

import json
import os
import time
import urllib.parse

# 默认接口路径（逆向基准值，2026-08-15 验证）
DEFAULT_ENDPOINTS = {
    "maillist": {
        "path": "/list/maillist",
        "method": "GET",
        # 匹配特征：路径关键词（+2 分）与必需参数名（+1 分）
        "path_kw": ["list", "maillist"],
        "params": ["dirid", "page_now", "page_size"],
    },
    "readmail": {
        "path": "/read/readmail",
        "method": "POST",
        "path_kw": ["read", "readmail"],
        "params": ["mailid"],
    },
    "fapiao": {
        "path": "/fapiao/download",
        "method": "GET",
        "path_kw": ["fapiao", "download"],
        "params": ["fapiao_list"],
    },
}

# 匹配通过的最低得分（路径关键词命中 1 个即视为同类）
_MATCH_THRESHOLD = 2


class ApiRegistry:
    """接口路径注册表（JSON 持久化 + 特征匹配）。"""

    def __init__(self, path=None):
        self.file = path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..", "config", "api_registry.json")
        self.data = self._load()

    # ---------- 持久化 ----------
    def _load(self):
        default = {
            "version": 1,
            "updated_at": "",
            "endpoints": {
                name: dict(ep, **{"status": "ok"})
                for name, ep in DEFAULT_ENDPOINTS.items()
            },
        }
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "endpoints" not in data:
                return default
            # 合并默认值：新版本新增的接口自动补上
            for name, ep in DEFAULT_ENDPOINTS.items():
                data["endpoints"].setdefault(name, dict(ep))
            return data
        except Exception:
            return default

    def save(self):
        self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            with open(self.file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    # ---------- 查询/更新 ----------
    def get_path(self, name):
        """返回接口路径（可能已被学习覆盖）。"""
        ep = self.data["endpoints"].get(name) or {}
        return ep.get("path") or DEFAULT_ENDPOINTS.get(name, {}).get("path", "")

    def get_method(self, name):
        ep = self.data["endpoints"].get(name) or {}
        return ep.get("method") or DEFAULT_ENDPOINTS.get(name, {}).get("method", "GET")

    def set_path(self, name, path):
        if name in self.data["endpoints"] and path:
            self.data["endpoints"][name]["path"] = path
            self.data["endpoints"][name]["status"] = "ok"
            return self.save()
        return False

    # ---------- 失效标记 ----------
    def mark_failed(self, name):
        if name in self.data["endpoints"]:
            self.data["endpoints"][name]["status"] = "failed"
            self.save()

    def clear_failed(self):
        changed = False
        for ep in self.data["endpoints"].values():
            if ep.get("status") == "failed":
                ep["status"] = "ok"
                changed = True
        if changed:
            self.save()

    def failed_endpoints(self):
        """返回 status=failed 的接口名列表。"""
        return [n for n, ep in self.data["endpoints"].items()
                if ep.get("status") == "failed"]

    # ---------- 特征匹配 ----------
    def match_endpoint(self, url, method="GET"):
        """从观察到的请求 URL 匹配接口，返回 (endpoint_name, new_path) 或 None。

        打分规则：路径段命中 path_kw 得 2 分；query 参数命中 params 得 1 分。
        返回得分最高且 >= _MATCH_THRESHOLD 的匹配。
        """
        try:
            parsed = urllib.parse.urlparse(url)
            path = parsed.path or ""
            qs = urllib.parse.parse_qs(parsed.query)
            path_segs = set(s.lower() for s in path.split("/") if s)
            q_params = set(qs.keys())
        except Exception:
            return None

        best, best_score = None, 0
        for name, ep in DEFAULT_ENDPOINTS.items():
            score = 0
            for kw in ep.get("path_kw", []):
                if kw in path_segs or kw in path:
                    score += 2
            for p in ep.get("params", []):
                if p in q_params:
                    score += 1
            if score > best_score:
                best, best_score = name, score
        if best and best_score >= _MATCH_THRESHOLD:
            return best, path
        return None

    def learn_from_observations(self, observations):
        """从观察到的请求列表学习新接口路径。

        observations: [{u: url, m: method}, ...]
        返回更新的接口名列表；无变化返回 []。
        """
        updated = []
        for obs in observations or []:
            url = (obs or {}).get("u", "")
            method = (obs or {}).get("m", "GET") or "GET"
            if not url:
                continue
            hit = self.match_endpoint(url, method)
            if not hit:
                continue
            name, new_path = hit
            if new_path and new_path != DEFAULT_ENDPOINTS.get(name, {}).get("path"):
                if self.set_path(name, new_path):
                    updated.append(name)
        return updated
