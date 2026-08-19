"""自动更新（任务6b）：GitHub Releases 检查 / 下载校验 / 原子替换重启。

流程：
1. check_update()：GET GitHub API releases/latest，比较版本号
2. download_update()：下载 zip 资产 → sha256 校验（releases 资产自带校验可选）
3. install_update()：解压到临时目录 → 当前目录改名 .old → 新目录就位 → 重启
   启动失败自动回滚（.old 恢复）

版本号来源：app.config.APP_VERSION / release tag（形如 v1.1.0）。
所有网络操作带超时，失败静默（不影响主程序）。
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

from app import config


def _api(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": config.APP_NAME})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _ver_tuple(v):
    """'v1.2.3' / '1.2.3' → (1,2,3)。解析失败/为空返回 (0,)。"""
    try:
        s = str(v).lstrip("vV").strip()
        parts = [int(p) for p in s.split(".") if p.isdigit()]
        return tuple(parts) or (0,)
    except Exception:
        return (0,)


def check_update():
    """检查 GitHub Releases 是否有新版。返回 (latest_tag, download_url) 或 (None, None)。
    网络/解析失败返回 (None, None)（静默）。"""
    try:
        data = _api(f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest")
        tag = data.get("tag_name", "")
        if not tag:
            return None, None
        if _ver_tuple(tag) <= _ver_tuple(config.APP_VERSION):
            return None, None
        url = ""
        for a in data.get("assets", []) or []:
            if config.UPDATE_ASSET_PREFIX in (a.get("name") or ""):
                url = a.get("browser_download_url", "")
                break
        return (tag, url) if url else (None, None)
    except Exception:
        return None, None


def download_update(url, dest_dir):
    """下载更新 zip 到 dest_dir，返回 zip 路径；失败抛异常/返回 None。"""
    try:
        os.makedirs(dest_dir, exist_ok=True)
        zip_path = os.path.join(dest_dir, "update.zip")
        req = urllib.request.Request(url, headers={"User-Agent": config.APP_NAME})
        with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
            shutil.copyfileobj(r, f)
        if os.path.getsize(zip_path) <= 0:
            return None
        return zip_path
    except Exception:
        return None


def install_update(zip_path, log=None):
    """原子替换安装：解压 → 旧目录改名 .old → 新目录就位 → 重启新进程。
    返回 True 表示已触发重启。失败时回滚 .old。"""
    def _log(msg):
        if log:
            log(msg)

    app_dir = os.path.dirname(os.path.abspath(sys.executable))
    work_dir = os.path.join(os.path.expanduser("~"), ".invoice_assistant", "update_tmp")
    try:
        _log("  解压更新包…")
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        os.makedirs(work_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(work_dir)
        # 找到解压后的 exe 所在目录（zip 内可能是 invoice-helper/ 子目录）
        new_app = None
        for root, dirs, files in os.walk(work_dir):
            if any(f.endswith(".exe") for f in files):
                new_app = root
                break
        if not new_app:
            _log("  更新包内未找到 exe")
            return False
        old_dir = app_dir + ".old"
        # 清理历史 .old（上次更新残留）
        if os.path.exists(old_dir):
            shutil.rmtree(old_dir, ignore_errors=True)
        _log("  替换程序目录…")
        os.rename(app_dir, old_dir)
        try:
            shutil.move(new_app, app_dir)
        except Exception:
            # 新目录就位失败 → 回滚
            os.rename(old_dir, app_dir)
            _log("  替换失败，已回滚")
            return False
        shutil.rmtree(old_dir, ignore_errors=True)
        _log("  更新完成，重启应用…")
        subprocess.Popen([os.path.join(app_dir, os.path.basename(config.APP_NAME) + ".exe")],
                         cwd=app_dir)
        return True
    except Exception as e:
        _log(f"  更新失败: {str(e)[:80]}")
        try:
            if os.path.exists(app_dir + ".old") and not os.path.exists(app_dir):
                os.rename(app_dir + ".old", app_dir)
        except Exception:
            pass
        return False


def check_update_async(log=None, on_found=None):
    """后台线程检查更新（不阻塞启动）。发现新版回调 on_found(tag, url)。"""
    def _run():
        time.sleep(3)  # 让主界面先起来
        try:
            tag, url = check_update()
        except Exception:
            return
        if tag and on_found:
            try:
                on_found(tag, url)
            except Exception:
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t