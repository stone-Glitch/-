#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
首次使用向导 wizard.py
- 在 MainView.__init__ 延迟 (after(300ms)) 调用；当 config_data["first_run"] == True 时显示。
- 三步：① 选工作目录 → ② 是否加载示例映射表 → ③ 选默认计算预设（写回 config）。
- 完成后把 first_run=False 写入配置，下次启动不再弹。
- **不阻塞主界面**：所有变量写回 MainView.*_var，最后调用 controller 完成真正的加载/扫描。
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.view import MainView


# ---------- 可复用默认值 ----------
def _default_work_dir() -> str:
    """用户文档下建一个 MolManager 子目录，存在就直接返回，不存在自动 mkdir。"""
    home = Path(os.path.expanduser("~"))
    docs = home / "Documents" if (home / "Documents").exists() else home
    d = docs / "MolManager"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        return str(home)
    return str(d)


def _sample_mapping_template(target_dir: Path) -> Path:
    """写一份示例 mapping.txt 到目标目录，方便用户直接打开编辑。"""
    sample = target_dir / "mapping_template.txt"
    if sample.exists():
        return sample
    try:
        sample.write_text(
            "# 一行一条：英文名 中文名 或 英文名,中文名 或 编号,英文名,中文名\n"
            "# 以 # 开头为注释\n"
            "\n"
            "benzene                    苯\n"
            "toluene                    甲苯\n"
            "ethanol                    乙醇\n"
            "benzoic_acid               苯甲酸\n"
            "aspirin                    阿司匹林\n"
            "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    return sample


# ===========================================================
# 向导主类：基于 Toplevel，不占用主窗口，不改变主窗口焦点策略
# ===========================================================
class FirstRunWizard:
    """
    三步首次使用向导。
    usage:
        w = FirstRunWizard(app)   # app = MainView 实例
        w.show()                  # 非阻塞
    """

    def __init__(self, app: "MainView"):
        self.app = app
        self.config_data: dict = dict(app.config_data)
        self.top: tk.Toplevel | None = None
        self._step = 0  # 0,1,2 共 3 步
        # 每步要保存的值
        self.work_dir_value: str = _default_work_dir()
        self.load_sample_mapping: tk.BooleanVar = tk.BooleanVar(value=True)
        self.sample_mapping_path: Path | None = None
        try:
            from utils.constants import RUN_PRESETS
            preset_names = list(RUN_PRESETS.keys())
        except Exception:
            RUN_PRESETS = {}
            preset_names = []
        self.preset_value: tk.StringVar = tk.StringVar(
            value=preset_names[0] if preset_names else "快速（力场）"
        )
        self.preset_names = preset_names
        self.RUN_PRESETS = RUN_PRESETS

    # ---------------- 公共入口 ----------------
    def show(self):
        if self.top is not None and self.top.winfo_exists():
            self.top.lift()
            self.top.focus_force()
            return
        self.top = tk.Toplevel(self.app)
        self.top.title("🌱  欢迎使用分子与计算文件管理器 — 首次设置向导")
        self.top.configure(bg="#161B22")
        self.top.geometry("640x440")
        self.top.minsize(600, 420)
        self.top.transient(self.app)  # 始终前置于主窗口
        self.top.grab_set()            # 模态，防止新手同时乱点主窗口
        # 居中
        self.top.update_idletasks()
        try:
            sw, sh = self.app.winfo_screenwidth(), self.app.winfo_screenheight()
            w, h = 640, 440
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.top.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

        self._build_header()
        self._body = tk.Frame(self.top, bg="#161B22", bd=1, relief=tk.SOLID)
        self._body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 12))
        self._nav = tk.Frame(self.top, bg="#161B22")
        self._nav.pack(fill=tk.X, padx=18, pady=(0, 16))
        self._prev_btn = ttk.Button(self._nav, text="◀ 上一步", command=self._go_prev, state=tk.DISABLED)
        self._prev_btn.pack(side=tk.LEFT)
        ttk.Frame(self._nav, width=2).pack(side=tk.LEFT)
        self._next_btn = ttk.Button(self._nav, text="下一步 ▶", command=self._go_next, style="Aurora.Primary.TButton")
        self._next_btn.pack(side=tk.RIGHT)
        self._cancel_btn = ttk.Button(self._nav, text="跳过向导", command=self._close)
        self._cancel_btn.pack(side=tk.RIGHT, padx=(0, 10))

        self._render_step()

    # ---------------- 顶部步骤条 ----------------
    def _build_header(self):
        header = tk.Frame(self.top, bg="#161B22")
        header.pack(fill=tk.X, padx=18, pady=(14, 12))
        titles = ["1/3 工作目录", "2/3 映射模板", "3/3 默认计算预设"]
        for i, t in enumerate(titles):
            active = (i == self._step)
            bg = "#3B6EFF" if active else "#FFFFFF"
            fg = "#FFFFFF" if active else "#2C3E50"
            pill = tk.Label(header, text=t, bg=bg, fg=fg,
                            font=('Microsoft YaHei UI', 10, "bold"), padx=14, pady=6,
                            bd=1, relief=tk.SOLID,
                            highlightthickness=1, highlightbackground="#C8D0DC")
            pill.pack(side=tk.LEFT, padx=4)

    # ---------------- 根据当前 step 渲染 body ----------------
    def _render_step(self):
        # 清 body
        for w in self._body.winfo_children():
            w.destroy()
        # 重绘步骤头（高亮当前步）
        self._build_header()
        # 导航按钮 state
        self._prev_btn.configure(state=(tk.NORMAL if self._step > 0 else tk.DISABLED))
        if self._step == 2:
            self._next_btn.configure(text="✅ 完成并开始使用")
        else:
            self._next_btn.configure(text="下一步 ▶")

        if self._step == 0:
            self._render_step1()
        elif self._step == 1:
            self._render_step2()
        else:
            self._render_step3()

    # ---------------- Step1：工作目录 ----------------
    def _render_step1(self):
        tk.Label(self._body, text="📂  选择你的工作目录",
                 bg="#161B22", fg="#E6EDF3",
                 font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor="w", padx=24, pady=(20, 6))
        tk.Label(self._body,
                 text="   工作目录用来存放所有 .mol / .xyz / .fchk / .out 等计算文件。\n"
                      "   我们推荐使用独立文件夹，后续可随时在顶部工具栏切换。",
                 bg="#161B22", fg="#9DA7B3",
                 font=('Microsoft YaHei UI', 10), justify="left").pack(anchor="w", padx=24)

        row = tk.Frame(self._body, bg="#161B22")
        row.pack(fill=tk.X, padx=24, pady=(20, 12))
        tk.Label(row, text="目录路径:", bg="#161B22", fg="#E6EDF3",
                 font=('Microsoft YaHei UI', 11, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        self._wd_var = tk.StringVar(value=self.work_dir_value)
        entry = ttk.Entry(row, textvariable=self._wd_var, font=('Microsoft YaHei UI', 11))
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        def _browse():
            d = filedialog.askdirectory(parent=self.top, title="选择工作目录",
                                         initialdir=self._wd_var.get() or str(Path.home()))
            if d:
                self._wd_var.set(d)
        ttk.Button(row, text="浏览…", command=_browse).pack(side=tk.LEFT)

        tip = tk.Label(self._body,
                       text="💡 推荐：在「文档」下新建一个空文件夹（程序已自动尝试创建 Documents/MolManager）。",
                       bg="#161B22", fg="#58A6FF",
                       font=('Microsoft YaHei UI', 9), justify="left")
        tip.pack(anchor="w", padx=24, pady=(0, 0))

    # ---------------- Step2：映射模板 ----------------
    def _render_step2(self):
        tk.Label(self._body, text="🗂️  中英文/编号映射表",
                 bg="#161B22", fg="#E6EDF3",
                 font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor="w", padx=24, pady=(20, 6))
        tk.Label(self._body,
                 text="   映射表能把「文件名 → 中文名」自动关联，列表里一眼看出每个分子是什么。\n"
                      "   初学者建议直接生成示例模板，在它基础上添加你自己的条目即可。",
                 bg="#161B22", fg="#9DA7B3",
                 font=('Microsoft YaHei UI', 10), justify="left").pack(anchor="w", padx=24)

        cb = tk.Checkbutton(self._body, text="✅ 在工作目录里生成 mapping_template.txt 并默认加载它",
                            variable=self.load_sample_mapping, bg="#161B22",
                            fg="#E6EDF3", font=('Microsoft YaHei UI', 11, "bold"),
                            activebackground="#1C2330", activeforeground="#2DD4BF",
                            selectcolor="#1C2330", bd=0, pady=6)
        cb.pack(anchor="w", padx=24, pady=(24, 4))

        tk.Label(self._body,
                 text="\n".join([
                     "示例格式（以空格/逗号分隔皆可）：",
                     "   benzene             苯",
                     "   toluene             甲苯",
                     "   benzoic_acid, 苯甲酸",
                 ]),
                 bg="#1C2330", fg="#E6EDF3",
                 font=('Consolas', 10), justify="left",
                 bd=1, relief=tk.SOLID, padx=16, pady=10
                 ).pack(anchor="w", padx=24, pady=(8, 10))

    # ---------------- Step3：默认计算预设 ----------------
    def _render_step3(self):
        tk.Label(self._body, text="🎯  选择默认计算预设",
                 bg="#161B22", fg="#E6EDF3",
                 font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor="w", padx=24, pady=(20, 6))
        tk.Label(self._body,
                 text="   以后每次打开「计算与动画」页，都会默认选中这个预设，一键即可运行。\n"
                      "   高级用户仍可在 PSI4 完整面板里自由调整所有参数。",
                 bg="#161B22", fg="#9DA7B3",
                 font=('Microsoft YaHei UI', 10), justify="left").pack(anchor="w", padx=24)

        row = tk.Frame(self._body, bg="#161B22")
        row.pack(fill=tk.X, padx=24, pady=(20, 8))
        tk.Label(row, text="预设:", bg="#161B22", fg="#E6EDF3",
                 font=('Microsoft YaHei UI', 11, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        cb = ttk.Combobox(row, textvariable=self.preset_value,
                          values=self.preset_names, state="readonly", width=44,
                          font=('Microsoft YaHei UI', 11))
        cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 显示所选预设的参数摘要
        self._preset_summary = tk.Label(self._body, text="", bg="#161B22", fg="#58A6FF",
                                        font=('Microsoft YaHei UI', 10), justify="left", anchor="w")
        self._preset_summary.pack(fill=tk.X, padx=24, pady=(6, 0))

        def _update_summary(_e=None):
            info = self.RUN_PRESETS.get(self.preset_value.get(), {}) if hasattr(self, "RUN_PRESETS") else {}
            lines = []
            if info:
                lines.append("当前预设参数：")
                for k in ("task_type", "method", "basis", "solvent", "memory_gb"):
                    if k in info and info[k] is not None:
                        lines.append(f"  · {k} = {info[k]}")
            else:
                lines.append("（未发现 RUN_PRESETS 配置，后续可用默认值。）")
            self._preset_summary.configure(text="\n".join(lines))
        cb.bind("<<ComboboxSelected>>", _update_summary)
        _update_summary()

        tk.Label(self._body,
                 text="\n完成后：工作目录会立刻切换 + 扫描文件 + 加载示例映射 + 默认预设写回配置。",
                 bg="#161B22", fg="#3FB950",
                 font=('Microsoft YaHei UI', 9, "bold"), justify="left"
                 ).pack(anchor="w", padx=24, pady=(14, 10))

    # ---------------- 导航动作 ----------------
    def _validate_step0(self) -> bool:
        d = self._wd_var.get().strip()
        if not d:
            messagebox.showwarning("请选择工作目录", "工作目录不能为空。", parent=self.top)
            return False
        p = Path(d)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("创建目录失败", f"无法创建/访问目录 {p}\n{e}", parent=self.top)
            return False
        self.work_dir_value = str(p)
        return True

    def _validate_step1(self) -> bool:
        # 生成示例模板到当前选择的工作目录（即使没勾选也无所谓，仅在勾选时创建）
        if self.load_sample_mapping.get():
            try:
                target = Path(self.work_dir_value)
                self.sample_mapping_path = _sample_mapping_template(target)
            except Exception as e:
                messagebox.showwarning("映射模板创建失败", f"{e}\n（可稍后手动在文件管理页点「生成缺失CSV」）",
                                       parent=self.top)
        return True

    def _validate_step2(self) -> bool:
        return True  # 预设总有一个值，无需校验

    def _go_next(self):
        ok = True
        if self._step == 0:
            ok = self._validate_step0()
        elif self._step == 1:
            ok = self._validate_step1()
        elif self._step == 2:
            ok = self._validate_step2()
            if ok:
                self._apply_and_close()
                return
        if ok:
            self._step += 1
            self._render_step()

    def _go_prev(self):
        if self._step <= 0:
            return
        self._step -= 1
        self._render_step()

    # ---------------- 完成：把结果写回 MainView ----------------
    def _apply_and_close(self):
        app = self.app
        try:
            # 1) 工作目录
            app.work_dir_var.set(self.work_dir_value)
            # 同步到 model.work_dir
            try:
                from pathlib import Path as _P
                new_wd = _P(self.work_dir_value)
                app.controller.model.work_dir = new_wd
                app.controller.model.exts = set(app.controller.model.exts)  # no-op
            except Exception:
                pass

            # 2) 映射模板加载
            if self.load_sample_mapping.get() and self.sample_mapping_path and self.sample_mapping_path.exists():
                try:
                    app.mapping_file_var.set(str(self.sample_mapping_path))
                    # 延迟调用 controller.load_mapping_file（避免模态对话框中阻塞）
                    app.after(80, lambda: _safe_call(app.controller.load_mapping_file))
                except Exception:
                    pass

            # 3) 默认计算预设写回 config
            try:
                self.config_data["first_run"] = False
                self.config_data.setdefault("psi4_config", {})
                self.config_data["default_run_preset"] = self.preset_value.get()
                # 也同步到主窗口，方便 dialogs.py 打开时直接读取
                try:
                    from utils.constants import RUN_PRESETS
                    preset_info = RUN_PRESETS.get(self.preset_value.get(), {})
                    for ck, ak in (("last_method", "method"),
                                   ("last_basis", "basis"),
                                   ("last_task", "task_type")):
                        if preset_info.get(ak) is not None:
                            self.config_data["psi4_config"][ck] = preset_info[ak]
                except Exception:
                    pass
                # 真正写回 app.config_data 并保存
                app.config_data.update(self.config_data)
                try:
                    from utils.config import save_config
                    save_config(app.config_data)
                except Exception:
                    pass
            except Exception:
                try:
                    # first_run 至少要写 False 避免下次还弹
                    app.config_data["first_run"] = False
                except Exception:
                    pass

            # 4) 触发扫描
            app.after(160, lambda: _safe_call(app.controller.scan_files))

            # 5) 状态栏提示
            try:
                app.action_tip_var.set("✅ 首次配置完成！下一步：在列表中勾选要计算的文件 → 切到「计算与动画」页。")
            except Exception:
                pass
        finally:
            self._close()

    def _close(self):
        # 无论如何都把 first_run 置 False 标记为看过了，避免用户每次启动都被迫弹向导
        try:
            if "first_run" not in self.app.config_data or self.app.config_data.get("first_run") is not False:
                self.app.config_data["first_run"] = False
                try:
                    from utils.config import save_config
                    save_config(self.app.config_data)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self.top is not None and self.top.winfo_exists():
                self.top.grab_release()
                self.top.destroy()
        finally:
            self.top = None


def _safe_call(fn):
    try:
        return fn()
    except Exception:
        pass


# ---------------- 便捷入口：MainView 只需要调用这一个函数 ----------------
def maybe_show_first_run_wizard(app: "MainView") -> None:
    """
    当 config_data.get("first_run") != False 时，300ms 后弹出向导。
    （给主窗口留够先绘制的时间，避免 splash 刚关就被另一个模态抢焦点）
    """
    try:
        if app.config_data.get("first_run", True) in (False, 0, "false", "False", "no", "No"):
            return
    except Exception:
        # 读不到配置也不弹，免得异常
        return

    def _do():
        try:
            w = FirstRunWizard(app)
            w.show()
        except Exception:
            # 向导失败不要影响主程序
            try:
                app.config_data["first_run"] = False
                from utils.config import save_config
                save_config(app.config_data)
            except Exception:
                pass

    try:
        app.after(300, _do)
    except Exception:
        _do()
