# app/engine/animation.py
"""animejs 动画封装：给 WebView2 页面注入并执行常用动画。
用法：
    from app.engine.animation import Animation
    Animation.stagger_list_in(web_client, '#download-list .mail-row')
"""

class Animation:
    """animejs 动画快捷方式。所有方法内部先确保 anime.js 加载，再执行动画。"""

    # 使用 jsdelivr CDN，版本锁定 v4.5.0（稳定、体积小）
    # UMD 版本兼容浏览器直接引用
    _ANIME_CDN = "https://cdn.jsdelivr.net/npm/animejs@4.5.0/dist/bundles/anime.umd.min.js"
    # 本地兜底（打包时自动复制到 resources/web/，开发环境也可用）
    _ANIME_LOCAL = "anime.min.js"

    @staticmethod
    def _ensure_anime_injected() -> str:
        """返回确保 anime.js 已注入的 JS Promise（幂等：已存在则直接 resolve）。
        优先 CDN，失败则尝试本地文件。
        """
        return f"""
        (function() {{
          if (window.anime) return Promise.resolve();
          return new Promise((resolve, reject) => {{
            const tryLoad = (src) => {{
              const s = document.createElement('script');
              s.src = src;
              s.onload = resolve;
              s.onerror = () => reject(new Error('load failed: ' + src));
              document.head.appendChild(s);
            }};
            tryLoad('{Animation._ANIME_CDN}').catch(() => tryLoad('{Animation._ANIME_LOCAL}'));
          }});
        }})()
        """

    @classmethod
    def _wrap(cls, animation_js: str) -> str:
        """把动画代码包在 anime.js 加载后执行。"""
        return f"""
        {cls._ensure_anime_injected()}.then(() => {{
          {animation_js}
        }}).catch(err => console.error('Animation error:', err));
        """

    # ==================== 列表/入场/退场 ====================

    @classmethod
    def stagger_list_in(cls, selector: str = '#download-list .mail-row',
                        duration: int = 400,
                        delay: int = 60,
                        start_delay: int = 100,
                        translate_y: int = 30,
                        easing: str = 'outExpo') -> str:
        """
        列表项交错入场（从下往上淡入）。
        返回可直接传给 web_client.run_js(js) 的 JS 字符串。
        """
        anim = f"""
        anime({{
          targets: '{selector}',
          translateY: [{translate_y}, 0],
          opacity: [0, 1],
          duration: {duration},
          easing: '{easing}',
          delay: anime.stagger({delay}, {{ start: {start_delay} }})
        }});
        """
        return cls._wrap(anim)

    @classmethod
    def stagger_list_out(cls, selector: str = '#download-list .mail-row',
                         duration: int = 200, delay: int = 30,
                         on_complete: str = '') -> str:
        """列表项交错退场（向上淡出）。"""
        cb = f", complete: () => {{ {on_complete} }}" if on_complete else ''
        anim = f"""
        anime({{
          targets: '{selector}',
          translateY: [0, -20],
          opacity: [1, 0],
          duration: {duration},
          easing: 'inExpo',
          delay: anime.stagger({delay}, {{ from: 'last' }})
          {cb}
        }});
        """
        return cls._wrap(anim)

    @classmethod
    def fade_in(cls, selector: str, duration: int = 300, translate_y: int = -20,
                easing: str = 'outExpo') -> str:
        """单元素淡入 + 向上滑入（Toast、弹窗、空状态提示）。"""
        anim = f"""
        anime({{
          targets: '{selector}',
          translateY: [{translate_y}, 0],
          opacity: [0, 1],
          duration: {duration},
          easing: '{easing}'
        }});
        """
        return cls._wrap(anim)

    @classmethod
    def slide_up_remove(cls, selector: str, duration: int = 300,
                        on_complete: str = '') -> str:
        """向上滑出并从 DOM 移除（删除动画）。"""
        cb = f", complete: () => {{ {on_complete} }}" if on_complete else ''
        anim = f"""
        anime({{
          targets: '{selector}',
          translateY: [0, -30],
          opacity: [1, 0],
          duration: {duration},
          easing: 'inExpo'
          {cb}
        }});
        """
        return cls._wrap(anim)

    # ==================== 反馈/状态 ====================

    @classmethod
    def bounce(cls, selector: str, scale: float = 1.25, duration: int = 500) -> str:
        """点击/完成弹跳反馈。"""
        anim = f"""
        anime({{
          targets: '{selector}',
          scale: [{scale}, 1],
          duration: {duration},
          easing: 'outElastic(1, .5)'
        }});
        """
        return cls._wrap(anim)

    @classmethod
    def pulse(cls, selector: str, scale: float = 1.1, duration: int = 1000,
              loop: bool = True) -> str:
        """呼吸脉动（加载中、待处理状态、暂停态）。"""
        anim = f"""
        anime({{
          targets: '{selector}',
          scale: [{scale}, 1],
          duration: {duration},
          easing: 'easeInOutSine',
          direction: 'alternate',
          loop: {str(loop).lower()}
        }});
        """
        return cls._wrap(anim)

    @classmethod
    def draw_svg(cls, selector: str, duration: int = 1500,
                 easing: str = 'easeInOutQuad') -> str:
        """SVG 路径描边动画（stroke-dashoffset）。"""
        anim = f"""
        (function() {{
          const el = document.querySelector('{selector}');
          if (!el) return;
          const len = el.getTotalLength();
          el.style.strokeDasharray = len;
          el.style.strokeDashoffset = len;
          anime({{
            targets: el,
            strokeDashoffset: [len, 0],
            duration: {duration},
            easing: '{easing}'
          }});
        }})();
        """
        return cls._wrap(anim)

    # ==================== 进度条动画（新增） ====================

    @classmethod
    def progress_start(cls, selector: str = '#progress-fill',
                       duration: int = 800,
                       easing: str = 'outExpo') -> str:
        """
        进度条开始：从 0 平滑滑入到少量进度（如 5%），表示“已启动”。
        适合下载刚开始、不确定总进度时。
        """
        anim = f"""
        anime({{
          targets: '{selector}',
          width: ['0%', '5%'],
          duration: {duration},
          easing: '{easing}'
        }});
        """
        return cls._wrap(anim)

    @classmethod
    def progress_set(cls, selector: str = '#progress-fill',
                     percent: int = 50,
                     duration: int = 600,
                     easing: str = 'outCubic') -> str:
        """
        设置进度到指定百分比（0-100）。
        平滑过渡，自动钳制 0-100。
        """
        p = max(0, min(100, int(percent)))
        anim = f"""
        anime({{
          targets: '{selector}',
          width: ['{p}%'],
          duration: {duration},
          easing: '{easing}'
        }});
        """
        return cls._wrap(anim)

    @classmethod
    def progress_indeterminate(cls, selector: str = '#progress-fill',
                               duration: int = 1500,
                               color: str = '#2563eb') -> str:
        """
        不确定进度（来回滑动/呼吸灯效果），用于“正在连接/等待响应”等无法预估进度的场景。
        """
        anim = f"""
        (function() {{
          const el = document.querySelector('{selector}');
          if (!el) return;
          el.style.width = '30%';
          anime({{
            targets: el,
            translateX: ['-100%', '200%'],
            duration: {duration},
            easing: 'easeInOutSine',
            direction: 'alternate',
            loop: true
          }});
        }})();
        """
        return cls._wrap(anim)

    @classmethod
    def progress_complete(cls, selector: str = '#progress-fill',
                          duration: int = 500,
                          on_complete: str = '') -> str:
        """
        完成态：平滑滑到 100%，可选回调（如切换成功图标、显示完成文案）。
        """
        cb = f", complete: () => {{ {on_complete} }}" if on_complete else ''
        anim = f"""
        anime({{
          targets: '{selector}',
          width: ['100%'],
          duration: {duration},
          easing: 'outExpo'
          {cb}
        }});
        """
        return cls._wrap(anim)

    @classmethod
    def progress_error(cls, selector: str = '#progress-fill',
                       duration: int = 400,
                       shake_count: int = 3) -> str:
        """
        错误态：红色闪烁 + 抖动，表示下载失败/校验失败。
        """
        # 生成抖动关键帧：[-8, 8, -8, 8...] shake_count 次
        keyframes = []
        for i in range(shake_count * 2):
            keyframes.append(-8 if i % 2 == 0 else 8)
        keyframes_str = ', '.join(str(x) for x in keyframes)
        anim = f"""
        (function() {{
          const el = document.querySelector('{selector}');
          if (!el) return;
          const originalBg = el.style.background;
          el.style.background = '#ef4444';
          anime({{
            targets: el,
            translateX: [{keyframes_str}],
            duration: {duration // (shake_count * 2)},
            easing: 'linear',
            direction: 'alternate',
            loop: {shake_count},
            complete: () => {{
              el.style.background = originalBg || '#ef4444';
            }}
          }});
        }})();
        """
        return cls._wrap(anim)

    @classmethod
    def progress_pause(cls, selector: str = '#progress-fill',
                       pulse: bool = True) -> str:
        """
        暂停态：呼吸脉动，表示“暂停/等待用户操作”。
        """
        if pulse:
            return cls.pulse(selector, scale=1.02, duration=1200, loop=True)
        return cls._wrap(f"/* paused */")

    # ==================== 内部工具 ====================

    @classmethod
    def _wrap(cls, animation_js: str) -> str:
        """把动画代码包在 anime.js 加载后执行。"""
        return f"""
        {cls._ensure_anime_injected()}.then(() => {{
          {animation_js}
        }}).catch(err => console.error('Animation error:', err));
        """