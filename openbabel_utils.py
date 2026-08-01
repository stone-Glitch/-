#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Open Babel 工具模块 - 封装常用分子操作
支持格式转换、SMILES生成、力场优化、描述符计算、分子叠加等

所有函数返回统一格式：{'success': bool, 'message': str, 'data': any}
其中 'data' 包含具体结果（如描述符字典、文件路径等）。
"""

import logging
import os
import sys
import re
import csv
import subprocess
import tempfile
import shutil
import threading
import warnings
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any, Union

from logger import default_logger as logger, performance_timer
from constants import (
    DEFAULT_FORCEFIELD,
    OB_DEFAULT_TIMEOUT_SEC,
    OB_LARGE_TIMEOUT_SEC,
    OB_CONVERT_TIMEOUT_SEC,
    OB_PNG_TIMEOUT_SEC,
    COMMON_INPUT_FORMATS,
    ATOMIC_WEIGHTS,
)

# ======================== 导入与版本兼容 ========================
try:
    # 新版 OpenBabel (>=3.0) 推荐使用 openbabel 模块
    import openbabel as ob
    import openbabel.pybel as pybel
    PYBEL_AVAILABLE = True
except ImportError:
    try:
        # 旧版使用 pybel 顶层模块
        import pybel
        PYBEL_AVAILABLE = True
    except ImportError:
        PYBEL_AVAILABLE = False

# ======================== 缓存（性能优化 + 线程安全） ========================
_DESC_CACHE_MAX = 128
_DESC_CACHE: dict[tuple[str, int, int], Dict[str, Any]] = {}
_DESC_CACHE_LOCK = threading.Lock()

_MOL_READ_CACHE_MAX = 256
_MOL_READ_CACHE: dict[tuple[str, int, int, str], list] = {}
_MOL_READ_CACHE_LOCK = threading.Lock()

_OBABEL_CLI_LOCK = threading.Lock()  # 保护 _OBABEL_CLI_EXE 单例初始化

# ======================== 问题三：用户可手动指定 obabel 可执行文件路径 ========================
# 默认空串 = 自动解析（PATH + shutil.which）。
# 用户通过「环境设置 / OpenBabel 路径设置」对话框写入 config["obabel_path"]，
# 这里在 _resolve_obabel_cli 的最开头优先尝试。
_MANUAL_OBABEL_PATH: str | None = None

# OpenBabel 安装建议 / 故障诊断建议（纯文本，不含外部 URL 点击，避免安全弹窗）
OB_INSTALL_GUIDE: str = (
    "━━━━━━━━━━━━  OpenBabel 安装/故障排查指引  ━━━━━━━━━━━━\n"
    "【推荐方式】\n"
    "  1) conda 安装（最稳，CLI + Python 接口都会配好）：\n"
    "       conda install -c conda-forge openbabel\n"
    "  2) Windows 官网安装包：https://github.com/openbabel/openbabel/releases\n"
    "     安装后把 C:\\Program Files\\OpenBabel-3.1.1 加入系统 PATH，再重启程序。\n"
    "  3) pip 安装（成功率较低，只推荐纯 Python 场景）：\n"
    "       pip install openbabel-wheel   # 或 pip install openbabel\n"
    "\n"
    "【本程序提供的修复入口】\n"
    "  • 菜单「帮助 → 环境诊断」可一键检查依赖状态\n"
    "  • 状态栏右下角的「OB 指示灯」（绿/红色圆点）点击可查看详情或手动指定路径\n"
    "  • 直接点击「手动选择 obabel 路径」，浏览选中 obabel.exe（Linux/mac 为 obabel）即可\n"
    "\n"
    "【常见失败原因】\n"
    "  • obabel 没加入系统 PATH，导致自动查找失败\n"
    "  • 旧版 Windows 安装包只配了 GUI，没勾选「Add to PATH」\n"
    "  • conda 环境没激活，导致程序只能看到基础 Python\n"
    "  • 杀毒软件把 obabel.exe 当成可疑程序隔离（恢复白名单即可）\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
)


def clear_caches() -> tuple[int, int]:
    """
    公开接口：安全地清空所有 OpenBabel 缓存（描述符 + 分子读取）。
    返回: (evicted_desc_count, evicted_mol_read_count)
    保证：即使清空过程中抛错，两个字典最终都处于「已清空」的一致状态。
    """
    d = 0
    m = 0
    with _DESC_CACHE_LOCK:
        d = len(_DESC_CACHE)
        _DESC_CACHE.clear()
    with _MOL_READ_CACHE_LOCK:
        m = len(_MOL_READ_CACHE)
        _MOL_READ_CACHE.clear()
    return d, m


def _cache_key(path_str: str) -> tuple[str, int, int] | None:
    try:
        st = os.stat(path_str)
        return (os.fspath(Path(path_str).resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


def cache_stats() -> dict[str, int]:
    """公开接口：返回当前缓存状态（只读，内部加锁，对多线程安全）。"""
    with _DESC_CACHE_LOCK:
        dc = len(_DESC_CACHE)
    with _MOL_READ_CACHE_LOCK:
        mc = len(_MOL_READ_CACHE)
    return {"descriptors": dc, "mol_read": mc, "desc_max": _DESC_CACHE_MAX, "mol_read_max": _MOL_READ_CACHE_MAX}


# ======================== 问题三：手动 obabel 路径 ========================
def set_manual_obabel_path(path: str | None) -> None:
    """设置用户手动指定的 obabel 可执行文件路径。传入 None 或 "" 会清除手动设置并回退到自动查找。"""
    global _MANUAL_OBABEL_PATH, _OBABEL_CLI_EXE
    v = (path or "").strip() if path else ""
    if not v:
        _MANUAL_OBABEL_PATH = None
    else:
        _MANUAL_OBABEL_PATH = v
    # 手动路径改了，必须让下一次调用重新解析（清掉已缓存的 _OBABEL_CLI_EXE）
    with _OBABEL_CLI_LOCK:
        _OBABEL_CLI_EXE = None


def get_manual_obabel_path() -> str | None:
    return _MANUAL_OBABEL_PATH


def _load_manual_from_config() -> str | None:
    """在第一次解析 CLI 时，从配置懒加载用户手动路径（避免 openbabel_utils → config 循环 import）。"""
    try:
        from config import load_config
        cfg = load_config()
        v = str(cfg.get("obabel_path", "") or "").strip()
        return v or None
    except Exception as _e:
        logger.debug("从配置加载 obabel_path 失败，忽略: %s", _e)
        return None


# ======================== 子进程包装（安全、跨平台） ========================
_OBABEL_CLI_EXE: str | None = None


def _resolve_obabel_cli() -> str:
    """
    安全解析 obabel 命令行可执行文件的绝对路径，
    避免相对名 + PATH 搜索导致的本地可执行文件劫持（B607/CWE-426）。

    ===== 放宽策略（问题三修复） =====
      - 顺序：① 手动路径（用户显式指定）② PATH 中的 obabel ③ 常见安装目录兜底
      - 只拒绝 tempdir / 当前工作目录 下的真实可执行（因为它们是典型的劫持目录）
      - 不再一概拒绝用户家目录：conda 安装到 ~/anaconda3/bin、~/miniconda3/bin、
        ~/AppData/Local/Continuum/anacondaX 等都是用户常用合法路径；另外「手动路径」
        因为是用户明确点选，视为显式信任，不再做目录黑白名单。
      - 解析结果缓存，加锁保护单例初始化。
    """
    import shutil as _shutil
    import tempfile as _tempfile
    global _OBABEL_CLI_EXE, _MANUAL_OBABEL_PATH

    def _candidate_locations() -> list[str]:
        """兜底：常见安装位置（只在 shutil.which 找不到时试，避免误劫持）。"""
        home = Path.home()
        out: list[str] = []
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\OpenBabel-3.1.1\obabel.exe",
                r"C:\Program Files\OpenBabel-3.0.0\obabel.exe",
                r"C:\Program Files (x86)\OpenBabel-3.1.1\obabel.exe",
                str(home / "anaconda3" / "Library" / "bin" / "obabel.exe"),
                str(home / "miniconda3" / "Library" / "bin" / "obabel.exe"),
                str(home / "Miniconda3" / "Library" / "bin" / "obabel.exe"),
                str(home / "Anaconda3" / "Library" / "bin" / "obabel.exe"),
                str(home / "AppData" / "Local" / "Programs" / "OpenBabel" / "obabel.exe"),
            ]
            out.extend(candidates)
        else:
            out.extend([
                "/usr/bin/obabel",
                "/usr/local/bin/obabel",
                "/opt/homebrew/bin/obabel",              # macOS Apple Silicon
                "/usr/local/homebrew/bin/obabel",        # macOS Intel
                str(home / "anaconda3" / "bin" / "obabel"),
                str(home / "miniconda3" / "bin" / "obabel"),
                str(home / "miniforge3" / "bin" / "obabel"),
                str(home / "mambaforge" / "bin" / "obabel"),
                str(home / "bin" / "obabel"),
            ])
        return out

    def _safe_real(p: Path, *, user_explicit: bool) -> Path:
        """解析真实路径 + 安全检查。
        user_explicit=True 时：只做「存在性 + 是文件」检查，不再限制目录（视为用户显式信任）。
        user_explicit=False 时：拒绝 tempdir 和 cwd（家目录放行）。
        """
        try:
            real = p.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f"obabel 路径不存在或不可读: {p}") from exc
        if not real.is_file():
            raise RuntimeError(f"obabel 路径不是文件: {real}")
        if user_explicit:
            # 用户显式选择的路径：信任他的选择，但仍然校验可执行（后面 _run_obabel 调用时会得到真实报错）
            logger.info("使用用户指定的 OpenBabel CLI: %s", real)
            return real
        # 自动路径的安全检查：只拒绝 临时目录 和 当前工作目录
        unsafe_roots: list[Path] = []
        for _cand in (
            _tempfile.gettempdir(),
            os.getcwd(),
        ):
            try:
                unsafe_roots.append(Path(_cand).resolve(strict=False))
            except Exception as _e:
                logger.debug("obabel 安全路径检查：跳过不可解析目录 %r: %s", _cand, _e)
        for root in unsafe_roots:
            try:
                real.relative_to(root)
                raise RuntimeError(
                    f"出于安全考虑，拒绝执行在可写目录下的 obabel 真实路径: {real}（父目录={root}），"
                    "请把它移动到系统路径，或通过「菜单→帮助→环境诊断」手动选择该路径以显式信任。"
                )
            except ValueError:
                # ValueError = 不在 root 下，是期望的安全结果
                logger.debug("obabel 路径安全检查通过：%s 不在 %s 下", real, root)
        return real

    # 双检锁（DCL）避免频繁抢锁
    if _OBABEL_CLI_EXE is not None:
        return _OBABEL_CLI_EXE
    with _OBABEL_CLI_LOCK:
        if _OBABEL_CLI_EXE is not None:
            return _OBABEL_CLI_EXE

        # Step 1：懒加载用户手动配置路径（第一次调用才读 config）
        if _MANUAL_OBABEL_PATH is None:
            cfg_v = _load_manual_from_config()
            if cfg_v:
                _MANUAL_OBABEL_PATH = cfg_v

        # Step 2：手动路径优先
        if _MANUAL_OBABEL_PATH:
            real_path = _safe_real(Path(_MANUAL_OBABEL_PATH), user_explicit=True)
            _OBABEL_CLI_EXE = str(real_path)
            return _OBABEL_CLI_EXE

        # Step 3：shutil.which（PATH 解析）
        resolved = _shutil.which("obabel")
        real_path: Path | None = None
        if resolved:
            try:
                real_path = _safe_real(Path(resolved), user_explicit=False)
            except RuntimeError as _e:
                logger.warning("自动解析的 obabel 路径不安全/不可用：%s，继续尝试常见目录", _e)
                real_path = None

        # Step 4：常见安装目录兜底
        if real_path is None:
            last_err: Exception | None = None
            for cand in _candidate_locations():
                if not os.path.isfile(cand):
                    continue
                try:
                    real_path = _safe_real(Path(cand), user_explicit=False)
                    break
                except RuntimeError as _e:
                    last_err = _e
                    logger.debug("obabel 兜底候选不可用 %s: %s", cand, _e)
            if real_path is None and last_err is not None:
                # 把兜底里最严重的错误也带出去，方便诊断
                raise RuntimeError(
                    "未找到可用的 obabel（OpenBabel 命令行），请安装或手动指定路径。\n"
                    f"最近一次失败原因：{last_err}"
                )

        if real_path is None:
            raise RuntimeError(
                "未在 PATH 和常见安装目录中找到 obabel（OpenBabel 命令行），请安装后重试。\n"
                "也可以通过「菜单 → 帮助 → 环境诊断 → 手动选择 obabel 路径」指定。"
            )
        _OBABEL_CLI_EXE = str(real_path)
        return _OBABEL_CLI_EXE


def _run_obabel(args: List[str], timeout: Optional[int] = OB_DEFAULT_TIMEOUT_SEC,
                check: bool = False) -> subprocess.CompletedProcess:
    """
    安全执行 obabel 命令，自动处理 Windows 控制台窗口隐藏。
    args[0] 应为 "obabel"（相对名占位），此函数会替换为已解析的绝对路径。

    H-2 修复：timeout 默认从 constants 取 OB_DEFAULT_TIMEOUT_SEC（60s），不再是 None；
    避免异常 obabel 死锁卡住整个后台任务。大分子场景可显式传 OB_LARGE_TIMEOUT_SEC 或更大值。
    若需要无限制（极少场景），显式传 timeout=None。

    【审计 2.1 修复：命令参数防选项注入】
    所有调用方给出的「位置参数」（输入文件、输出文件、SMILES 串等）：
      a) 若该参数是「文件路径」，则一律转换为绝对路径；
      b) 若该参数可能是 "-" 开头的文件名，在前面补 "./"（Unix）或保留绝对路径。
    同时，本函数对每个 token 做最基本的 NUL/CR/LF 剔除，避免 subprocess 层报错或潜在的
    选项拼接风险。
    """
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs = {
            'startupinfo': startupinfo,
            'creationflags': subprocess.CREATE_NO_WINDOW
        }
    else:
        kwargs = {}

    if not args:
        raise ValueError("_run_obabel 调用缺少命令参数")
    exe = _resolve_obabel_cli()
    real_args: list[str] = [exe]

    def _hygiene(token: str) -> str:
        """剔除 CR / LF / NUL，防止命令行层被注入控制字符或截断。"""
        if token is None:
            return ""
        if not isinstance(token, str):
            token = str(token)
        for bad in ("\x00", "\r", "\n"):
            if bad in token:
                token = token.replace(bad, "")
        return token

    def _sanitize_file_path_arg(token: str) -> str:
        """把文件路径变成绝对路径（若存在），并做 hygiene。绝对路径天然不会被当作选项。"""
        t = _hygiene(token)
        if not t:
            return t
        p = Path(t)
        try:
            if p.exists() or p.parent.exists():
                # 走绝对路径：避免 "-foo.xyz" 被 obabel 当选项
                return os.path.abspath(os.fspath(p))
        except OSError:
            pass
        if not p.is_absolute():
            # 相对路径以 "-" 开头：用 "./" 前缀（或 Windows 下 ".\\"）防被 obabel 当选项
            if p.name.startswith("-"):
                return os.path.join(".", os.fspath(p))
        return os.path.abspath(os.fspath(p)) if not t.startswith("-") else os.path.join(".", t)

    rest: List[str] = []
    if args[0] in ("obabel", "obabel.exe", str(Path("obabel"))):
        rest = list(args[1:])
    else:
        rest = list(args)

    # 规则：
    #   - 如果 token 形如 "-O"、"-m" 这种单字母开关，或 "--..." 长选项，原样保留；
    #   - 如果 token 前面紧跟的开关是 "-O" / "-xi" / "-xo" / "--align" / "-a" / "-s" / "-v" / "-O"
    #     等已知要求文件 / 字符串值的开关，就做对应清洗（文件或纯字符串 hygiene）。
    #   - 其余自由位置参数视为输入文件 → _sanitize_file_path_arg。
    FILE_SWITCHES = {"-O", "-xi", "-xo", "-xr", "-xc", "--align", "-p"}
    VALUE_SWITCHES_EXPECT_FILE = FILE_SWITCHES

    i = 0
    n = len(rest)
    while i < n:
        tok = _hygiene(rest[i])
        if not tok:
            i += 1
            continue
        if tok == "--":
            # 显式「之后全是位置参数」：之后全部视为输入文件
            real_args.append("--")
            i += 1
            while i < n:
                real_args.append(_sanitize_file_path_arg(rest[i]))
                i += 1
            break
        if tok in VALUE_SWITCHES_EXPECT_FILE and i + 1 < n:
            real_args.append(tok)
            real_args.append(_sanitize_file_path_arg(rest[i + 1]))
            i += 2
            continue
        if tok.startswith("-"):
            # 其他开关（可能无值）：直接加，值用 hygiene
            real_args.append(tok)
            # 处理 紧接的值（如果不是另一个开关，就做 hygiene）
            if i + 1 < n and not rest[i + 1].startswith("-"):
                real_args.append(_hygiene(rest[i + 1]))
                i += 2
            else:
                i += 1
            continue
        # 自由位置参数 → 输入文件
        real_args.append(_sanitize_file_path_arg(tok))
        i += 1

    return subprocess.run(
        real_args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        shell=False,
        **kwargs
    )


# 输出路径安全校验：统一包装（审计 1.1 路径遍历修复）
def _secure_output_path(
    requested_path,
    *,
    base_dir=None,
    is_dir: bool = False,
    default_name=None,
    allow_outside: bool = False,
    create_parent: bool = False,
) -> Path:
    """
    对 obabel 输出文件 / 目录路径做安全解析。
    base_dir 若为 None：
      - 若 requested_path 是绝对路径，走 allow_outside 判定（默认仍不允许越出 None 语义
        上的 work_dir，但若调用方明确 allow_outside=True 则放行）；
      - 若为相对路径：以当前工作目录（tempfile.gettempdir fallback）为基准，
        但我们更建议调用方显式传 base_dir。
    """
    from model import resolve_secure_output_path_external

    if base_dir is None:
        # 兜底：优先当前 cwd；不存在时 fallback 到临时目录
        try:
            cwd = Path.cwd()
            if cwd.is_dir():
                base_dir = cwd
            else:
                raise RuntimeError
        except Exception:
            base_dir = Path(tempfile.gettempdir())
    return resolve_secure_output_path_external(
        requested_path,
        base_dir=base_dir,
        is_dir=is_dir,
        default_name=default_name,
        allow_outside=allow_outside,
        create_parent=create_parent,
    )


# ======================== 环境检测 ========================
def check_openbabel() -> Tuple[bool, str, Dict[str, Any]]:
    """
    检测 Open Babel 是否可用，优先使用 pybel 接口，其次检测命令行。

    增强版（问题三修复）：返回更详细信息，包括版本、支持的格式数、接口类型、
    用户指定的路径、安装指引 install_guide 等。

    返回: (可用性, 消息, 详情字典)
    详情字典 keys:
      - interfaces_available: list[str]  ("pybel" / "cli")
      - pybel_version / cli_version: str 或 None
      - supported_format_count: int 或 None
      - warnings: list[str]
      - manual_path_used: bool
      - resolved_cli_path: str | None
      - install_guide: str (OB_INSTALL_GUIDE，UI 可直接展示)
      - diagnosis: str[] (按严重程度排的诊断建议)
    """
    global _MANUAL_OBABEL_PATH
    details: Dict[str, Any] = {
        "interfaces_available": [],
        "pybel_version": None,
        "cli_version": None,
        "supported_format_count": None,
        "warnings": [],
        "manual_path_used": False,
        "resolved_cli_path": None,
        "install_guide": OB_INSTALL_GUIDE,
        "diagnosis": [],
    }
    warning_list: list[str] = details["warnings"]
    diagnosis_list: list[str] = details["diagnosis"]

    # 首次探测：懒加载手动路径 + 检查手动路径是否配了
    if _MANUAL_OBABEL_PATH is None:
        _MANUAL_OBABEL_PATH = _load_manual_from_config()
    if _MANUAL_OBABEL_PATH:
        details["manual_path_used"] = True

    # 1. 检测 pybel
    pybel_ok = False
    if PYBEL_AVAILABLE:
        try:
            mol = pybel.readstring("smi", "C")
            if mol:
                pybel_ok = True
                details["interfaces_available"].append("pybel")
                try:
                    details["pybel_version"] = getattr(ob, "__version__", None) or \
                                                getattr(pybel, "__version__", None) or \
                                                "unknown"
                except Exception as _ve:
                    logger.debug("pybel 版本探测失败: %s", _ve)
                try:
                    n_fmts = len(getattr(pybel, "informats", {})) + len(getattr(pybel, "outformats", {}))
                    details["supported_format_count"] = max(0, n_fmts)
                except Exception as _ce:
                    logger.debug("pybel 支持格式计数失败: %s", _ce)
            else:
                warning_list.append("pybel 导入但无法创建测试分子 CH4")
        except Exception as e:
            warning_list.append(f"pybel 接口异常: {e}")
    else:
        diagnosis_list.append("未检测到 Python 包 pybel/openbabel（仅影响部分高级功能），建议安装 conda 版 OpenBabel")

    # 2. 检测命令行
    cli_ok = False
    try:
        # 先拿到解析到的路径（失败也不能让整个 check 抛错，只记 warning）
        try:
            exe = _resolve_obabel_cli()
            details["resolved_cli_path"] = exe
        except Exception as _re:
            warning_list.append(f"obabel 命令行未找到或不可用: {_re}")
            exe = None
        if exe:
            result = _run_obabel(["obabel", "-V"], timeout=OB_VERSION_TIMEOUT_SEC)
            if result.returncode == 0 and result.stdout.strip():
                cli_ok = True
                details["interfaces_available"].append("cli")
                details["cli_version"] = result.stdout.strip()
            else:
                warning_list.append(f"obabel -V 返回码={result.returncode}, stderr={result.stderr[:200]}")
    except Exception as e:
        warning_list.append(f"无法运行 obabel 命令行: {e}")

    if not cli_ok:
        if details.get("manual_path_used"):
            diagnosis_list.append("已配置手动 obabel 路径，但命令行仍不可用，请检查路径是否指向正确的可执行文件（Windows 下应为 obabel.exe）")
        else:
            diagnosis_list.append("未找到 obabel 命令行：推荐执行 conda install -c conda-forge openbabel，或使用下方「手动选择路径」")
        diagnosis_list.append("点击状态栏右下角的红点（OB 指示灯）可查看完整诊断并一键进入手动路径设置")

    # 汇总
    available = pybel_ok or cli_ok
    if available:
        if pybel_ok and cli_ok:
            msg = f"pybel + CLI 双接口可用（pybel={details['pybel_version']}, cli={details['cli_version']}）"
        elif pybel_ok:
            msg = f"pybel 接口可用（版本={details['pybel_version']}）"
        else:
            msg = f"obabel 命令行可用（版本={details['cli_version']}）"
        if details["supported_format_count"]:
            msg += f"，支持约 {details['supported_format_count']} 种格式"
        if details["resolved_cli_path"]:
            msg += f"；CLI 路径={details['resolved_cli_path']}"
        if details["manual_path_used"]:
            msg += "（使用用户手动指定路径）"
        if warning_list:
            msg += f"（警告：{len(warning_list)} 条）"
    else:
        msg_parts = ["OpenBabel 不可用"]
        if warning_list:
            msg_parts.append("；".join(warning_list[:2]))
        msg_parts.append("点击下方「环境诊断」或状态栏红点查看完整解决指引")
        msg = "，".join(msg_parts)

    return available, msg, details


def check_openbabel_simple() -> Tuple[bool, str]:
    """兼容旧调用方：只返回 (bool, str)，内部调用增强版。"""
    ok, msg, _ = check_openbabel()
    return ok, msg


def get_supported_formats() -> List[str]:
    """
    获取 Open Babel 支持的读写格式列表。
    返回格式名称列表（字符串）。
    """
    formats: set[str] = set()
    if PYBEL_AVAILABLE:
        try:
            formats.update(pybel.informats.keys())
            formats.update(pybel.outformats.keys())
        except AttributeError as _ae:
            logger.debug("pybel informats/outformats 属性缺失: %s", _ae)
    else:
        try:
            result = _run_obabel(["obabel", "-L", "formats"], timeout=OB_PROPLIST_TIMEOUT_SEC)
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    parts = line.strip().split()
                    if parts:
                        formats.add(parts[0])
        except Exception as _e:
            logger.debug("obabel CLI 查询格式列表失败: %s", _e)
    return sorted(formats)


# ======================== 格式转换 ========================
# 集中化：从 constants 复用，避免多处重复硬编码
_COMMON_IN_FORMATS = COMMON_INPUT_FORMATS

def _read_molecules(input_path: str, input_ext: str) -> list:
    """从 pybel 读入，空扩展名时先尝试常见扩展名，失败后再穷举。带 (path,mtime,size,ext) LRU 缓存；读写均加锁。"""
    ck = _cache_key(input_path)
    cache_full_key: tuple | None = (ck[0], ck[1], ck[2], input_ext) if ck is not None else None
    if cache_full_key is not None:
        with _MOL_READ_CACHE_LOCK:
            if cache_full_key in _MOL_READ_CACHE:
                return list(_MOL_READ_CACHE[cache_full_key])
    if input_ext:
        result = list(pybel.readfile(input_ext, input_path))
    else:
        result = []
        tried_paths: list[tuple[str, str]] = []
        for fmt in _COMMON_IN_FORMATS:
            try:
                mols = list(pybel.readfile(fmt, input_path))
                if mols:
                    result = mols
                    break
            except Exception as e:
                tried_paths.append((fmt, str(e)))
        if not result:
            for fmt in pybel.informats:
                if fmt in _COMMON_IN_FORMATS:
                    continue
                try:
                    mols = list(pybel.readfile(fmt, input_path))
                    if mols:
                        result = mols
                        break
                except Exception:
                    continue
    if cache_full_key is not None:
        with _MOL_READ_CACHE_LOCK:
            if len(_MOL_READ_CACHE) >= _MOL_READ_CACHE_MAX:
                try:
                    k = next(iter(_MOL_READ_CACHE))
                    del _MOL_READ_CACHE[k]
                except StopIteration:
                    pass
            _MOL_READ_CACHE[cache_full_key] = list(result)
    return result

def convert_file(input_path: str, output_path: str, output_format: str) -> Dict[str, Any]:
    """
    转换分子文件格式。
    返回: {'success': bool, 'message': str, 'output_path': str}
    """
    # 处理输出路径扩展名
    base, ext = os.path.splitext(output_path)
    if not ext or ext[1:].lower() != output_format.lower():
        output_path = f"{base}.{output_format}" if base else f"output.{output_format}"

    # 【审计 1.1 路径遍历加固】：输出路径走安全解析，创建父目录
    try:
        output_path = str(_secure_output_path(output_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出路径非法: {e}", "output_path": None}

    try:
        if PYBEL_AVAILABLE:
            input_ext = os.path.splitext(input_path)[1][1:].lower()
            mols = _read_molecules(input_path, input_ext)
            if not mols:
                return {"success": False, "message": "无法读取输入文件（没有可识别的分子）"}

            # 写入输出
            with pybel.Outputfile(output_format, output_path, overwrite=True) as out:
                for mol in mols:
                    out.write(mol)
            return {"success": True, "message": f"成功转换为 {output_format}", "output_path": output_path}
        else:
            # 使用命令行
            cmd = ["obabel", input_path, "-O", output_path]
            result = _run_obabel(cmd, timeout=OB_CONVERT_TIMEOUT_SEC)
            if result.returncode == 0 and os.path.exists(output_path):
                return {"success": True, "message": f"成功转换为 {output_format}", "output_path": output_path}
            else:
                return {"success": False, "message": f"转换失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}


# ======================== SMILES → 分子 ========================
def generate_from_smiles(
    smiles: str,
    output_prefix: str,
    output_dir: str = ".",
    generate_3d: bool = True,
    optimize: bool = True,
    forcefield: str = DEFAULT_FORCEFIELD,
) -> Dict[str, Any]:
    """
    从 SMILES 生成 3D 分子文件（.mol 和 .xyz）。
    返回: {'success': bool, 'message': str, 'mol': str, 'xyz': str}
    """
    # 【审计 1.1】输出目录安全解析
    try:
        output_dir = str(_secure_output_path(output_dir, is_dir=True, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出目录非法: {e}", "mol": None, "xyz": None}
    # 同样校验 prefix：避免包含路径分隔符 / ..，保证只会在 output_dir 下生成文件
    try:
        from model import enforce_no_path_separators
    except Exception:
        def enforce_no_path_separators(name: str) -> None:
            if any(ch in name for ch in ("/", "\\", "\x00", "\r", "\n")):
                raise ValueError(f"文件名前缀包含非法字符: {name!r}")
    try:
        enforce_no_path_separators(output_prefix)
    except ValueError as e:
        return {"success": False, "message": f"文件前缀非法: {e}", "mol": None, "xyz": None}

    mol_path = os.path.join(output_dir, f"{output_prefix}.mol")
    xyz_path = os.path.join(output_dir, f"{output_prefix}.xyz")

    try:
        if PYBEL_AVAILABLE:
            mol = pybel.readstring("smi", smiles)
            if mol is None:
                return {"success": False, "message": "无效的 SMILES", "mol": None, "xyz": None}

            if generate_3d:
                mol.make3D()
                if optimize:
                    # 根据 forcefield 选择优化
                    mol.localopt(forcefield=forcefield, steps=500)

            # 写入 .mol 和 .xyz（基于同一个分子对象）
            mol.write("mol", mol_path, overwrite=True)
            mol.write("xyz", xyz_path, overwrite=True)
            return {"success": True, "message": "生成成功", "mol": mol_path, "xyz": xyz_path}
        else:
            # 命令行模式：先生成 .mol，再转换为 .xyz（避免重复 gen3d）
            # 生成 .mol（含 3D 和优化）
            cmd_mol = ["obabel", f"-:{smiles}", "-O", mol_path]
            if generate_3d:
                cmd_mol.append("--gen3d")
                if optimize:
                    cmd_mol.extend(["--minimize", "--ff", forcefield])
            # gen3d + minimize 对大分子可能较慢，使用较大超时
            result_mol = _run_obabel(cmd_mol, timeout=OB_LARGE_TIMEOUT_SEC)
            if result_mol.returncode != 0 or not os.path.exists(mol_path):
                return {
                    "success": False,
                    "message": f"生成 .mol 失败: {result_mol.stderr.strip()}",
                    "mol": None,
                    "xyz": None
                }

            # 从 .mol 转换为 .xyz（无需重新优化）
            cmd_xyz = ["obabel", mol_path, "-O", xyz_path]
            result_xyz = _run_obabel(cmd_xyz, timeout=30)
            if result_xyz.returncode == 0 and os.path.exists(xyz_path):
                return {"success": True, "message": "生成成功", "mol": mol_path, "xyz": xyz_path}
            else:
                # 即使 xyz 失败，mol 已生成，可返回部分成功
                return {
                    "success": True,
                    "message": f".mol 成功，但 .xyz 转换失败: {result_xyz.stderr.strip()}",
                    "mol": mol_path,
                    "xyz": None
                }
    except Exception as e:
        return {"success": False, "message": str(e), "mol": None, "xyz": None}


# ======================== 力场优化 ========================
def optimize_geometry(input_path: str, output_path: str,
                      forcefield: str = DEFAULT_FORCEFIELD) -> Dict[str, Any]:
    """
    使用 Open Babel 力场优化分子结构。
    返回: {'success': bool, 'message': str, 'output_path': str}
    """
    # 【审计 1.1】输出路径安全解析
    try:
        output_path = str(_secure_output_path(output_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出路径非法: {e}", "output_path": None}

    try:
        if PYBEL_AVAILABLE:
            # 自动检测输入格式
            ext = os.path.splitext(input_path)[1][1:].lower()
            if not ext:
                # 尝试 pybel 自动识别
                mols = None
                for fmt in pybel.informats:
                    try:
                        mols = list(pybel.readfile(fmt, input_path))
                        if mols:
                            break
                    except Exception:
                        continue
                if not mols:
                    return {"success": False, "message": "无法识别输入文件格式", "output_path": None}
            else:
                mols = list(pybel.readfile(ext, input_path))

            if not mols:
                return {"success": False, "message": "无法读取分子", "output_path": None}

            mol = mols[0]
            # 确保有 3D 结构（如果没有则生成）
            if not mol.OBMol.Has3D():
                mol.make3D()

            # 优化
            try:
                mol.localopt(forcefield=forcefield, steps=500)
            except TypeError:
                # 旧版参数可能不同
                mol.localopt(ff=forcefield, steps=500)

            # 写入输出（保持原格式或用户指定格式）
            out_ext = os.path.splitext(output_path)[1][1:] or ext
            mol.write(out_ext, output_path, overwrite=True)
            return {"success": True, "message": "优化完成", "output_path": output_path}
        else:
            # 命令行优化：obabel input -O output --minimize --ff MMFF94
            cmd = ["obabel", input_path, "-O", output_path, "--minimize", "--ff", forcefield]
            # 力场优化对大分子较慢，使用 OB_LARGE_TIMEOUT_SEC
            result = _run_obabel(cmd, timeout=OB_LARGE_TIMEOUT_SEC)
            if result.returncode == 0 and os.path.exists(output_path):
                return {"success": True, "message": "优化完成", "output_path": output_path}
            else:
                return {"success": False, "message": f"优化失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}


# ======================== 计算描述符 ========================
@performance_timer(name="ob.calculate_descriptors", level=logging.DEBUG, min_ms=10.0)
def calculate_descriptors(input_path: str) -> Dict[str, Any]:
    """
    计算分子描述符（分子量、logP、TPSA、氢键供体/受体、可旋转键、环数等）。
    返回: {'success': bool, 'message': str, 'descriptors': dict}
    带 LRU 缓存：基于 (path_resolved, mtime_ns, size) 命中直接返回；读写加锁。
    """
    ck = _cache_key(input_path)
    if ck is not None:
        with _DESC_CACHE_LOCK:
            if ck in _DESC_CACHE:
                return dict(_DESC_CACHE[ck])
    descriptors: Dict[str, Any] = {}
    try:
        if PYBEL_AVAILABLE:
            ext = os.path.splitext(input_path)[1][1:].lower()
            mols = _read_molecules(input_path, ext)
            if not mols:
                result = {"success": False, "message": "无法读取分子", "descriptors": {}}
            else:
                mol = mols[0]
                obmol = mol.OBMol
                descriptors = {
                    "molecular_weight": 0.0,
                    "logP": 0.0,
                    "tpsa": 0.0,
                    "heavy_atoms": obmol.NumAtoms() if hasattr(obmol, "NumAtoms") else len(mol.atoms),
                    "bonds": obmol.NumBonds() if hasattr(obmol, "NumBonds") else None,
                    "hbd": obmol.NumHBD() if hasattr(obmol, "NumHBD") else 0,
                    "hba": obmol.NumHBA() if hasattr(obmol, "NumHBA") else 0,
                    "rotors": obmol.NumRotors() if hasattr(obmol, "NumRotors") else 0,
                    "rings": obmol.NumSSSR() if hasattr(obmol, "NumSSSR") else 0,
                }
                for attr_name, attr_key in (("molwt", "molecular_weight"),
                                            ("logP", "logP"), ("tpsa", "tpsa")):
                    try:
                        v = getattr(mol, attr_name)
                        if callable(v):
                            v = v()
                        descriptors[attr_key] = float(v)
                    except Exception as _de:
                        logger.debug("计算描述符 %s 失败: %s", attr_key, _de)
                result = {"success": True, "message": "描述符计算成功", "descriptors": descriptors}
        else:
            # 命令行模式（有限支持）
            with tempfile.NamedTemporaryFile(suffix=".prop", delete=False) as tmp:
                tmp_name = tmp.name
            try:
                cmd = ["obabel", input_path, "-o", "prop", "-O", tmp_name]
                cmd_result = _run_obabel(cmd, timeout=30)
                if cmd_result.returncode == 0 and os.path.exists(tmp_name):
                    with open(tmp_name, 'r', encoding='utf-8', errors='replace') as f:
                        data = f.read()
                    descriptors["info"] = data.strip()
                else:
                    descriptors["error"] = "命令行模式获取描述符失败"
            finally:
                if os.path.exists(tmp_name):
                    try:
                        os.unlink(tmp_name)
                    except OSError as _oe:
                        logger.debug("清理 obabel 临时描述符文件失败: %s, err=%s", tmp_name, _oe)
            result = {"success": True, "message": "命令行模式描述符（有限）", "descriptors": descriptors}
    except Exception as e:
        result = {"success": False, "message": str(e), "descriptors": {}}
    if ck is not None:
        with _DESC_CACHE_LOCK:
            if len(_DESC_CACHE) >= _DESC_CACHE_MAX:
                try:
                    k = next(iter(_DESC_CACHE))
                    del _DESC_CACHE[k]
                except StopIteration:
                    pass
            _DESC_CACHE[ck] = dict(result)
    return result


# ======================== O3：分子式 / 精确分子量 / 元素百分比 ========================
def analyze_formula(input_path: str) -> Dict[str, Any]:
    """
    选中一个文件返回：
      formula (字符串，例：CH4)
      exact_mass (精确分子量，浮点)
      molecular_weight (平均分子量)
      atoms_count (原子总数)
      elements_pct: {"C": 75.0, "H": 25.0, ...} （元素→质量百分比 %）
    """
    try:
        ext = os.path.splitext(input_path)[1][1:].lower()
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        mol = mols[0]
        obmol = mol.OBMol
        formula = ""
        mw_exact = 0.0
        mw_avg = 0.0
        elements: dict[str, int] = {}
        try:
            formula = obmol.GetFormula() if hasattr(obmol, "GetFormula") else ""
        except Exception as _e:
            logger.debug("obmol.GetFormula() 失败: %s", _e)
        try:
            mw_exact = float(obmol.GetExactMass()) if hasattr(obmol, "GetExactMass") else 0.0
        except Exception as _e:
            logger.debug("obmol.GetExactMass() 失败: %s", _e)
        try:
            mw_avg = float(obmol.GetMolWt()) if hasattr(obmol, "GetMolWt") else 0.0
        except Exception as _e:
            logger.debug("obmol.GetMolWt() 失败: %s", _e)
        try:
            atoms_iter = obmol.GetAtoms() if hasattr(obmol, "GetAtoms") else list(mol.atoms)
        except Exception as _e:
            logger.debug("obmol.GetAtoms() 失败，回退 mol.atoms: %s", _e)
            atoms_iter = list(mol.atoms)
        # 集中化：从 constants 复用原子量表，方便统一维护
        atomic_weights: Dict[str, float] = dict(ATOMIC_WEIGHTS)
        tot_mass = 0.0
        atoms_count = 0
        try:
            for a in atoms_iter:
                sym = a.GetSymbol() if hasattr(a, "GetSymbol") else a.symbol
                num = a.GetAtomicNum() if hasattr(a, "GetAtomicNum") else a.atomicnum
                w = atomic_weights.get(sym) or atomic_weights.get(sym.capitalize(), num or 12.0)
                elements[sym] = elements.get(sym, 0) + 1
                tot_mass += w
                atoms_count += 1
        except Exception as _ae:
            logger.debug("遍历原子失败，回退 formula 粗解析: %s", _ae)
            # 回退：按 formula 粗解析
            for m in re.findall(r"([A-Z][a-z]?)(\d*)", formula):
                if not m[0]:
                    continue
                cnt = int(m[1]) if m[1] else 1
                elements[m[0]] = elements.get(m[0], 0) + cnt
                tot_mass += atomic_weights.get(m[0], 12.0) * cnt
                atoms_count += cnt
        # 按 Hill 系统重排
        hill_parts: list[str] = []
        for k in ("C", "H"):
            if k in elements:
                hill_parts.append(f"{k}{elements[k] if elements[k] != 1 else ''}")
        for k in sorted(elements.keys()):
            if k in ("C", "H"):
                continue
            hill_parts.append(f"{k}{elements[k] if elements[k] != 1 else ''}")
        if not formula:
            formula = "".join(hill_parts)
        # 元素质量百分比
        pct: dict[str, float] = {}
        if tot_mass > 0:
            for sym, count in elements.items():
                w = atomic_weights.get(sym, 12.0)
                pct[sym] = round(count * w / tot_mass * 100.0, 2)
        if mw_avg <= 0 and tot_mass > 0:
            mw_avg = tot_mass
        return {
            "success": True,
            "formula": formula,
            "hill_formula": "".join(hill_parts),
            "exact_mass": mw_exact,
            "molecular_weight": mw_avg,
            "atoms_count": atoms_count,
            "elements": elements,
            "elements_pct": pct,
        }
    except Exception as e:
        return {"success": False, "message": f"元素分析失败：{e}"}


# ======================== O6：导出键长 / 键角 CSV ========================
def export_geometry_csv(input_path: str, out_csv_path: str) -> Dict[str, Any]:
    """
    提取分子所有键长（Å）及所有可能的 1-2-3 键角（度），写 CSV。
    纯 OpenBabel 实现，不依赖任何量化软件。
    """
    # 【审计 1.1】输出路径安全解析
    try:
        out_csv_path = str(_secure_output_path(out_csv_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出 CSV 路径非法: {e}"}
    try:
        ext = os.path.splitext(input_path)[1][1:].lower()
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        mol = mols[0]
        obmol = mol.OBMol

        # 原子 0-based → 符号 + 坐标 (Å)
        atoms_list: list[tuple[int, str, list[float]]] = []
        try:
            iter_atoms = list(obmol.GetAtoms())
        except Exception:
            iter_atoms = list(mol.atoms)
        for idx, a in enumerate(iter_atoms):
            if hasattr(a, "GetX"):
                sym = a.GetSymbol(); x, y, z = a.GetX(), a.GetY(), a.GetZ()
            else:
                sym = a.symbol; x, y, z = a.coords
            atoms_list.append((idx + 1, str(sym), [float(x), float(y), float(z)]))  # 1-based 编号

        # 键长
        bonds_list: list[tuple[int, int, str, str, float]] = []
        try:
            iter_bonds = list(obmol.GetBonds())
            for b in iter_bonds:
                i = b.GetBeginAtomIdx(); j = b.GetEndAtomIdx()
                if hasattr(b, "GetLength"):
                    length = float(b.GetLength())
                else:
                    import math
                    a1 = next((a for a in atoms_list if a[0] == i), None)
                    a2 = next((a for a in atoms_list if a[0] == j), None)
                    if not a1 or not a2:
                        continue
                    length = math.sqrt(sum((a1[2][k] - a2[2][k]) ** 2 for k in range(3)))
                sym_i = next((a[1] for a in atoms_list if a[0] == i), "?")
                sym_j = next((a[1] for a in atoms_list if a[0] == j), "?")
                bonds_list.append((i, j, sym_i, sym_j, round(length, 5)))
        except Exception:
            import itertools, math
            # 回退：根据原子间距 < 1.85Å 猜测键（通用有机分子，金属键可能不准）
            for (i1, s1, c1), (i2, s2, c2) in itertools.combinations(atoms_list, 2):
                d = math.sqrt(sum((c1[k] - c2[k]) ** 2 for k in range(3)))
                if d <= 1.85:
                    bonds_list.append((i1, i2, s1, s2, round(d, 5)))

        # 键角：对每个有至少 2 个邻居的原子作为中心原子，枚举两边
        angles_list: list[tuple[int, int, int, str, str, str, float]] = []
        try:
            neighbors: dict[int, list[int]] = {}
            for i, j, _, _, _ in bonds_list:
                neighbors.setdefault(i, []).append(j)
                neighbors.setdefault(j, []).append(i)
            import math
            sym_map = {a[0]: a[1] for a in atoms_list}
            coord_map = {a[0]: a[2] for a in atoms_list}
            for center, neigh in neighbors.items():
                if len(neigh) < 2:
                    continue
                import itertools as _it
                for a1, a2 in _it.combinations(neigh, 2):
                    if center not in coord_map or a1 not in coord_map or a2 not in coord_map:
                        continue
                    c, p1, p2 = coord_map[center], coord_map[a1], coord_map[a2]
                    v1 = [p1[k] - c[k] for k in range(3)]
                    v2 = [p2[k] - c[k] for k in range(3)]
                    dot = sum(v1[k] * v2[k] for k in range(3))
                    n1 = math.sqrt(sum(v1[k] ** 2 for k in range(3)))
                    n2 = math.sqrt(sum(v2[k] ** 2 for k in range(3)))
                    if n1 <= 0 or n2 <= 0:
                        continue
                    cosang = max(-1.0, min(1.0, dot / (n1 * n2)))
                    deg = math.degrees(math.acos(cosang))
                    angles_list.append((a1, center, a2,
                                        sym_map.get(a1, "?"), sym_map.get(center, "?"), sym_map.get(a2, "?"),
                                        round(deg, 3)))
        except Exception as e_ang:
            logger.debug("计算键角失败：%s", e_ang)
        # 写 CSV
        with open(out_csv_path, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.writer(f)
            wr.writerow([f"分子元素分析：{len(atoms_list)} 个原子，{len(bonds_list)} 根键"])
            wr.writerow([])
            wr.writerow(["键长表 (Bond Lengths)"])
            wr.writerow(["Atom1_Id", "Atom1", "Atom2_Id", "Atom2", "Length_A"])
            for i, j, si, sj, L in bonds_list:
                wr.writerow([i, si, j, sj, L])
            wr.writerow([])
            wr.writerow(["键角表 (Bond Angles，度)"])
            wr.writerow(["Atom1_Id", "Atom1", "Center_Id", "Center", "Atom3_Id", "Atom3", "Angle_deg"])
            for a, c, b, sa, sc, sb, deg in angles_list:
                wr.writerow([a, sa, c, sc, b, sb, deg])
        return {
            "success": True,
            "out_csv": out_csv_path,
            "n_atoms": len(atoms_list),
            "n_bonds": len(bonds_list),
            "n_angles": len(angles_list),
        }
    except Exception as e:
        return {"success": False, "message": f"导出几何参数失败：{e}"}


# ======================== O2：SMILES → InChIKey 搜索本地相似分子 ========================
def smiles_to_inchikey(smiles: str) -> Dict[str, Any]:
    """
    把一个 SMILES 字符串变成 InChIKey（第一块 14 字母 = 骨架相同可近似命中）。
    失败返回 success=False + message。
    """
    try:
        if not PYBEL_AVAILABLE:
            return {"success": False, "message": "需要安装 pybel/OpenBabel Python 包才能解析 SMILES"}
        smi = smiles.strip()
        if not smi:
            return {"success": False, "message": "SMILES 为空"}
        mol = pybel.readstring("smi", smi)
        if mol is None:
            return {"success": False, "message": f"无法解析 SMILES: {smiles}"}
        obmol = mol.OBMol
        obmol.AddHydrogens()
        try:
            obmol.PerceiveStereo()
        except Exception:
            pass
        inchikey = ""
        # pybel 方式
        try:
            inchikey = str(mol.write("inchikey")).strip().split("\n")[0].strip()
        except Exception:
            pass
        if not inchikey:
            try:
                conv = ob.OBConversion()
                conv.SetOutFormat("inchikey")
                inchikey = conv.WriteString(obmol).strip().split("\n")[0].strip()
            except Exception:
                pass
        if not inchikey or "InChIKey" not in inchikey and len(inchikey) < 10:
            return {"success": False, "message": f"InChIKey 生成失败: {inchikey!r}"}
        key = inchikey if "=" not in inchikey else inchikey.split("=", 1)[1].strip()
        key = key.strip()
        skeleton = key.split("-")[0] if "-" in key else key[:14]
        return {
            "success": True,
            "smiles": smi,
            "inchikey": key,
            "skeleton_14": skeleton.upper(),
            "canonical_smiles": mol.write("can").strip() if mol else smi,
            "formula": obmol.GetFormula() if hasattr(obmol, "GetFormula") else "",
        }
    except Exception as e:
        return {"success": False, "message": f"SMILES 解析失败：{e}"}


def batch_inchikey(paths: list[str]) -> Dict[str, str | None]:
    """
    批量把多个分子文件 → InChIKey dict: {abs_path: inchikey or None}。
    带 LRU（基于文件 cache_key）。
    """
    ret: Dict[str, str | None] = {}
    if not PYBEL_AVAILABLE:
        return {p: None for p in paths}
    for fp in paths:
        try:
            ext = os.path.splitext(fp)[1][1:].lower()
            mols = _read_molecules(fp, ext)
            if not mols:
                ret[fp] = None
                continue
            mol = mols[0]
            obmol = mol.OBMol
            ik = ""
            try:
                ik = str(mol.write("inchikey")).strip().split("\n")[0]
            except Exception as _e1:
                logger.debug("pybel.write(inchikey) 失败 %s: %s", fp, _e1)
            if not ik:
                try:
                    conv = ob.OBConversion()
                    conv.SetOutFormat("inchikey")
                    ik = conv.WriteString(obmol).strip().split("\n")[0]
                except Exception as _e2:
                    logger.debug("OBConversion(inchikey) 失败 %s: %s", fp, _e2)
            if "=" in ik:
                ik = ik.split("=", 1)[1].strip()
            ret[fp] = ik or None
        except Exception as _be:
            logger.debug("批量 InChIKey 处理失败 %s: %s", fp, _be)
            ret[fp] = None
    return ret


# ======================== O4：手性中心识别 + 对映体翻转 ========================
def analyze_chirality(input_path: str) -> Dict[str, Any]:
    """
    返回：
      n_centers: int (sp3 手性中心个数)
      centers: [{ idx_1based, symbol, label: R|S|? }]
      has_unknown: bool
    """
    try:
        ext = os.path.splitext(input_path)[1][1:].lower()
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        mol = mols[0]; obmol = mol.OBMol
        try:
            obmol.UnsetFlag(ob.OB_CHIRALITY_PERCEIVED)
            obmol.PerceiveStereo()
        except Exception:
            pass
        centers: list[Dict[str, Any]] = []
        n_atoms = obmol.NumAtoms() if hasattr(obmol, "NumAtoms") else 0
        try:
            stereo_data = list(obmol.GetAllStereoData())
        except Exception:
            stereo_data = []
        chiral_idxs: set[int] = set()
        label_by_idx: dict[int, str] = {}
        try:
            for sd in stereo_data:
                try:
                    typ = sd.GetType()
                    # OBStereo::Tetrahedral = 1
                    if typ == 1 or getattr(sd, "IsTetrahedral", lambda: False)():
                        refs = list(sd.GetReferenceAtoms())
                        if refs:
                            c = refs[0]
                            chiral_idxs.add(int(c))
                            try:
                                cfg = sd.GetConfig()
                                label_by_idx[int(c)] = "R" if cfg > 0 else ("S" if cfg < 0 else "?")
                            except Exception:
                                pass
                except Exception:
                    continue
        except Exception:
            pass
        # 兜底：FindStereoCenters
        if not chiral_idxs:
            try:
                ch = list(obmol.FindStereoCenters())
                for c in ch:
                    chiral_idxs.add(int(c))
            except Exception:
                pass
        sym = {a.GetIdx(): a.GetSymbol() for a in obmol.GetAtoms()} if hasattr(obmol, "GetAtoms") else {}
        for idx in sorted(chiral_idxs):
            centers.append({
                "idx_1based": int(idx),
                "symbol": sym.get(idx, "?"),
                "label": label_by_idx.get(idx, "?"),
            })
        return {
            "success": True,
            "n_centers": len(centers),
            "centers": centers,
            "has_unknown": any(c["label"] == "?" for c in centers),
            "total_atoms": n_atoms,
        }
    except Exception as e:
        return {"success": False, "message": f"手性分析失败：{e}"}


def invert_enantiomer(input_path: str, output_path: str) -> Dict[str, Any]:
    """翻转所有手性中心 → 生成对映体并写文件。"""
    try:
        # 【审计 1.1】输出路径安全解析
        try:
            output_path = str(_secure_output_path(output_path, create_parent=True))
        except ValueError as e:
            return {"success": False, "message": f"输出路径非法: {e}"}
        ext = os.path.splitext(input_path)[1][1:].lower()
        out_ext = os.path.splitext(output_path)[1][1:].lower()
        if not PYBEL_AVAILABLE:
            return {"success": False, "message": "需要 pybel"}
        mols = _read_molecules(input_path, ext)
        if not mols:
            return {"success": False, "message": "OpenBabel 无法读取该文件为分子"}
        mol = mols[0]; obmol = mol.OBMol
        try:
            obmol.UnsetFlag(ob.OB_CHIRALITY_PERCEIVED)
            obmol.PerceiveStereo()
        except Exception:
            pass
        try:
            obmol.InvertStereo()
        except Exception:
            # 回退：每个四面体 stereo data 取反配置
            try:
                for sd in list(obmol.GetAllStereoData()):
                    try:
                        typ = sd.GetType()
                        if typ == 1 or getattr(sd, "IsTetrahedral", lambda: False)():
                            cfg = sd.GetConfig()
                            sd.SetConfig(-cfg)
                    except Exception:
                        continue
            except Exception as e2:
                return {"success": False, "message": f"InvertStereo 不可用: {e2}"}
        mol2 = pybel.Molecule(obmol)
        mol2.write(out_ext or "xyz", output_path, overwrite=True)
        if not os.path.exists(output_path):
            return {"success": False, "message": "对映体写入失败"}
        return {"success": True, "output_path": output_path}
    except Exception as e:
        return {"success": False, "message": f"生成对映体失败：{e}"}


# ======================== O7：生理 pH=7.4 一键加氢 ========================
def protonate_ph(input_path: str, output_path: str, ph: float = 7.4) -> Dict[str, Any]:
    """
    用 `obabel -p <ph>` 做 pH 下的质子化：
      - COOH → COO⁻
      - NH2 → NH3⁺
      - 吡啶 N → N⁺H
    """
    try:
        if not 0 <= ph <= 14:
            return {"success": False, "message": "pH 范围 0-14"}
        # 【审计 1.1】输出路径安全解析
        try:
            output_path = str(_secure_output_path(output_path, create_parent=True))
        except ValueError as e:
            return {"success": False, "message": f"输出路径非法: {e}"}
        with tempfile.NamedTemporaryFile(suffix="." + (os.path.splitext(input_path)[1][1:] or "xyz"), delete=False) as _t1:
            pass
        with tempfile.NamedTemporaryFile(suffix="." + (os.path.splitext(output_path)[1][1:] or "xyz"), delete=False) as _t2:
            pass
        try:
            shutil.copy2(input_path, _t1.name)
            cmd = ["obabel", _t1.name, "-O", _t2.name, "-p", f"{ph:g}"]
            # pH 加氢需要构建完整 3D + 电荷分配，使用 OB_LARGE_TIMEOUT_SEC
            r = _run_obabel(cmd, timeout=OB_LARGE_TIMEOUT_SEC)
            if r.returncode != 0 or not os.path.exists(_t2.name) or os.path.getsize(_t2.name) == 0:
                return {"success": False, "message": f"obabel -p 返回码 {r.returncode}: {r.stderr[:300]}"}
            shutil.copy2(_t2.name, output_path)
            return {"success": True, "output_path": output_path, "ph": ph,
                    "message": f"已在 pH={ph:g} 下加氢：-COOH→-COO⁻、-NH2→-NH3⁺ 等"}
        finally:
            for t in (_t1.name, _t2.name):
                try:
                    os.unlink(t)
                except OSError as _oe:
                    logger.debug("清理 pH 加氢临时文件失败 %s: %s", t, _oe)
    except Exception as e:
        return {"success": False, "message": f"pH 加氢失败：{e}"}


# ======================== O8：SDF 拆分/合并 ========================
def split_multi_sdf(input_sdf: str, out_dir: str, prefix: str = "mol", format_ext: str = "xyz") -> Dict[str, Any]:
    """把一个 SDF（或任何多分子文件，.sdf/.mol2/.xyz 都行）拆成多个单分子文件。"""
    try:
        # 【审计 1.1】输出目录安全解析 + prefix 不允许包含路径分隔符
        try:
            out_dir = str(_secure_output_path(out_dir, is_dir=True, create_parent=True))
        except ValueError as e:
            return {"success": False, "message": f"输出目录非法: {e}"}
        try:
            from model import enforce_no_path_separators
        except Exception:
            def enforce_no_path_separators(name: str) -> None:
                if any(ch in name for ch in ("/", "\\", "\x00", "\r", "\n")):
                    raise ValueError(f"文件名前缀包含非法字符: {name!r}")
        try:
            enforce_no_path_separators(prefix)
        except ValueError as e:
            return {"success": False, "message": f"文件前缀非法: {e}"}
        if not PYBEL_AVAILABLE:
            return {"success": False, "message": "需要 pybel"}
        ext_in = os.path.splitext(input_sdf)[1][1:].lower() or "sdf"
        mols = _read_molecules(input_sdf, ext_in)
        if not mols:
            return {"success": False, "message": "未读取到任何分子"}
        ok = 0
        names: list[str] = []
        pad = max(3, len(str(len(mols))))
        ext_use = format_ext.lower().lstrip(".")
        for i, mol in enumerate(mols, 1):
            try:
                title = ""
                try:
                    title = mol.title.strip().replace("/", "_").replace("\\", "_").replace(":", "_")
                except Exception:
                    title = ""
                if not title:
                    title = f"{prefix}_{str(i).zfill(pad)}"
                name = f"{title}.{ext_use}"
                fp = os.path.join(out_dir, name)
                uniq = 1
                while os.path.exists(fp):
                    fp = os.path.join(out_dir, f"{title}_{uniq}.{ext_use}")
                    uniq += 1
                mol.write(ext_use, fp, overwrite=True)
                if os.path.exists(fp):
                    ok += 1; names.append(fp)
            except Exception as _we:
                logger.debug("拆分分子写入 %s 失败: %s", name, _we)
                continue
        return {"success": ok > 0, "total": len(mols), "ok": ok, "output_dir": out_dir, "files": names}
    except Exception as e:
        return {"success": False, "message": f"拆分多分子文件失败：{e}"}


def merge_to_sdf(input_paths: list[str], output_sdf: str) -> Dict[str, Any]:
    """把一堆分子文件（任意格式）合并成一个 SDF。"""
    try:
        if not PYBEL_AVAILABLE:
            return {"success": False, "message": "需要 pybel"}
        all_mols = []
        for fp in input_paths:
            try:
                ext = os.path.splitext(fp)[1][1:].lower()
                ms = _read_molecules(fp, ext) or []
                all_mols.extend(ms)
            except Exception as _re:
                logger.debug("SDF 合并跳过文件 %s: %s", fp, _re)
                continue
        if not all_mols:
            return {"success": False, "message": "未读取到任何分子"}
        # 【审计 1.1】输出 SDF 路径安全解析
        try:
            output_sdf = str(_secure_output_path(output_sdf, create_parent=True))
        except ValueError as e:
            return {"success": False, "message": f"输出 SDF 路径非法: {e}"}
        # 逐个 append 写 sdf（pybel write('sdf', multi=True)）
        with tempfile.NamedTemporaryFile(suffix=".sdf", delete=False, mode="wb") as _tmp:
            tmp_name = _tmp.name
        try:
            conv = ob.OBConversion() if PYBEL_AVAILABLE and 'ob' in globals() else None
            if conv is not None:
                conv.SetOutFormat("sdf")
                with open(tmp_name, "wb") as f:
                    for m in all_mols:
                        try:
                            if hasattr(m, "OBMol"):
                                s = conv.WriteString(m.OBMol)
                                if s: f.write(s.encode("utf-8", errors="replace"))
                        except Exception as _we:
                            logger.debug("SDF 合并写入单分子失败: %s", _we)
                            continue
            else:
                with open(tmp_name, "w", encoding="utf-8") as f:
                    for i, m in enumerate(all_mols):
                        try:
                            f.write(m.write("sdf"))
                        except Exception as _we:
                            logger.debug("SDF 合并 pybel.write 失败 (%d): %s", i, _we)
                            continue
            shutil.copy2(tmp_name, output_sdf)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError as _oe:
                logger.debug("清理 SDF 合并临时文件失败: %s, err=%s", tmp_name, _oe)
        size = os.path.getsize(output_sdf)
        return {"success": size > 0, "output_sdf": output_sdf, "molecules": len(all_mols), "bytes": size}
    except Exception as e:
        return {"success": False, "message": f"合并为 SDF 失败：{e}"}


# ======================== 分子叠加 ========================
def align_molecules(ref_path: str, mobile_path: str, output_path: str) -> Dict[str, Any]:
    """
    将移动分子叠加到参考分子上。
    返回: {'success': bool, 'message': str, 'output_path': str}
    """
    # 【审计 1.1】输出路径安全解析
    try:
        output_path = str(_secure_output_path(output_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出路径非法: {e}", "output_path": None}

    try:
        # 使用 obabel 的 --align 选项
        cmd = ["obabel", mobile_path, "-O", output_path, "--align", ref_path]
        result = _run_obabel(cmd, timeout=OB_CONVERT_TIMEOUT_SEC)
        if result.returncode == 0 and os.path.exists(output_path):
            return {"success": True, "message": "叠加成功", "output_path": output_path}
        else:
            return {"success": False, "message": f"叠加失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}


def render_png_2d(input_path: str, output_path: str, width: int = 800, height: int = 600) -> Dict[str, Any]:
    """渲染 2D PNG 图：优先 pybel → OBDepict，最后回退 obabel CLI。"""
    # 【审计 1.1】输出路径安全解析
    try:
        output_path = str(_secure_output_path(output_path, create_parent=True))
    except ValueError as e:
        return {"success": False, "message": f"输出路径非法: {e}", "output_path": None}

    try:
        if PYBEL_AVAILABLE:
            try:
                input_ext = os.path.splitext(input_path)[1][1:].lower()
                mols = _read_molecules(input_path, input_ext)
                if not mols:
                    return {"success": False, "message": "无法读取输入文件（没有可识别的分子）", "output_path": None}
                mol = mols[0]

                try:
                    depict = ob.OBDepict()
                    depict.SetWidth(width)
                    depict.SetHeight(height)
                    obmol = mol.OBMol
                    depict.DrawMolecule(obmol)
                    depict.WritePNG(output_path)
                    if os.path.exists(output_path):
                        return {"success": True, "message": "2D PNG 渲染成功（OBDepict）", "output_path": output_path}
                except Exception as _de:
                    logger.debug("OBDepict 渲染失败: %s", _de)

                try:
                    mol.draw(width=width, height=height, filename=output_path)
                    if os.path.exists(output_path):
                        return {"success": True, "message": "2D PNG 渲染成功（pybel.draw）", "output_path": output_path}
                except Exception as _de2:
                    logger.debug("pybel.draw 渲染失败: %s", _de2)
            except Exception as _re:
                logger.debug("读取分子失败（2D PNG 渲染阶段）: %s", _re)

        cmd = ["obabel", input_path, "-O", output_path, "-xS", "-xN", str(width), "-xW", str(height)]
        # 2D 渲染有时很慢，使用 OB_PNG_TIMEOUT_SEC
        result = _run_obabel(cmd, timeout=OB_PNG_TIMEOUT_SEC)
        if result.returncode == 0 and os.path.exists(output_path):
            return {"success": True, "message": "2D PNG 渲染成功（obabel CLI）", "output_path": output_path}
        else:
            return {"success": False, "message": f"渲染失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}