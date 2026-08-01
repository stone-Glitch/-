#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序入口（含启动动画）
"""

import os
import sys
import time
import shutil
import tempfile
import tkinter as tk
from pathlib import Path
from logger import default_logger as logger


def _cleanup_stale_tempdirs(max_age_seconds: int = 3 * 24 * 3600) -> int:
    """
    启动时清理过期的 psi4_temp_* 临时目录，返回删除的数量。

    安全加固（修复 CWE-59 符号链接跟随 / CWE-367 TOCTOU / 审计 1.2 Windows junction）：
      • 仅在系统临时目录（tempfile.gettempdir / %TEMP% / TMPDIR / TMP）内匹配，
        不再触碰 Path.cwd()，避免误伤工作区或用户创建的同名目录。
      • 先用 resolve(strict=True) 拿真实路径，
        随后 relative_to 校验真实路径仍在系统临时目录下。
      • 拒绝所有 is_symlink 为 True 的路径（含 Windows junction），
        避免跟随到外部目录。
      • age 检查与 rmtree 都作用在同一个 real（已 resolve）的 Path 对象上，
        缩小两次文件系统检查之间的竞争窗口。
      • 【审计 1.2 新增】Windows 下显式检测 reparse point（junction），避免
        Path.is_symlink 漏检 NTFS reparse point。
    """
    def _is_windows_junction(path: Path) -> bool:
        if os.name != "nt":
            return False
        try:
            st = os.lstat(os.fspath(path))
        except OSError:
            return False
        import stat
        # IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003；junction 都是 FILE_ATTRIBUTE_REPARSE_POINT
        return bool(getattr(st, "st_file_attributes", 0) & 0x00000400)  # FILE_ATTRIBUTE_REPARSE_POINT
    removed = 0
    roots: set[Path] = set()
    for envvar in ('TMPDIR', 'TEMP', 'TMP'):
        v = os.environ.get(envvar)
        if v:
            try:
                roots.add(Path(v).resolve())
            except OSError:
                continue
    try:
        roots.add(Path(tempfile.gettempdir()).resolve())
    except OSError:
        pass
    # 去掉重复后做有效性过滤
    roots = {r for r in roots if r and r.is_dir()}
    if not roots:
        return 0
    now = time.time()
    seen: set[Path] = set()
    for root in roots:
        try:
            candidates = list(root.glob("psi4_temp_*"))
        except OSError:
            continue
        for d in candidates:
            try:
                # 【审计 1.2 junction 检测】先在未 resolve 的路径上用 lstat
                if _is_windows_junction(d):
                    continue
                real = d.resolve(strict=True)
                # resolve 之后如果本身是 symlink（极少，但防御），或原 path 是 symlink
                if d.is_symlink() or real.is_symlink():
                    continue
                # 【审计 1.2 junction 检测】resolve 后的路径也跑一遍
                if _is_windows_junction(real):
                    continue
                if real in seen:
                    continue
                seen.add(real)
                # (1) 解析后仍必须在该临时根目录内
                try:
                    real.relative_to(root)
                except ValueError:
                    continue
                # (3) 必须是目录
                if not real.is_dir():
                    continue
                # (4) 真实目录名仍保持 psi4_temp_ 前缀
                if not real.name.startswith("psi4_temp_"):
                    continue
                # (5) 年龄 & 删除（均作用于已 resolve 的 real）
                try:
                    st = real.stat(follow_symlinks=False)
                except OSError:
                    continue
                if now - st.st_mtime >= max_age_seconds:
                    try:
                        shutil.rmtree(real, ignore_errors=True)
                        removed += 1
                    except OSError:
                        pass
            except Exception:
                continue
    if removed:
        logger.info("清理过期临时目录 %d 个（> %.1f 天）", removed, max_age_seconds / 86400.0)
    return removed



class SplashScreen:
    """
    🫧 Aurora Frost Splash：
      • 深夜蓝渐变底（Canvas 手绘）
      • 中间一个发光分子轨道图（Canvas 画 3 条同心椭圆 + 旋转粒子）
      • 标题 Microsoft YaHei UI 加粗白字 + 副字紫蓝渐变
      • 底部进度 + 动态加载点
    """
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)

        W, H = 480, 260
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - W) // 2
        y = (sh - H) // 2
        self.root.geometry(f"{W}x{H}+{x}+{y}")

        self.canvas = tk.Canvas(self.root, width=W, height=H, bg="#0F1733",
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # —— 背景渐变：深夜 #0F1733 → 分子紫 #1B1F4B ——
        self._bg_c1 = (15, 23, 51)
        self._bg_c2 = (27, 31, 75)
        self._draw_bg()

        # —— 极光粒子光斑 ——
        import random
        self._rng = random.Random(42)
        for _ in range(6):
            cx = int(self._rng.uniform(0, W))
            cy = int(self._rng.uniform(0, H))
            r = int(self._rng.uniform(50, 140))
            col = self._rng.choice(["#3B6EFF", "#0EA288", "#8B5CF6"])
            for k in range(5, 0, -1):
                alpha = 0.05 * k
                self.canvas.create_oval(cx - r * k / 5, cy - r * k / 5,
                                        cx + r * k / 5, cy + r * k / 5,
                                        outline=col, width=0,
                                        fill=self._mix_with_bg(col, alpha))

        # —— 左侧分子轨道图（发光原子核 + 3 条椭圆轨道 + 3 个电子）——
        mx, my = 90, 130
        # 核（渐变圆：多层）
        for k in range(8, 0, -1):
            rad = 3 + k * 2
            alpha = 0.12 * k
            self.canvas.create_oval(mx - rad, my - rad, mx + rad, my + rad,
                                    outline="", fill=self._mix_with_bg("#0EA288", alpha))
        self.canvas.create_oval(mx - 5, my - 5, mx + 5, my + 5,
                                outline="#FFFFFF", fill="#0EA288", width=1)
        # 3 条轨道（装饰性椭圆线框，用 create_oval 兼容性最好；
        # 注意 create_oval 不支持 style/start/extent，所以直接画整椭圆）
        self._orbit_items = []
        for idx, (ry, rx, rot) in enumerate([(36, 62, 0), (48, 52, 25), (44, 64, -25)]):
            col = ["#3B6EFF", "#8B5CF6", "#0EA288"][idx]
            item = self.canvas.create_oval(mx - rx, my - ry, mx + rx, my + ry,
                                           outline=col, width=2)
            # 简单近似：不真的旋转椭圆，用 canvas 画点模拟电子沿轨道
            self._orbit_items.append((mx, my, rx, ry, rot, col, idx))

        # —— 标题 ——
        self.canvas.create_text(210, 95, anchor="w",
                                text="分子与计算文件管理器",
                                fill="#FFFFFF",
                                font=("Microsoft YaHei UI", 20, "bold"))
        # 副标题（彩色胶囊）
        sub = self.canvas.create_rectangle(210, 122, 456, 148,
                                           outline="#3B6EFF", fill="#1A224F", width=1)
        self.canvas.create_text(222, 135, anchor="w",
                                text="  🫧  Aurora Frost   ·   分子文件 · QM · 动画 · 工具箱",
                                fill="#B7CCFF",
                                font=("Microsoft YaHei UI", 10))

        # —— 底部状态文字 / 进度点 ——
        self.status_lbl = self.canvas.create_text(210, 195, anchor="w",
                                                   text="正在初始化…",
                                                   fill="#8B9DCF",
                                                   font=("Microsoft YaHei UI", 10))
        self.dots_lbl = self.canvas.create_text(210, 222, anchor="w",
                                                text="",
                                                fill="#3B6EFF",
                                                font=("Consolas", 14, "bold"))
        self.progress_bar_bg = self.canvas.create_rectangle(210, 232, 440, 240,
                                                            outline="#2A3067", fill="#1A224F")
        self.progress_bar = self.canvas.create_rectangle(210, 232, 210, 240,
                                                         outline="", fill="#0EA288")

        self.anim_running = True
        self._after_ids: list[str] = []
        self._t0 = 0
        self._animate()
        self.root.update()

    # ——— 工具：颜色混合（带 alpha 叠到背景色）———
    def _mix_with_bg(self, hex_col: str, alpha: float) -> str:
        h = hex_col.lstrip("#")
        r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
        br, bg_, bb = self._bg_c1
        rr = int(br + (r - br) * alpha)
        gg = int(bg_ + (g - bg_) * alpha)
        bb = int(bb + (b - bb) * alpha)
        return f"#{rr:02x}{gg:02x}{bb:02x}"

    def _draw_bg(self):
        W = self.canvas.winfo_reqwidth()
        H = self.canvas.winfo_reqheight()
        for y in range(0, H, 2):
            t = y / max(1, H - 1)
            t_e = t * t * (3 - 2 * t)
            r = int(self._bg_c1[0] + (self._bg_c2[0] - self._bg_c1[0]) * t_e)
            g = int(self._bg_c1[1] + (self._bg_c2[1] - self._bg_c1[1]) * t_e)
            b = int(self._bg_c1[2] + (self._bg_c2[2] - self._bg_c1[2]) * t_e)
            col = f"#{r:02x}{g:02x}{b:02x}"
            self.canvas.create_rectangle(0, y, W, y + 2, fill=col, outline=col)

    def _animate(self):
        if not self.anim_running:
            return
        self._t0 += 1
        # 进度点
        dots = "●" * ((self._t0 % 7)) + "○" * max(0, 6 - (self._t0 % 7))
        self.canvas.itemconfigure(self.dots_lbl, text=dots[:6])
        # 进度条：缓慢推进（最多到 ~90% 等待主窗口就绪）
        t = min(0.9, self._t0 / 80)
        self.canvas.coords(self.progress_bar, 210, 232, 210 + (440 - 210) * t, 240)
        # 状态轮换
        tips = ["正在初始化…", "加载 OpenBabel…", "准备 PSI4 接口…", "构建 UI…"]
        self.canvas.itemconfigure(self.status_lbl, text=tips[min(len(tips) - 1, self._t0 // 20)])
        self._after_ids.append(self.root.after(90, self._animate))

    def close(self):
        self.anim_running = False
        for a in self._after_ids:
            try: self.root.after_cancel(a)
            except Exception: pass
        self.root.destroy()


def main():
    logger.info("程序启动")
    # M-3 修复：清理过期临时目录：改为非守护线程（daemon=False）
    # daemon 线程会在解释器退出时被硬杀，正在 rmtree/stat 时被强杀可能留下 Fatal Python error，
    # 且清理操作本身耗时极短（几百毫秒级），等它结束是安全的。
    try:
        import threading as _th
        _th.Thread(target=_cleanup_stale_tempdirs, daemon=False, name="TmpCleanup").start()
    except Exception as e:
        logger.debug("启动临时目录清理线程失败（将跳过清理）: %s", e)
    splash = SplashScreen()

    def _close_splash_safely():
        """无论哪种失败路径都关 splash，避免一个看不见的 Tk 根一直挂着导致 showerror 无父窗口"""
        try:
            if getattr(splash, 'root', None) is not None:
                try:
                    splash.close()
                except Exception as _sc:
                    # splash 可能已经被用户或前面的异常链关掉，兜底 destroy
                    try:
                        splash.root.destroy()
                    except Exception as _sd:
                        logger.debug("关闭 splash 失败 (destroy): %s", _sd)
        except Exception as _sf:
            logger.debug("关闭 splash 失败 (outer): %s", _sf)

    def _destroy_any_tk_root(*exclude):
        """
        启动失败时清理 Tk 根：把除了 exclude 里（一般是临时用的 tmp_root）之外的
        所有活着的 Tk 解释器都 destroy 掉，避免「半初始化的 MainView」或 splash
        残留在内存里，导致后续 messagebox.showerror 拿一个不可见窗口做父窗口。
        """
        try:
            # Tkinter 内部用 _toplevel 字典维护所有活着的 Tk/Toplevel 实例
            all_tk = list(tk._default_root.tk.call("winfo", "children", ".")) if getattr(tk._default_root, 'tk', None) else []
        except Exception as _we:
            logger.debug("枚举 Tk 窗口失败: %s", _we)
            all_tk = []
        for w_path in all_tk:
            try:
                py_obj = None
                try:
                    # Tkinter 有个 NameToWidget 字典，路径 → Python 控件对象
                    py_obj = getattr(tk._default_root, '_nametowidget', lambda _: None)(w_path)
                except Exception as _nw:
                    logger.debug("NameToWidget 失败 path=%s: %s", w_path, _nw)
                    py_obj = None
                if py_obj is None:
                    continue
                if py_obj in exclude:
                    continue
                try:
                    py_obj.destroy()
                except Exception as _de:
                    logger.debug("destroy Tk 失败 %s: %s", w_path, _de)
            except Exception as _le:
                logger.debug("清理 Tk 窗口循环出错: %s", _le)
        # 再兜底：重置 Tk 默认根
        try:
            tk._default_root = None  # type: ignore[attr-defined]
        except Exception as _re:
            logger.debug("重置 _default_root 失败: %s", _re)

    def _show_fatal_error(title: str, body_lines: list[str], fallback_tb: str = ""):
        """
        优先用 Tk messagebox 弹错误；如果连 messagebox 都初始化不起来
        （例如 Tcl/Tk 本身坏了），退回到 stderr + 文件写日志，保证错误不丢。
        """
        body = "\n".join(str(x) for x in body_lines if x)
        # 先写日志（无论如何都不丢）
        try:
            logger.error("启动失败 %s | %s", title, body)
            if fallback_tb:
                logger.error("完整堆栈:\n%s", fallback_tb)
        except Exception:
            pass
        # 尝试 showerror。父窗口传 None，避免依赖不存在的 MainView；
        # Tkinter 会自动用当前最顶层的 Tk 做父（也就是 splash.root 已经关掉后新建的隐式 Tk）
        try:
            import tkinter.messagebox as _mb
            # （关键改动）先 destroy 掉任何残留的不可见 Tk：可能是半初始化的 MainView
            # 或者 splash，确保这次 messagebox 不会作为它们的「子对话框」被一起隐藏。
            tmp_root = None
            try:
                try:
                    alive_root = tk._default_root  # type: ignore[attr-defined]
                    if alive_root is None or not bool(getattr(alive_root, 'tk', None)):
                        raise RuntimeError("no alive tk")
                except Exception:
                    pass
                # 新建一个纯隐式根，它的唯一用途就是弹出错误对话框；用完就 destroy
                tmp_root = tk.Tk()
                tmp_root.withdraw()
                # 销毁除 tmp_root 之外的其他 Tk（如果有半初始化的 MainView / splash 残根）
                _destroy_any_tk_root(tmp_root)
                # 截断过长的堆栈：_mb 对话框不希望一次塞几万字
                safe_body = body
                if len(safe_body) > 4000:
                    safe_body = safe_body[:4000] + "\n…（堆栈过长，完整内容见日志文件）"
                _mb.showerror(title, safe_body, parent=tmp_root)
            finally:
                if tmp_root is not None:
                    try:
                        tmp_root.destroy()
                    except Exception:
                        pass
                # 最终再重置一次默认根，避免后续任何隐式 Tk 行为（比如导入模块时）依赖坏根
                try:
                    tk._default_root = None  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception as _mb_err:
            # 最后兜底：打印到控制台 + 写日志（如果还没写进去）
            try:
                print("=" * 60, file=sys.stderr)
                print(f"[{title}]", file=sys.stderr)
                print(body, file=sys.stderr)
                if fallback_tb:
                    print("---- traceback ----", file=sys.stderr)
                    print(fallback_tb, file=sys.stderr)
                print(f"(messagebox 不可用: {_mb_err})", file=sys.stderr)
                print("=" * 60, file=sys.stderr)
            except Exception:
                pass

    def load_main():
        import traceback as _tb
        captured_tb: str = ""
        app = None
        try:
            from view import MainView
            # 关 splash 再实例化主窗口（主窗口实例化失败时下面的 except 会再次安全关一次）
            _close_splash_safely()
            try:
                app = MainView()
            except Exception:
                # MainView.__init__ 内部已经 destroy 自己的 Tk 根，这里再兜底一次，
                # 防止 MainView 在 super().__init__() 之后但在自己的 destroy 之前，
                # 因某些资源抛错而留残根。
                try:
                    if app is not None and bool(getattr(app, 'tk', None)):
                        app.destroy()
                except Exception:
                    pass
                raise
            # mainloop 内部抛异常（例如 Tcl 错误）也走同一错误通道
            try:
                app.mainloop()
            except Exception:
                # mainloop 抛异常时也把 app .destroy 掉，防止主窗口半关不关的残根
                try:
                    if app is not None and bool(getattr(app, 'tk', None)):
                        app.destroy()
                except Exception:
                    pass
                raise
        except Exception as e:
            captured_tb = _tb.format_exc()
            _close_splash_safely()
            lines = [
                f"初始化主窗口时出错：{e}",
                "",
                "---- 技术堆栈（复制给开发者）----",
                captured_tb,
            ]
            _show_fatal_error("启动失败", lines, fallback_tb=captured_tb)
            # 非 0 退出码，方便 .bat / 打包脚本知道失败
            try:
                sys.exit(1)
            except Exception:
                pass

    splash.root.after(500, load_main)
    try:
        splash.root.mainloop()
    except Exception as _ml_err:
        # splash 自己的 mainloop 也可能因 Tcl/Tk 底层问题报错，别静默吞掉
        import traceback as _tb2
        captured2 = _tb2.format_exc()
        _close_splash_safely()
        _show_fatal_error(
            "启动画面运行失败",
            [f"错误详情：{_ml_err}", "", "堆栈：", captured2],
            fallback_tb=captured2,
        )


if __name__ == "__main__":
    main()