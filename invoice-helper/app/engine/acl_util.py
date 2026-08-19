"""文件 ACL 修复工具。

以管理员身份运行的程序创建的 PDF，ACL 默认只有 SYSTEM/Administrators 可读，
普通权限的资源管理器 / PDF 阅读器无法打开（表现为"载入错误"或"文件不可读"）。
本模块在下载写盘后为当前用户补上访问权限，静默失败，不影响主流程。
"""

import os
import subprocess


def grant_current_user_access(path, rights="(M)"):
    """为当前用户授予文件/目录访问权限（默认 Modify：读、写、删除、重命名）。

    :param path: 文件或目录路径
    :param rights: icacls 权限字符串，如 (F)/(M)/(R)
    """
    if not path or not os.path.exists(path):
        return
    try:
        user = os.environ.get("USERNAME", "")
        if not user:
            return
        dom = os.environ.get("USERDOMAIN", "")
        who = f"{dom}\\{user}" if dom else user
        subprocess.run(
            ["icacls", path, "/grant", f"{who}:{rights}"],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass