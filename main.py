#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序入口（含启动动画）
"""

import os
import time
import shutil
import tempfile
import tkinter as tk
from pathlib import Path
from logger import default_logger as logger


def _cleanup_stale_tempdirs(max_age_seconds: int = 3 * 24 * 3600) -> int:
    """
    启动时清理过期的 psi4_temp_* 临时目录，返回删除的数量。

    安全加固（修复 CWE-59 符号链接跟随 / CWE-367 TOCTOU）：
      • 仅在系统临时目录（tempfile.gettempdir / %TEMP% / TMPDIR / TMP）内匹配，
        不再触碰 Path.cwd()，避免误伤工作区或用户创建的同名目录。
      • 先用 resolve(strict=True) 拿真实路径，
        随后 relative_to 校验真实路径仍在系统临时目录下。
      • 拒绝所有 is_symlink 为 True 的路径（含 Windows junction），
        避免跟随到外部目录。
      • age 检查与 rmtree 都作用在同一个 real（已 resolve）的 Path 对象上，
        缩小两次文件系统检查之间的竞争窗口。
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
                real = d.resolve(strict=True)
            except OSError:
                continue
            try:
                if real in seen:
                    continue
                seen.add(real)
                # (1) 解析后仍必须在该临时根目录内
                try:
                    real.relative_to(root)
                except ValueError:
                    continue
                # (2) 拒绝 symlink / junction
                if d.is_symlink() or real.is_symlink():
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
        # 3 条轨道
        self._orbit_items = []
        for idx, (ry, rx, rot) in enumerate([(36, 62, 0), (48, 52, 25), (44, 64, -25)]):
            col = ["#3B6EFF", "#8B5CF6", "#0EA288"][idx]
            item = self.canvas.create_oval(mx - rx, my - ry, mx + rx, my + ry,
                                           outline=col, width=2, style=tk.ARC,
                                           start=0, extent=359)
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
    try:
        _cleanup_stale_tempdirs()
    except Exception as e:
        logger.debug("清理临时目录时发生非致命错误: %s", e)
    splash = SplashScreen()

    def load_main():
        from view import MainView
        splash.close()
        app = MainView()
        app.mainloop()

    splash.root.after(500, load_main)
    splash.root.mainloop()


if __name__ == "__main__":
    main()