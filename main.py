#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序入口（含启动动画）

重构说明：
  - 移除内部重复的 _is_windows_junction，改用 path_utils.is_windows_junction
  - 保持所有外部接口不变
"""
import os
import sys
import time
import math
import random
import shutil
import tempfile
import tkinter as tk
from pathlib import Path

from utils.logger import default_logger as logger
from utils.path_utils import is_windows_junction


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
                if is_windows_junction(d):
                    continue
                real = d.resolve(strict=True)
                # resolve 之后如果本身是 symlink（极少，但防御），或原 path 是 symlink
                if d.is_symlink() or real.is_symlink():
                    continue
                # 【审计 1.2 junction 检测】resolve 后的路径也跑一遍
                if is_windows_junction(real):
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
    🫧 Aurora Frost Splash（v2 动效增强版）：
      • 深夜蓝渐变底 + 流动的极光波带
      • 左侧发光分子轨道：原子核脉冲 + 3 个电子沿椭圆轨道真实公转
      • 标题带柔光描边 + 副标题渐变胶囊
      • 底部进度条带流光高光 + 动态加载点 + 轮换状态提示
    对外接口保持与旧版一致：self.root / self.close()。
    """

    # —— 配色（与 App 主题 Aurora Frost 一致）——
    BG_C1 = (15, 23, 51)      # #0F1733
    BG_C2 = (27, 31, 75)      # #1B1F4B
    ACCENT = "#0EA288"        # teal
    BLUE = "#3B6EFF"
    PURPLE = "#8B5CF6"
    INK = "#FFFFFF"
    SUB = "#B7CCFF"
    MUTE = "#8B9DCF"

    W, H = 540, 300

    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        W, H = self.W, self.H
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw - W)//2}+{(sh - H)//2}")
        self.canvas = tk.Canvas(self.root, width=W, height=H, bg="#0F1733",
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self._rng = random.Random(42)
        self._draw_bg()
        self._init_aurora()
        self._init_particles()
        self._init_orbit()
        self._init_ui()

        self.anim_running = True
        self._after_ids = []
        self._frame = 0
        self._t0 = 0
        self._animate()
        self.root.update()

    # ——— 颜色工具 ———
    @staticmethod
    def _hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def _mix_with_bg(self, hex_col, alpha):
        r, g, b = self._hex_to_rgb(hex_col)
        br, bg_, bb = self.BG_C1
        return f"#{int(br+(r-br)*alpha):02x}{int(bg_+(g-bg_)*alpha):02x}{int(bb+(b-bb)*alpha):02x}"

    def _draw_bg(self):
        W, H = self.W, self.H
        for y in range(0, H, 2):
            t = y / max(1, H - 1)
            t_e = t * t * (3 - 2 * t)
            r = int(self.BG_C1[0] + (self.BG_C2[0] - self.BG_C1[0]) * t_e)
            g = int(self.BG_C1[1] + (self.BG_C2[1] - self.BG_C1[1]) * t_e)
            b = int(self.BG_C1[2] + (self.BG_C2[2] - self.BG_C1[2]) * t_e)
            col = f"#{r:02x}{g:02x}{b:02x}"
            self.canvas.create_rectangle(0, y, W, y + 2, fill=col, outline=col)
        # 顶部一抹冷光晕，增强“极光”氛围
        for k in range(6, 0, -1):
            self.canvas.create_rectangle(0, 0, W, k * 14,
                                         fill=self._mix_with_bg(self.BLUE, 0.04 * k), outline="")

    def _init_aurora(self):
        W, H = self.W, self.H
        self._aurora = []
        # (base_y, amp, wave_len, speed, thickness, color)
        specs = [
            (H * 0.30, 16, 380, 0.020, 24, self.BLUE),
            (H * 0.58, 22, 300, -0.015, 30, self.PURPLE),
            (H * 0.82, 14, 440, 0.012, 20, self.ACCENT),
        ]
        dx = 18
        xs = list(range(0, W + dx, dx))
        for base_y, amp, wl, speed, th, color in specs:
            # 占位多边形，_step_aurora 每帧刷新真实波形
            item = self.canvas.create_polygon([0, 0, W, 0],
                                              outline="",
                                              fill=self._mix_with_bg(color, 0.16),
                                              smooth=True)
            self._aurora.append((item, xs, base_y, amp, wl, speed, th, color))

    def _init_orbit(self):
        W, H = self.W, self.H
        mx, my = int(W * 0.18), int(H * 0.5)
        self._omx, self._omy = mx, my
        # 核外大 halo（呼吸式辉光，置于最底层）
        self._nucleus_glow = self.canvas.create_oval(mx - 20, my - 20, mx + 20, my + 20,
                                                     outline="", fill=self._mix_with_bg(self.ACCENT, 0.10))
        # 核脉冲光晕（多层，随帧缩放）
        self._nucleus = []
        for k in range(6, 0, -1):
            rad = 4 + k * 2
            item = self.canvas.create_oval(mx - rad, my - rad, mx + rad, my + rad,
                                           outline="", fill=self._mix_with_bg(self.ACCENT, 0.10 * k))
            self._nucleus.append(item)
        self._nucleus_core = self.canvas.create_oval(mx - 5, my - 5, mx + 5, my + 5,
                                                     outline="#FFFFFF", fill=self.ACCENT, width=1)
        # 三条轨道（装饰椭圆）+ 3 个真实公转的电子（glow + core）
        self._electrons = []
        orbit_specs = [
            (38, 70, 0, self.BLUE, 0.16),
            (52, 60, 28, self.PURPLE, -0.12),
            (44, 75, -28, self.ACCENT, 0.20),
        ]
        rot = math.pi / 180
        for ry, rx, rotd, color, spd in orbit_specs:
            self.canvas.create_oval(mx - rx, my - ry, mx + rx, my + ry,
                                    outline=self._mix_with_bg(color, 0.55), width=1.5)
            glow = self.canvas.create_oval(mx - rx - 9, my - 9, mx - rx + 9, my + 9,
                                           outline="", fill=self._mix_with_bg(color, 0.35))
            core = self.canvas.create_oval(mx - rx - 4, my - 4, mx - rx + 4, my + 4,
                                           outline="#FFFFFF", fill=color, width=1)
            # 末尾 0.0 为电子相位（公转角度），每帧累加
            self._electrons.append([glow, core, rx, ry, rotd * rot, color, spd, 0.0])

    def _init_ui(self):
        W = self.W
        x0 = int(W * 0.37)
        # 标题：多方向淡蓝柔光描边 + 实心白字
        gy = 92
        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
            self.canvas.create_text(x0 + dx, gy + dy, anchor="w",
                                    text="分子与计算文件管理器",
                                    fill=self._mix_with_bg(self.BLUE, 0.5),
                                    font=("Microsoft YaHei UI", 20, "bold"))
        self.canvas.create_text(x0, gy, anchor="w",
                                text="分子与计算文件管理器",
                                fill=self.INK, font=("Microsoft YaHei UI", 20, "bold"))
        # 副标题胶囊
        self.canvas.create_rectangle(x0, 120, W - 20, 148,
                                     outline=self._mix_with_bg(self.BLUE, 0.6),
                                     fill="#1A224F", width=1)
        self.canvas.create_text(x0 + 12, 134, anchor="w",
                                text="  🫧  Aurora Frost   ·   分子文件 · QM · 动画 · 工具箱",
                                fill=self.SUB, font=("Microsoft YaHei UI", 10))
        # 状态 / 加载点 / 进度条
        self.status_lbl = self.canvas.create_text(x0, 198, anchor="w",
                                                  text="正在初始化…", fill=self.MUTE,
                                                  font=("Microsoft YaHei UI", 10))
        self.dots_lbl = self.canvas.create_text(x0, 228, anchor="w", text="",
                                                fill=self.BLUE, font=("Consolas", 14, "bold"))
        self._pbar_x0, self._pbar_x1 = x0, W - 40
        self._pbar_y0, self._pbar_y1 = 260, 272
        self.canvas.create_rectangle(self._pbar_x0, self._pbar_y0,
                                     self._pbar_x1, self._pbar_y1,
                                     outline=self._mix_with_bg(self.BLUE, 0.4), fill="#1A224F")
        self.progress_bar = self.canvas.create_rectangle(
            self._pbar_x0, self._pbar_y0, self._pbar_x0, self._pbar_y1,
            outline="", fill=self._mix_with_bg(self.ACCENT, 0.95))
        # 进度条流光高光（在已填充区间内循环移动）
        self._shimmer = self.canvas.create_rectangle(
            self._pbar_x0, self._pbar_y0, self._pbar_x0 + 24, self._pbar_y1,
            outline="", fill="#BFF7EC")
        self.canvas.itemconfigure(self._shimmer, state="hidden")
        # 进度百分比数字
        self.pct_lbl = self.canvas.create_text(self._pbar_x1 + 10, (self._pbar_y0 + self._pbar_y1) // 2,
                                              anchor="w", text="0%", fill=self.ACCENT,
                                              font=("Microsoft YaHei UI", 10, "bold"))

    def _init_particles(self):
        W, H = self.W, self.H
        self._particles = []
        rng = random.Random(7)
        for _ in range(14):
            r = rng.uniform(1.0, 2.6)
            x = rng.uniform(0, W)
            y = rng.uniform(0, H)
            vx = rng.uniform(-0.25, 0.25)
            vy = rng.uniform(-0.18, 0.18)
            col = rng.choice([self.BLUE, self.PURPLE, self.ACCENT])
            item = self.canvas.create_oval(x - r, y - r, x + r, y + r, outline="",
                                          fill=self._mix_with_bg(col, 0.5))
            self._particles.append([item, x, y, vx, vy, r, col])

    def _step_particles(self):
        W, H = self.W, self.H
        for p in self._particles:
            item, x, y, vx, vy, r, col = p
            x += vx
            y += vy
            if x < -4:
                x = W + 4
            elif x > W + 4:
                x = -4
            if y < -4:
                y = H + 4
            elif y > H + 4:
                y = -4
            p[1], p[2] = x, y
            a = 0.35 + 0.30 * (0.5 + 0.5 * math.sin(self._frame * 0.08 + x))
            self.canvas.coords(item, x - r, y - r, x + r, y + r)
            self.canvas.itemconfigure(item, fill=self._mix_with_bg(col, a))

    def _step_aurora(self):
        for item, xs, base_y, amp, wl, speed, th, color in self._aurora:
            ph = self._frame * speed
            pts = []
            for x in xs:
                y = base_y + amp * math.sin(2 * math.pi * x / wl + ph)
                pts.append((x, y))
            for x in reversed(xs):
                y = base_y + amp * math.sin(2 * math.pi * x / wl + ph) + th
                pts.append((x, y))
            self.canvas.coords(item, *[c for p in pts for c in p])

    def _step_orbit(self):
        mx, my = self._omx, self._omy
        # 核脉冲
        pulse = 1.0 + 0.18 * math.sin(self._frame * 0.12)
        # 核外大 halo 呼吸
        gp = 0.5 + 0.5 * math.sin(self._frame * 0.12)
        gr = 16 + 8 * gp
        self.canvas.coords(self._nucleus_glow, mx - gr, my - gr, mx + gr, my + gr)
        self.canvas.itemconfigure(self._nucleus_glow, fill=self._mix_with_bg(self.ACCENT, 0.06 + 0.05 * gp))
        for k, item in enumerate(self._nucleus):
            rad = (4 + (6 - k) * 2) * pulse
            self.canvas.coords(item, mx - rad, my - rad, mx + rad, my + rad)
        # 电子沿椭圆真实公转（含轨道倾角旋转）
        for e in self._electrons:
            glow, core, rx, ry, rot, color, spd, phase = e
            phase = phase + spd
            e[7] = phase
            xe = rx * math.cos(phase)
            ye = ry * math.sin(phase)
            x = mx + xe * math.cos(rot) - ye * math.sin(rot)
            y = my + xe * math.sin(rot) + ye * math.cos(rot)
            self.canvas.coords(glow, x - 9, y - 9, x + 9, y + 9)
            self.canvas.coords(core, x - 4, y - 4, x + 4, y + 4)

    def _animate(self):
        if not self.anim_running or not self.root.winfo_exists():
            return
        self._frame += 1
        self._t0 += 1
        self._step_aurora()
        self._step_orbit()
        # 加载点
        n = self._t0 % 7
        dots = "●" * n + "○" * (6 - n)
        self.canvas.itemconfigure(self.dots_lbl, text=dots[:6])
        # 进度条：缓慢推进（最多到 ~90% 等待主窗口就绪）
        t = min(0.9, self._t0 / 80)
        x1 = self._pbar_x0 + (self._pbar_x1 - self._pbar_x0) * t
        self.canvas.coords(self.progress_bar, self._pbar_x0, self._pbar_y0, x1, self._pbar_y1)
        self.canvas.itemconfigure(self.pct_lbl, text=f"{int(t * 100)}%")
        self._step_particles()
        # 流光高光在已填充区间内循环移动
        if x1 - self._pbar_x0 > 28:
            self.canvas.itemconfigure(self._shimmer, state="normal")
            span = int(x1 - self._pbar_x0 - 28)
            sx = self._pbar_x0 + ((self._frame * 6) % max(1, span))
            self.canvas.coords(self._shimmer, sx, self._pbar_y0, sx + 24, self._pbar_y1)
        # 状态轮换
        tips = ["正在初始化…", "加载 OpenBabel…", "准备 PSI4 接口…", "构建 UI…"]
        self.canvas.itemconfigure(self.status_lbl, text=tips[min(len(tips) - 1, self._t0 // 20)])
        self._after_ids.append(self.root.after(90, self._animate))

    def close(self):
        self.anim_running = False
        for a in self._after_ids:
            try:
                self.root.after_cancel(a)
            except Exception:
                pass
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
            from core.view import MainView
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
