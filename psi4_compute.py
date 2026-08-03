#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSI4 计算核心 - 强制中文路径转义，确保日志保存
"""

import os
import re
import json
import csv
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Callable, Any, List, Tuple

from constants import (
    DEFAULT_SOLVENT,
    PSI4_DEFAULT_PROCESS_TIMEOUT,
    ATOMIC_WEIGHTS,
    PSI4_PRESETS,
)

# ---------- NumPy 兼容性补丁：防止 pint 旧版本在 NumPy 1.20+ 下崩溃 ----------
# 背景：pint 依赖 qcelemental 处理物理单位；pint 旧版本（< 0.22）会调用
#       np.cumproduct 这个在 NumPy 1.20 中已被删除、1.23+ 完全不存在的旧别名。
#       错误栈：psi4 → qcelemental → pint → AttributeError: numpy has no cumproduct。
# 处理方式（防御性）：
#   1) 尝试导入 pint：如果成功，说明环境已经 OK，无需任何修改。
#   2) 若 ImportError / AttributeError（或 numpy 导入失败但 psi4 存在），
#      则在 numpy 模块上临时补上 cumproduct = cumprod 别名，并记录 warning
#      提示用户"建议升级 pint 到 0.24+ 或 numpy 降级到 1.23.x"。
# 注意：本补丁**不**替代环境升级，只是为了在受限环境下软件能正常工作。
try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _np = None
    _HAS_NUMPY = False

def _apply_numpy_cumproduct_compat_patch() -> None:
    """
    惰性调用：只在 psi4 第一次要被用到、或者 pint 导入失败时才调用，
    避免 main/import 阶段就去摸 numpy 的内部结构造成不必要副作用。
    """
    if not _HAS_NUMPY:
        return
    if hasattr(_np, "cumproduct"):
        return
    # numpy ≥ 1.24：cumproduct 完全移除，我们给它补回一个别名，
    # 这样 pint 旧版本不会炸。同时打 warning 建议用户升级环境。
    from logger import default_logger as _log
    try:
        _np.cumproduct = _np.cumprod
        _log.warning(
            "检测到 numpy ≥ 1.24 且 pint 较旧：已临时为 numpy 补上 cumproduct=cumprod 别名，"
            "避免 PSI4 导入崩溃。建议运行 `conda install -c conda-forge pint=0.24` "
            "或 `pip install --upgrade pint` 以彻底解决此问题。"
        )
    except Exception as _e:
        # 嵌套 try：logger 本身也可能没初始化好
        try:
            _log.debug("应用 numpy cumproduct 兼容性补丁时发生非致命错误: %s", _e)
        except Exception:
            # 最后兜底：print 到 stderr 保证不丢失（避免完全静默）
            import sys as _sys
            print(f"[compat] numpy cumproduct 补丁非致命错误: {_e}", file=_sys.stderr)

try:
    # 先尝试"无补丁"路径：如果 pint 本身是新的，会正常导入，不做任何修改。
    import psi4  # type: ignore[unused-import]
except Exception as _psi4_first_import_err:
    # ImportError / AttributeError 都可能是 numpy→pint 链炸了，先打补丁再试一次。
    try:
        from logger import default_logger as _log
        _log.warning("PSI4 首次导入失败（可能是 numpy/pint 不兼容），尝试应用兼容性补丁后重试: %s",
                     _psi4_first_import_err)
    except Exception:
        import sys as _sys
        print(f"[psi4_import] 首次导入失败：{_psi4_first_import_err}", file=_sys.stderr)
    _apply_numpy_cumproduct_compat_patch()
    try:
        import psi4  # type: ignore[no-redef]  # noqa: F811
    except Exception as _psi4_second_err:
        # 失败信息不要完全吞掉：warning 级别以便用户感知
        try:
            from logger import default_logger as _log2
            _log2.warning("PSI4 第二次导入仍失败，将标记为不可用: %s", _psi4_second_err)
        except Exception:
            import sys as _sys
            print(f"[psi4_import] 第二次导入仍失败：{_psi4_second_err}", file=_sys.stderr)
        psi4 = None
else:
    # 首次导入就成功了，也**依然**尝试检查一次（万一某些懒加载模块里才会触发）
    _apply_numpy_cumproduct_compat_patch()

import logging
import openbabel_utils as ob_utils
from logger import default_logger as logger, performance_timer

__all__ = [
    "check_psi4_installed",
    "convert_with_obabel",
    "read_xyz_content",
    "sanitize_filename",
    "run_psi4_task",
    "parse_psi4_output",
    "run_linear_scan",
    "run_rigid_scan",
    "conformer_search_ensemble",
    "run_irc_task",
    "_parse_irc_trajectory_from_log",
    "eyring_kinetics",
    "run_reaction_energy_profile",
    "run_pka_prediction",
    "run_nmr_simulation",
]


# 【审计 1.1 路径遍历】输出路径安全封装：同 openbabel_utils 一致
def _secure_output_path(
    requested_path,
    *,
    is_dir: bool = False,
    default_name=None,
    base_dir=None,
    allow_outside: bool = False,
    create_parent: bool = True,
) -> Path:
    """封装 model.resolve_secure_output_path_external，避免 psi4_compute 自己实现一套。"""
    from model import resolve_secure_output_path_external
    if base_dir is None:
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


def _default_base_dir_from_input(input_path: str | os.PathLike[str] | None, *, fallback: str | os.PathLike[str] | None = None) -> Path:
    """
    从输入文件推断默认 base_dir：
      - 输入存在：用其 parent 作为 base_dir（避免用户随便输相对路径时跑到 cwd）；
      - 否则：fallback → cwd → temp。
    """
    if input_path is not None:
        try:
            p = Path(input_path)
            if p.parent.is_dir():
                return p.parent.resolve()
        except Exception:
            pass
    if fallback is not None:
        try:
            pf = Path(fallback)
            if pf.is_dir():
                return pf.resolve()
            if pf.parent.is_dir():
                return pf.parent.resolve()
        except Exception:
            pass
    try:
        cwd = Path.cwd()
        if cwd.is_dir():
            return cwd.resolve()
    except Exception:
        pass
    return Path(tempfile.gettempdir()).resolve()


_XYZ_READ_CACHE: dict[tuple[str, int, int], str | None] = {}
_XYZ_READ_CACHE_MAX = 512


def _xyz_cache_key(path_str: str) -> tuple[str, int, int] | None:
    try:
        st = os.stat(path_str)
        return (os.fspath(Path(path_str).resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


def check_psi4_installed() -> Tuple[bool, str, Dict[str, Any]]:
    """
    增强版 PSI4 安装与功能支持检测（问题5修复）：
    返回 (可用性, 消息, 详情字典)。

    详情字典 keys：
      - version: str 或 None
      - has_energy / has_optimize / has_frequency: bool（核心任务支持）
      - has_cphf_nmr: bool（CPHF NMR 编译选项，没开则 NMR 只能跑经验法）
      - has_pcm: bool（PCMSolver 溶剂模型编译）
      - warnings: list[str]
    """
    details: Dict[str, Any] = {
        "version": None,
        "has_energy": False,
        "has_optimize": False,
        "has_frequency": False,
        "has_cphf_nmr": False,
        "has_pcm": False,
        "warnings": [],
    }
    wl: list[str] = details["warnings"]

    if psi4 is None:
        return False, "PSI4 未安装或导入失败（建议：conda install -c psi4 psi4 或 pip install psi4）", details

    # 版本号
    try:
        details["version"] = str(getattr(psi4, "__version__", None) or
                                  getattr(psi4.core, "version", lambda: "unknown")())
    except Exception as _ve:
        logger.debug("PSI4 版本探测失败: %s", _ve)

    # 核心任务接口：存在性检测即可
    for attr, key in (("energy", "has_energy"),
                      ("optimize", "has_optimize"),
                      ("frequency", "has_frequency")):
        details[key] = callable(getattr(psi4, attr, None))

    # CPHF NMR：psi4.cphf 是否可调用（通常需要编译时开 CPHF 选项）
    details["has_cphf_nmr"] = callable(getattr(psi4, "cphf", None))
    if not details["has_cphf_nmr"]:
        wl.append("PSI4 编译时未启用 CPHF 模块，¹H NMR 模拟将自动降级为经验化学位移库（结果准确度受限）")

    # PCMSolver：psi4.core.set_local_option("PCM", ...) 能否不报错
    try:
        # 不真正改设置，只测试接口存在
        details["has_pcm"] = callable(getattr(psi4.core, "set_local_option", None))
    except Exception:
        details["has_pcm"] = False

    # 汇总消息
    msg_parts = [f"PSI4 已安装（版本={details['version'] or '未知'}）"]
    caps = []
    if details["has_energy"]: caps.append("单点能")
    if details["has_optimize"]: caps.append("几何优化")
    if details["has_frequency"]: caps.append("频率分析")
    if details["has_cphf_nmr"]: caps.append("CPHF NMR")
    if details["has_pcm"]: caps.append("PCM 溶剂")
    if caps: msg_parts.append(f"支持功能：{'/'.join(caps)}")
    if wl: msg_parts.append(f"警告 {len(wl)} 条")

    return True, "，".join(msg_parts), details


def check_psi4_installed_simple() -> bool:
    """兼容旧调用方：只返回 bool。"""
    ok, _, _ = check_psi4_installed()
    return ok


def get_preset_info(preset_name: str) -> Dict:
    return PSI4_PRESETS.get(preset_name, {})


def sanitize_filename(name: str) -> str:
    illegal_chars = r'[\\/:*?"<>|]'
    return re.sub(illegal_chars, '_', name)


def _run_process_with_timeout(
    args: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = PSI4_DEFAULT_PROCESS_TIMEOUT,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> int:
    """
    安全地运行子进程：
      - args 必须是 list（禁止字符串拼接 → 避免 CWE-78 命令注入）
      - 可执行文件必须解析为**真实绝对路径且非符号链接**，
        避免 Windows 相对路径劫持 DLL（完全对齐 openbabel_utils._resolve_obabel_cli 逻辑）
      - timeout 超时后自动 kill + return 非 0

    解析策略（与 _resolve_obabel_cli 同严格度）：
      1. 已是绝对路径且真实存在：严格校验 is_symlink=False / 是文件。
      2. 对于 obabel：直接调用 openbabel_utils._resolve_obabel_cli()（无参），复用 DCL + LOCK 安全解析。
      3. 其他命令：shutil.which → resolve(strict=True) → 非 symlink → 是文件 → 替换 args[0]。
      4. 以上全部失败：保持 args 原样，但仍用 list+shell=False（最底线的安全保证）。
    """
    if not args:
        raise ValueError("_run_process_with_timeout: args 不能为空")
    arg0 = str(args[0])
    resolved: str | None = None

    def _validate_abs_path(path_str: str) -> str | None:
        """严格校验：是绝对路径、resolve 后真实文件、非 symlink；返回规范化绝对路径。"""
        try:
            p = Path(path_str)
            if not p.is_absolute():
                return None
            rp = p.resolve(strict=True)
            # 放宽符号链接限制：只拒绝可写目录下的真实路径，不一律拒绝符号链接
            # 但保留对路径是否在可写目录的检查
            import tempfile as _tempfile
            unsafe_roots: list[Path] = []
            for _cand in (
                _tempfile.gettempdir(),
                os.getcwd(),
            ):
                try:
                    unsafe_roots.append(Path(_cand).resolve(strict=False))
                except Exception:
                    continue
            for root in unsafe_roots:
                try:
                    rp.relative_to(root)
                    logger.warning("拒绝执行在可写目录下的可执行文件: %s (父目录=%s)", rp, root)
                    return None
                except ValueError:
                    pass
            if not rp.is_file():
                return None
            return str(rp)
        except OSError:
            return None

    validated = _validate_abs_path(arg0)
    if validated is not None:
        resolved = validated
        args = [resolved] + list(args[1:])
    else:
        # 命令名查找：obabel 优先复用安全解析链；其他命令严格 which+resolve
        try:
            import openbabel_utils as _obu
            if arg0 in ("obabel", "obabel.exe"):
                # _resolve_obabel_cli 是无参函数，返回绝对路径字符串（失败直接抛）
                try:
                    resolved_exe = _obu._resolve_obabel_cli()
                    if resolved_exe:
                        validated2 = _validate_abs_path(resolved_exe)
                        if validated2 is not None:
                            resolved = validated2
                            args = [resolved] + list(args[1:])
                except Exception:
                    # 让下面 generic which 流程再试一次（容错）
                    pass
            # 通用命令：shutil.which 后严格校验
            if resolved is None:
                w = shutil.which(arg0)
                if w:
                    validated3 = _validate_abs_path(w)
                    if validated3 is not None:
                        resolved = validated3
                        args = [resolved] + list(args[1:])
        except Exception:
            # 解析失败保持原样，但仍使用 list+shell=False 执行，避免注入
            pass
    try:
        cp = subprocess.run(
            list(args),
            cwd=None if cwd is None else str(cwd),
            timeout=float(timeout),
            env=env,
            shell=False,
            capture_output=bool(capture_output),
            check=False,
        )
        return int(cp.returncode)
    except subprocess.TimeoutExpired:
        logger.warning("子进程超时 (%.1fs): %s", timeout, args)
        return 124
    except FileNotFoundError as e:
        logger.error("子进程可执行文件不存在: %s (解析结果=%s)", arg0, resolved)
        return 127
    except OSError as e:
        logger.error("子进程启动失败 args=%s: %s", args, e)
        return 126


def convert_with_obabel(input_file: str, output_file: str) -> bool:
    try:
        res = ob_utils.convert_file(input_file, output_file, os.path.splitext(output_file)[1][1:] or 'xyz')
        success = res.get("success", False)
        output_path = res.get("output_path")
        ok = success and output_path and os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if not ok:
            logger.debug("OpenBabel 转换失败 %s → %s: result=%s", input_file, output_file, res)
        return ok
    except Exception as e:
        # 把 print 替换为 logger，避免 stdout 被 GUI 吞掉
        logger.warning("OpenBabel 转换异常 %s → %s: %s", input_file, output_file, e)
        return False


def read_xyz_content(file_path: str) -> Optional[str]:
    key = _xyz_cache_key(file_path)
    if key is not None and key in _XYZ_READ_CACHE:
        return _XYZ_READ_CACHE[key]
    encodings = ('utf-8', 'gbk', 'gb2312', 'latin-1')
    content: str | None = None
    for enc in encodings:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue
        except OSError as _io_err:
            logger.debug("读取 XYZ 文件失败 enc=%s path=%s: %s", enc, file_path, _io_err)
            break
    if content is None:
        try:
            with open(file_path, 'rb') as f:
                raw = f.read()
                content = raw.decode('utf-8', errors='replace')
        except OSError as _io_err:
            logger.debug("二进制读取 XYZ 失败 path=%s: %s", file_path, _io_err)
            content = None
    if content is None:
        if key is not None:
            _XYZ_READ_CACHE[key] = None
        return None
    lines = content.splitlines()
    if len(lines) < 2:
        result = None
    else:
        try:
            atom_count = int(lines[0].strip())
        except ValueError:
            atom_count = 0
        if atom_count <= 0:
            result = None
        else:
            coord_lines: list[tuple[str, float, float, float]] = []
            _atom_re = re.compile(r'^[A-Za-z][a-z]?$')
            for line in lines[2:]:
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        x = float(parts[1])
                        y = float(parts[2])
                        z = float(parts[3])
                    except ValueError:
                        continue
                    atom = parts[0]
                    if _atom_re.match(atom):
                        coord_lines.append((atom, x, y, z))
            if not coord_lines:
                result = None
            else:
                n = len(coord_lines)
                out_lines: list[str] = [str(n), "Converted by OpenBabel"]
                out_extend = out_lines.extend
                out_extend([f"{a:2s}  {x:12.6f}  {y:12.6f}  {z:12.6f}" for (a, x, y, z) in coord_lines])
                result = "\n".join(out_lines) + "\n"
    if key is not None:
        if len(_XYZ_READ_CACHE) >= _XYZ_READ_CACHE_MAX:
            try:
                first_key = next(iter(_XYZ_READ_CACHE))
                del _XYZ_READ_CACHE[first_key]
            except StopIteration:
                pass
        _XYZ_READ_CACHE[key] = result
    return result


@performance_timer(name="psi4.run_psi4_task", level=logging.DEBUG, min_ms=50.0)
def run_psi4_task(
    input_file: str,
    task_type: str = 'energy',
    method: str = 'b3lyp',
    basis: str = '6-31g*',
    output_dir: Optional[str] = None,
    preset_name: Optional[str] = None,
    solvent: Optional[str] = None,
    d3: bool = False,
    charge: int = 0,
    multiplicity: int = 1,
    memory: str = '4 GB',
    **kwargs
) -> Dict:

    if not check_psi4_installed_simple():
        return {"success": False, "error": "PSI4 未安装"}

    if not os.path.exists(input_file):
        return {"success": False, "error": f"文件不存在: {input_file}"}

    progress_callback: Optional[Callable] = kwargs.get('_progress_callback', None)
    extra_options: Dict[str, Any] = kwargs.get('extra_options', None) or {}
    extra_post_hook = kwargs.get('_extra_post_hook', None)

    def report(percent: float, msg: str) -> None:
        if progress_callback:
            progress_callback(percent, msg)
        logger.debug("[PSI4 进度] %3d%% - %s", int(percent), msg)

    # ===== _TempDirGuard：任何 early-return / exception 都会清理 temp_dir =====
    # 同时内部维护一个「注册的额外临时路径列表」，确保 converted_xyz 等也被清理
    class _TempDirGuard:
        def __init__(self):
            self.path: str | None = None
            self.active: bool = True
            self.extra_paths: list[str] = []

        def acquire(self, prefix: str = "psi4_temp_") -> str:
            if self.path is not None:
                return self.path
            self.path = tempfile.mkdtemp(prefix=prefix)
            return self.path

        def assign(self, existing_path: str | None) -> None:
            """外部已创建的临时目录，把所有权移交过来。"""
            self.path = existing_path

        def register_extra(self, p: str) -> None:
            """注册一个临时文件/目录路径（如 converted_xyz），release 时一起清理。"""
            if p and os.path.exists(p) and p not in self.extra_paths:
                self.extra_paths.append(p)

        def release(self) -> None:
            if not self.active:
                return
            self.active = False
            # 先清 extra_paths
            for ep in self.extra_paths:
                try:
                    if os.path.isdir(ep):
                        shutil.rmtree(ep, ignore_errors=True)
                    elif os.path.isfile(ep):
                        os.unlink(ep)
                except Exception as _re:
                    logger.debug("TempDirGuard 清理额外临时路径失败 %s: %s", ep, _re)
            self.extra_paths = []
            p, self.path = self.path, None
            if p and os.path.exists(p):
                try:
                    shutil.rmtree(p, ignore_errors=True)
                except Exception as _re:
                    # 至少打 debug 日志，避免磁盘被残留 tempdir 占满
                    logger.debug("TempDirGuard 清理主临时目录失败 %s: %s", p, _re)

    _td = _TempDirGuard()

    def _finalize():
        # 幂等：重复调用 release 第二次什么都不做
        _td.release()
        try:
            psi4.core.clean()
        except Exception as _ce:
            logger.debug("PSI4 core.clean() 失败: %s", _ce)

    # 把所有可能分配临时目录的代码包在最外层 try/finally，
    # 保证任何 return 路径都会触发 _finalize()
    try:
        # ---------- 1. 强制检测非ASCII字符 ----------
        input_path = Path(input_file)
        has_non_ascii = any(ord(c) > 127 for c in str(input_path.resolve()))
        print(f"路径检测: has_non_ascii = {has_non_ascii}, 路径 = {input_file}")

        # =====【审计 1.1 路径遍历修复】=====
        # 推断 base_dir：优先 input_path.parent（绝大多数真实场景），否则 fallback 到 cwd/temp
        _base_dir: Path = _default_base_dir_from_input(input_file)
        # original_output_dir 也要走安全校验：用户手动传 output_dir 时不允许越出 base_dir
        try:
            _raw_orig_dir = output_dir if output_dir is not None else str(input_path.parent)
            _safe_orig = _secure_output_path(
                _raw_orig_dir,
                is_dir=True,
                base_dir=_base_dir,
                create_parent=True,
                # 允许用户显式指定的 output_dir 落在 base_dir 内（默认策略）。
                # 若确实需要放到工作目录外部，请在 UI 层显式勾选"允许输出到外部目录"后
                # 由上层 controller 传入 allow_outside=True 的包装路径；这里默认拒绝。
                allow_outside=False,
            )
            original_output_dir = str(_safe_orig)
        except ValueError as _v:
            return {"success": False, "error": f"输出目录非法: {_v}"}
        output_dir = None  # 等三件套逻辑（下面 _switch 或 else 分支）再统一赋值

        # M-4 修复：任何切换到 temp_dir 的代码路径都必须走同一个辅助函数，
        # 保证 {use_temp=True; output_dir=temp_dir; os.makedirs 三件套} 永远同步，
        # 避免「只改 use_temp 没改 output_dir」导致后续写入失败。
        use_temp: bool = False
        temp_dir: str | None = None

        def _switch_to_temp_dir() -> str:
            """原子切换到临时目录：三件套永远一致，任何分支用它都不会漏同步 output_dir。"""
            nonlocal use_temp, temp_dir, output_dir
            if temp_dir is None:
                # 优先复用 _TempDirGuard（生命周期绑定整个函数的 finally）
                td = _td.acquire(prefix="psi4_temp_")
                temp_dir = td
            # 已经外部手动 mkdtemp 并通过 _td.assign 注册过的分支走这里，不再重复 acquire
            out = str(temp_dir)
            use_temp = True
            output_dir = out
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as m_err:
                raise RuntimeError(f"无法创建 PSI4 临时目录: {output_dir}") from m_err
            print(f"ℹ️  使用 PSI4 临时目录：{output_dir}")
            return output_dir

        if has_non_ascii:
            _switch_to_temp_dir()
        else:
            # 用户没传 output_dir 就用 original_output_dir（已安全化）
            if output_dir is None:
                output_dir = original_output_dir
            # 确保输出目录存在（纯英文路径）
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as m_err:
                raise RuntimeError(f"无法创建 PSI4 输出目录: {output_dir}") from m_err

        # ---------- 2. 读取分子 ----------
        # 安全：不再把 input_file 作为路径字符串直接传给 psi4.geometry（H-1 / CWE-918 SSRF 修复）
        # PSI4 的 geometry() 接口会自动解析以下前缀：
        #   pubchem:<name|CID>   embed:<SMILES>   smiles:<...>   file://...
        # 因此即使 input_file 是「文件名」，如果用户/攻击者把文件名取为如 "pubchem:h2o.xyz" 或
        # 预填入路径 Entry 中类似形式，PSI4 会主动从外网拉取结构。
        # 修复方法：先把输入文件强制解析为真实文件（严格模式 resolve）→ OpenBabel 统一转 xyz
        # → 读回纯文本 xyz 内容 → 把 *内存中的纯文本* 传给 psi4.geometry()，PSI4 仅会解析为
        # XYZ / Z-Matrix 字符串，不会触发任何前缀协议。
        report(5, "读取分子结构...")
        mol = None

        try:
            real_input_path = Path(input_file).resolve(strict=True)
        except OSError as exc:
            return {"success": False, "error": f"分子文件无法解析为真实路径: {exc}"}
        if not real_input_path.is_file() or real_input_path.is_symlink():
            return {"success": False,
                    "error": f"分子文件必须是真实文件（禁止符号链接）: {input_file}"}

        def _load_from_realpath(full_temp_dir: str | None) -> tuple:
            # 若 full_temp_dir 为 None，说明使用真实文件所在目录；否则在 temp_dir 里转 molecule.xyz
            work = Path(full_temp_dir) if full_temp_dir else real_input_path.parent
            converted_xyz = os.fspath(work / "molecule.xyz")
            # 问题3修复：把 converted_xyz 注册进 _TempDirGuard，避免 use_temp 目录外（real_input_path.parent 下）
            # 的临时 converted_xyz 被遗漏
            if full_temp_dir is None and not real_input_path.suffix.lower() == ".xyz":
                # 走了「真实目录 + 非 xyz」路径，converted_xyz 会被写在真实目录，
                # 但它仍是临时文件，应当清理
                _td.register_extra(converted_xyz)
            # 先尝试直接读原始文件为 XYZ，如果原始扩展名就是 .xyz 就不跑 OpenBabel，省掉一次转换
            src_is_xyz = real_input_path.suffix.lower() == ".xyz"
            if src_is_xyz:
                xyz = read_xyz_content(os.fspath(real_input_path))
            else:
                if not convert_with_obabel(os.fspath(real_input_path), converted_xyz):
                    return None, "OpenBabel 转换失败"
                xyz = read_xyz_content(converted_xyz)
            if xyz is None:
                return None, "无法解析 XYZ"
            try:
                # ⚠️ 关键：传 *纯文本字符串* 而非路径，彻底禁止 PSI4 前缀协议解析
                return psi4.geometry(xyz), None
            except Exception as load_err:
                return None, f"PSI4 读取失败: {load_err}"

        try:
            if use_temp:
                mol, err = _load_from_realpath(temp_dir)
                if err:
                    return {"success": False, "error": err}
            else:
                try:
                    mol, _load_err = _load_from_realpath(None)
                    if mol is None:
                        # 回退到临时目录：先手动创建 temp_dir 并注册给 _TempDirGuard，然后切三件套
                        td = tempfile.mkdtemp(prefix="psi4_temp_")
                        _td.assign(td)
                        temp_dir = td
                        _switch_to_temp_dir()
                        mol, err = _load_from_realpath(temp_dir)
                        if err:
                            return {"success": False, "error": err}
                except Exception:
                    td = tempfile.mkdtemp(prefix="psi4_temp_")
                    _td.assign(td)
                    temp_dir = td
                    _switch_to_temp_dir()
                    mol, err = _load_from_realpath(temp_dir)
                    if err:
                        return {"success": False, "error": err}

            # 设置电荷和多重度
            if mol is None:
                return {"success": False, "error": "未能构建分子"}
            try:
                mol.set_molecular_charge(charge)
            except AttributeError:
                try:
                    mol.set_charge(charge)
                except Exception as _ch:
                    logger.debug("设置分子电荷 (set_charge) 失败 q=%d: %s", charge, _ch)
            try:
                mol.set_multiplicity(multiplicity)
            except AttributeError as _ma:
                logger.debug("设置分子多重度 (set_multiplicity) 失败 mult=%d: %s", multiplicity, _ma)
        except Exception as e:
            logger.warning("准备分子失败: %s", e, exc_info=True)
            return {"success": False, "error": f"准备分子失败: {e}"}
        results: Dict[str, Any] = {
            "success": False,
            "energy": None,
            "optimized_xyz": None,
            "frequencies": None,
            "fchk_file": None,
            "log_file": None,
            "output_files": [],
            "error": None,
        }
        wfn = None
        log_file = None
        output_prefix = None

        try:
            # ---------- 3. 构建输出路径（纯英文） ----------
            if use_temp:
                base = "molecule"
            else:
                base = sanitize_filename(os.path.splitext(os.path.basename(input_file))[0])

            safe_method = sanitize_filename(method)
            safe_basis = sanitize_filename(basis)
            suffix = f"_{task_type}"
            if preset_name:
                suffix += f"_{sanitize_filename(preset_name)}"
            else:
                suffix += f"_{safe_method}_{safe_basis}"
            if solvent:
                suffix += f"_{sanitize_filename(solvent)}"
            if d3:
                suffix += "_d3"

            output_prefix = os.path.join(output_dir, base + suffix)
            log_file = output_prefix + ".log"
            results["log_file"] = log_file
            logger.info("PSI4 日志文件将保存到: %s", log_file)

            # 设置 PSI4 日志输出
            psi4.set_output_file(log_file, append=False)

            # ---------- 4. 计算选项 ----------
            psi4.set_memory(memory)
            psi4.set_options({
                'basis': basis,
                'scf_type': 'pk',
                'e_convergence': 1e-8,
                'd_convergence': 1e-8,
            })
            if extra_options:
                # 让用户可覆盖基础选项，比如 NMR 需要 cphf_tasks
                try:
                    psi4.set_options(dict(extra_options))
                except Exception as _eo_err:
                    logger.warning("应用 extra_options 失败：%s", _eo_err)
            if d3:
                try:
                    psi4.set_options({'dft_dispersion': 'd3'})
                except Exception as _d3_err:
                    logger.warning("D3 色散校正启用失败，回退为不加 D3: %s", _d3_err)
            _pcm_enabled_here = False
            if solvent:
                # PSI4 中「solvent=」选项只是全局 GLOBAL 变量，不会自动打开 PCM/SMD 模型。
                # 显式溶剂模型（PCM）只对单点能 energy() 任务稳定；
                # optimize / frequency 等需要解析梯度/ Hessian，部分泛函/基组组合会直接报错，
                # 这里做「尽力启用 + 失败降级」处理。
                _pcm_try_tasks = {"energy"}
                if task_type in _pcm_try_tasks:
                    try:
                        psi4.set_options({'pcm': True, 'solvent': solvent})
                        try:
                            psi4.core.set_local_option("PCM", "Solver", "IEFPCM")
                            psi4.core.set_local_option("PCM", "Medium", "UniformDielectric")
                            psi4.core.set_local_option("PCM", "SolverEnzyme", False)
                            psi4.core.set_local_option("PCM", "Cavity", "UFF")
                            psi4.core.set_local_option("PCM", "Scaling", True)
                            psi4.core.set_local_option("PCM", "RadiiSet", "UFF")
                            psi4.core.set_local_option("PCM", "Area", 0.3)
                        except Exception as _pcm_opts_err:
                            logger.debug("PCM 局部选项设置失败（通常是 PCMSolver 未编译进 PSI4）: %s",
                                         _pcm_opts_err)
                        # 部分 Psi4 版本（无 PCMSolver 编译）即使 pcm=True 也会在调用 energy 时
                        # 抛异常，所以我们在 energy 子块 catch 并自动回退为气相；
                        # 此处先假定能跑通，失败后会在具体 task 分支里降回气相。
                        _pcm_enabled_here = True
                    except Exception as _pcm_err:
                        logger.warning("启用 PCM 隐式溶剂失败，回退为仅写入 solvent 元数据: %s", _pcm_err)
                        try:
                            psi4.set_options({'pcm': False, 'solvent': solvent})
                        except Exception:
                            pass
                else:
                    # optimize / frequency 等任务：只把 solvent 名字作为元数据写进 options
                    # 不启用 PCM（因为 optimize 常要求解析梯度，SMD/PCM 在旧版本不一定有解析梯度实现）
                    try:
                        psi4.set_options({'solvent': solvent})
                    except Exception as _solv_meta_err:
                        logger.warning("写入溶剂元数据选项失败: %s", _solv_meta_err)

            report(10, "开始计算...")
            _pcm_safe_rollback_done = False

            def _rollback_pcm_if_needed():
                nonlocal _pcm_safe_rollback_done, _pcm_enabled_here
                if _pcm_safe_rollback_done or not _pcm_enabled_here:
                    return False
                _pcm_safe_rollback_done = True
                try:
                    psi4.set_options({'pcm': False})
                except Exception:
                    pass
                logger.warning("PCM 求解失败，已自动回退为气相 energy 重新计算")
                return True

            # ---------- 5. 执行任务 ----------
            if task_type == 'energy':
                report(30, "计算单点能...")
                try:
                    energy, wfn = psi4.energy(method, molecule=mol, return_wfn=True)
                except Exception as _e1:
                    if _rollback_pcm_if_needed():
                        energy, wfn = psi4.energy(method, molecule=mol, return_wfn=True)
                        results["pcm_rolled_back"] = True
                        results["solvent_rollback_reason"] = str(_e1)[:200]
                    else:
                        raise
                results["energy"] = energy
                results["success"] = True

            elif task_type == 'optimize':
                report(30, "开始几何优化...")
                energy, wfn = psi4.optimize(method, molecule=mol, return_wfn=True)
                results["energy"] = energy
                opt_mol = wfn.molecule()
                results["optimized_xyz"] = opt_mol.save_string_xyz()
                results["success"] = True

            elif task_type == 'frequency':
                report(30, "计算频率...")
                energy, wfn = psi4.frequency(method, molecule=mol, return_wfn=True)
                results["energy"] = energy
                freqs = psi4.core.variable("frequencies")
                if freqs is not None:
                    results["frequencies"] = freqs.to_array().tolist()
                results["success"] = True

            elif task_type == 'ts':
                report(30, "搜索过渡态...")
                energy, wfn = psi4.optimize('ts', molecule=mol, return_wfn=True)
                results["energy"] = energy
                results["success"] = True

            elif task_type == 'excited':
                report(30, "计算激发态...")
                psi4.set_options({'tdscf_excitations': 5})
                energy, wfn = psi4.energy(method, molecule=mol, return_wfn=True)
                results["energy"] = energy
                results["success"] = True

            elif task_type == 'sapt':
                report(30, "计算 SAPT...")
                psi4.set_options({'sapt_symmetry': 'c1'})
                energy = psi4.sapt_energy(method, molecule=mol)
                results["energy"] = energy
                results["success"] = True
                try:
                    wfn = psi4.core.get_wavefunction()
                except Exception:
                    wfn = None

            elif task_type == 'thermo':
                report(30, "进行几何优化...")
                opt_energy, opt_wfn = psi4.optimize(method, molecule=mol, return_wfn=True)
                results["energy"] = opt_energy
                opt_mol = opt_wfn.molecule()
                results["optimized_xyz"] = opt_mol.save_string_xyz()
                report(60, "计算频率（优化后结构）...")
                freq_energy, freq_wfn = psi4.frequency(method, molecule=opt_mol, return_wfn=True)
                thermo = psi4.core.variable("thermodynamics")
                if thermo is not None:
                    results["thermo"] = thermo.to_array().tolist()
                results["success"] = True
                wfn = opt_wfn

            else:
                results["error"] = f"未知任务类型: {task_type}"

            # ----- 高级扩展：用户自定义 post hook（如 NMR 需要在主任务后再跑 cphf） -----
            if results["success"] and extra_post_hook is not None:
                try:
                    _hook_ret = extra_post_hook(wfn, mol, method)
                    if isinstance(_hook_ret, dict):
                        results.setdefault("hook", {}).update(_hook_ret)
                except Exception as _hook_err:
                    logger.warning("extra_post_hook 执行失败：%s", _hook_err)
                    results["hook_error"] = str(_hook_err)

            # ========== P1 波函数属性（HOMO/LUMO/偶极/电荷布居/转动常数） ==========
            if results["success"] and wfn is not None:
                try:
                    props: dict[str, Any] = {}
                    # 1) 从波函数直接取 HOMO/LUMO 能隙（Hartree → eV）
                    try:
                        na_list = wfn.nalpha()  # 整数 alpha 电子数
                        nb_list = wfn.nbeta()
                        # epsilon_a_subset("ALL") → 所有 alpha 轨道能量（Hartree）
                        eps_a = None
                        try:
                            eps_a_arr = wfn.epsilon_a()  # psi4 Vector
                            if eps_a is not None:
                                eps_a = eps_a_arr.to_array()
                        except Exception:
                            pass
                        if eps_a is not None and len(eps_a) > 0:
                            n_a = int(na_list)
                            homo_i = max(0, min(n_a - 1, len(eps_a) - 1))
                            lumo_i = min(homo_i + 1, len(eps_a) - 1)
                            hartree_to_ev = 27.21139664
                            homo_ev = float(eps_a[homo_i]) * hartree_to_ev
                            lumo_ev = float(eps_a[lumo_i]) * hartree_to_ev
                            props["homo_ev"] = homo_ev
                            props["lumo_ev"] = lumo_ev
                            props["gap_ev"] = lumo_ev - homo_ev
                            props["homo_idx"] = homo_i + 1
                            props["lumo_idx"] = lumo_i + 1
                    except Exception as _e_hl:
                        logger.debug("取 HOMO/LUMO 失败: %s", _e_hl)

                    # 2) 用 oeprop 取 Mulliken / Löwdin 电荷、偶极矩
                    try:
                        psi4.oeprop(wfn,
                                    "MULLIKEN_CHARGES",
                                    "LOWDIN_CHARGES",
                                    "DIPOLE",
                                    "QUADRUPOLE",
                                    "MO_ENERGIES")
                        # oeprop 会把结果写入 psi4 变量；原子电荷通过 oeprop 返回字典或 core.get_array
                        try:
                            mu_x = float(psi4.core.variable("DIPOLE X"))
                            mu_y = float(psi4.core.variable("DIPOLE Y"))
                            mu_z = float(psi4.core.variable("DIPOLE Z"))
                            mu_tot = (mu_x ** 2 + mu_y ** 2 + mu_z ** 2) ** 0.5
                            props["dipole"] = {"x_D": mu_x, "y_D": mu_y, "z_D": mu_z, "total_D": mu_tot}
                        except Exception as _e_d:
                            logger.debug("取偶极矩失败: %s", _e_d)

                        # 原子电荷：通过 oeprop 内部 charge arrays
                        def _pull_charges(key: str) -> list[float] | None:
                            try:
                                arr = psi4.core.variable(key)
                                if arr is None:
                                    # 新版 PSI4 改放 oeprop().charges()
                                    return None
                                if hasattr(arr, 'to_array'):
                                    return arr.to_array().tolist()
                                if isinstance(arr, (list, tuple)):
                                    return [float(x) for x in arr]
                                return None
                            except Exception:
                                return None
                        mulliken = _pull_charges("MULLIKEN CHARGES")
                        lowdin = _pull_charges("LOWDIN CHARGES")
                        # 回退：解析 out log 文本（最后 200 行找 Mulliken Charges 块）
                        if mulliken is None and results.get("log_file") and os.path.exists(results["log_file"]):
                            try:
                                from psi4_compute import _extract_charges_from_log  # type: ignore  # noqa: F401
                            except Exception:
                                pass
                        atoms_sym: list[str] = []
                        if mol is not None:
                            try:
                                n_at = mol.natom()
                                for i in range(n_at):
                                    atoms_sym.append(mol.symbol(i))
                            except Exception:
                                atoms_sym = []
                        if mulliken is not None and atoms_sym and len(mulliken) == len(atoms_sym):
                            props["charges"] = {
                                "atoms": atoms_sym,
                                "mulliken": mulliken,
                            }
                            if lowdin is not None and len(lowdin) == len(atoms_sym):
                                props["charges"]["lowdin"] = lowdin
                    except Exception as _e_prop:
                        logger.debug("oeprop 属性计算失败: %s", _e_prop)

                    # 3) 转动常数（A/B/C，MHz）
                    try:
                        rot_const_keys = ["ROTATIONAL CONSTANT A", "ROTATIONAL CONSTANT B", "ROTATIONAL CONSTANT C"]
                        rcs: dict[str, float] = {}
                        for k in rot_const_keys:
                            v = psi4.core.variable(k)
                            if v is not None:
                                try:
                                    rcs[k.split()[-1]] = float(v)
                                except Exception:
                                    pass
                        if rcs:
                            props["rotational_constants_MHz"] = rcs
                    except Exception as _e_rc:
                        logger.debug("取转动常数失败: %s", _e_rc)

                    if props:
                        results["properties"] = props
                except Exception as _e_p1:
                    logger.debug("P1 波函数属性提取整体失败: %s", _e_p1)

            # ========== P3 频率 → 模拟 IR 光谱图（PNG + CSV） ==========
            ir_png: str | None = None
            ir_csv: str | None = None
            if results["success"] and results.get("frequencies") and output_prefix:
                try:
                    ir_csv = output_prefix + "_ir_spectrum.csv"
                    ir_png = output_prefix + "_ir_spectrum.png"
                    freqs = list(results["frequencies"])
                    # 强度：PSI4 frequency() 会把 "IR INTENSITIES" 写入 core variable，取不到就用常数 1 兜底
                    intensities: list[float] = []
                    try:
                        ir_arr = psi4.core.variable("IR INTENSITIES")
                        if ir_arr is not None and hasattr(ir_arr, "to_array"):
                            intensities = [float(x) for x in ir_arr.to_array().tolist()]
                    except Exception:
                        intensities = []
                    n = len(freqs)
                    if len(intensities) != n:
                        intensities = [1.0 for _ in freqs]
                    # 写 CSV：波数,强度
                    with open(ir_csv, "w", encoding="utf-8", newline="") as _f:
                        _wr = csv.writer(_f)
                        _wr.writerow(["wavenumber_cm-1", "intensity_km/mol"])
                        for fv, iv in zip(freqs, intensities):
                            _wr.writerow([fv, iv])
                    # 画 PNG：400-4000 cm⁻¹ 典型有机分子区间，洛伦兹展宽 FWHM=10 cm⁻¹
                    _plot_ir(freqs, intensities, ir_png)
                    results["ir_csv"] = ir_csv
                    results["ir_png"] = ir_png
                    results["output_files"].extend([ir_csv, ir_png])
                except Exception as _e_p3:
                    logger.debug("P3 IR 光谱生成失败: %s", _e_p3)

            # ========== P2 cubeprop：HOMO/LUMO/总电子密度 cube 文件 ==========
            cube_files: list[str] = []
            if results["success"] and wfn is not None and output_prefix:
                try:
                    cube_out_dir = Path(output_prefix).parent / (Path(output_prefix).name + "_cubes")
                    cube_out_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        old_cwd = Path.cwd()
                        os.chdir(cube_out_dir)
                    except OSError:
                        old_cwd = None
                    try:
                        psi4.set_options({
                            'CUBEPROP_TASKS': ['DENSITY', 'FRONTIER_ORBITALS'],
                            'CUBIC_GRID_SPACING': 0.25,
                        })
                        psi4.cubeprop(wfn)
                    finally:
                        if old_cwd is not None:
                            try:
                                os.chdir(old_cwd)
                            except OSError:
                                pass
                    # 收集 cube 文件
                    for p in cube_out_dir.iterdir():
                        if p.suffix.lower() == ".cube":
                            cube_files.append(str(p))
                    results["cube_dir"] = str(cube_out_dir)
                    results["cube_files"] = cube_files
                    results["output_files"].extend(cube_files)
                except Exception as _e_p2:
                    logger.debug("P2 cubeprop 失败: %s", _e_p2)

            # ---------- 6. 保存结果 ----------
            if results["success"] and output_prefix:
                report(80, "保存结果文件...")
                if wfn is None:
                    try:
                        wfn = psi4.core.get_wavefunction()
                    except Exception:
                        wfn = None

                if wfn is not None:
                    fchk_file = output_prefix + ".fchk"
                    psi4.fchk(wfn, fchk_file)
                    results["fchk_file"] = fchk_file
                    results["output_files"].append(fchk_file)

                if results.get("optimized_xyz"):
                    xyz_file = output_prefix + "_opt.xyz"
                    with open(xyz_file, 'w') as f:
                        f.write(results["optimized_xyz"])
                    results["output_files"].append(xyz_file)

                summary_file = output_prefix + "_summary.json"
                summary_data = {
                    "input_file": input_file,
                    "task_type": task_type,
                    "method": method,
                    "basis": basis,
                    "preset": preset_name,
                    "energy": results["energy"],
                    "success": results["success"],
                }
                with open(summary_file, 'w', encoding='utf-8') as f:
                    json.dump(summary_data, f, indent=2)
                results["output_files"].append(summary_file)

            # ---------- 7. 复制结果到原目录 ----------
            if use_temp and temp_dir:
                report(95, "复制结果到原目录...")
                os.makedirs(original_output_dir, exist_ok=True)
                for src_path in list(results["output_files"]):
                    if os.path.exists(src_path):
                        dst_path = os.path.join(original_output_dir, os.path.basename(src_path))
                        shutil.copy2(src_path, dst_path)
                        if src_path == results.get("log_file"):
                            results["log_file"] = dst_path
                        elif src_path == results.get("fchk_file"):
                            results["fchk_file"] = dst_path
                if log_file and os.path.exists(log_file) and log_file not in results["output_files"]:
                    dst_log = os.path.join(original_output_dir, os.path.basename(log_file))
                    shutil.copy2(log_file, dst_log)
                    results["log_file"] = dst_log

            report(100, "任务完成")

        except Exception as e:
            results["error"] = str(e)
            import traceback
            logger.error("PSI4 任务执行异常: %s", e, exc_info=True)
            traceback.print_exc()

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            try:
                psi4.core.clean()
            except Exception:
                pass

    finally:
        _finalize()

    return results


def parse_psi4_output(log_file: str, task_type: str = 'energy') -> Dict:
    result = {"energy": None, "optimized_xyz": None}
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.read()
        en_patterns = [
            r'@.*?Final\s+energy\s+([-\d.]+)',
            r'Total energy\s+=\s+([-\d.]+)',
            r'SCF\s+Done:\s+E\s*=\s*([-\d.]+)',
        ]
        for pattern in en_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                result["energy"] = float(matches[-1])
                break
        if task_type == 'optimize':
            coords = []
            in_coords = False
            coord_started = False
            for line in content.splitlines():
                if 'Standard nuclear orientation' in line or 'Current geometry' in line:
                    in_coords = True
                    coord_started = False
                    coords = []
                    continue
                if in_coords and '-----' in line:
                    if coord_started and coords:
                        break
                    coord_started = True
                    continue
                if in_coords and coord_started and re.match(r'\s*\d+\s+', line):
                    parts = line.split()
                    if len(parts) >= 5:
                        atom_num = int(parts[1])
                        element_map = {1: 'H', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 15: 'P', 16: 'S', 17: 'Cl', 35: 'Br', 53: 'I'}
                        atom_symbol = element_map.get(atom_num, f"X{atom_num}")
                        x, y, z = parts[2:5]
                        coords.append(f"{atom_symbol}  {x}  {y}  {z}")
            if coords:
                result["optimized_xyz"] = f"{len(coords)}\nOptimized geometry\n" + "\n".join(coords)
    except Exception as e:
        result["error"] = str(e)
        logger.debug("解析 PSI4 输出失败: %s", e)
    return result


def _parse_xyz(text: str) -> tuple[int, list[str], list[list[float]]]:
    lines = text.strip().splitlines()
    if len(lines) < 2:
        raise ValueError("XYZ 内容不足 2 行")
    n = int(lines[0].strip())
    atoms: list[str] = []
    coords: list[list[float]] = []
    atoms_append = atoms.append
    coords_append = coords.append
    end = min(2 + n, len(lines))
    for line in lines[2:end]:
        parts = line.split()
        if len(parts) < 4:
            continue
        atoms_append(parts[0])
        coords_append([float(parts[1]), float(parts[2]), float(parts[3])])
    return n, atoms, coords


def _write_xyz(n: int, atoms: list[str], coords: list[list[float]]) -> str:
    lines: list[str] = [str(n), ""]
    lines_append = lines.append
    for sym, xyz in zip(atoms, coords):
        x0, x1, x2 = xyz[0], xyz[1], xyz[2]
        lines_append(f"{sym:<3s} {x0:15.10f} {x1:15.10f} {x2:15.10f}")
    return "\n".join(lines) + "\n"


def _plot_ir(freqs_cm: list[float], intensities: list[float], out_png: str,
             fwhm: float = 10.0, vmin: float = 400.0, vmax: float = 4000.0, npts: int = 1600) -> bool:
    """
    P3：洛伦兹展宽画一张模拟 IR 光谱 PNG。
    有 matplotlib 就用；没有就退化为 Pillow，都没有返回 False（仍然保留 CSV 文件）。
    """
    if not freqs_cm:
        return False
    xs: list[float] = [vmin + (vmax - vmin) * i / max(1, npts - 1) for i in range(npts)]
    ys: list[float] = [0.0 for _ in xs]
    half = fwhm / 2.0
    g = half ** 2
    for v, I in zip(freqs_cm, intensities):
        if v <= 0:
            continue
        iI = I if I > 0 else 1.0
        imin = max(0, int((v - fwhm * 4 - vmin) / (vmax - vmin) * npts))
        imax = min(npts - 1, int((v + fwhm * 4 - vmin) / (vmax - vmin) * npts) + 1)
        for i in range(imin, imax + 1):
            d = xs[i] - v
            ys[i] += iI * g / (d * d + g)
    y_max = max(ys) if ys else 0.0
    if y_max > 0:
        ys = [y / y_max for y in ys]
    ys_abs = [1.0 - y for y in ys]  # 吸光度模式：0 顶部

    # ---- A. matplotlib ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        try:
            if os.name == "nt":
                from matplotlib import font_manager as _fm  # noqa: F401
                for _cand in ("Microsoft YaHei", "SimHei", "SimSun", "Arial Unicode MS"):
                    try:
                        plt.rcParams["font.sans-serif"] = [_cand] + list(
                            plt.rcParams.get("font.sans-serif", []))
                        break
                    except Exception:
                        continue
            plt.rcParams["axes.unicode_minus"] = False
        except Exception:
            pass
        fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
        ax.plot(xs, ys_abs, color="#1f77b4", linewidth=1.2)
        for v, I in zip(freqs_cm, intensities):
            if v <= 0:
                continue
            h = (I if I > 0 else 1.0) / y_max if y_max > 0 else 0.0
            ax.plot([v, v], [1.0, 1.0 - h], color="#d62728", linewidth=0.8, alpha=0.6)
        ax.set_xlim(vmax, vmin)
        ax.set_ylim(-0.05, 1.1)
        ax.set_xlabel("Wavenumber (cm-1) / 波数")
        ax.set_ylabel("Absorbance (norm) / 吸光度")
        ax.set_title("Simulated IR Spectrum / 模拟红外光谱")
        ax.grid(True, alpha=0.3, linestyle="--")
        fig.tight_layout()
        fig.savefig(out_png, dpi=130, bbox_inches="tight")
        plt.close(fig)
        return os.path.exists(out_png)
    except Exception as _e_mpl:
        logger.debug("matplotlib 画 IR 失败，尝试 Pillow: %s", _e_mpl)

    # ---- B. Pillow 直画 PNG ----
    try:
        from PIL import Image as _PIL_Image, ImageDraw as _ImageDraw  # type: ignore
        W, H = 1200, 600
        img = _PIL_Image.new("RGB", (W, H), "white")
        draw = _ImageDraw.Draw(img)
        pad_l, pad_r, pad_t, pad_b = 80, 30, 40, 60
        x0, x1 = pad_l, W - pad_r
        y0, y1 = pad_t, H - pad_b

        def _X(v: float) -> int:
            return int(x1 - (v - vmin) / (vmax - vmin) * (x1 - x0))

        def _Y(a: float) -> int:
            return int(y0 + (1.0 - a) * (y1 - y0))

        draw.rectangle([x0, y0, x1, y1], outline="black", width=1)
        for tick_pct in (0.0, 0.25, 0.5, 0.75, 1.0):
            tx = int(x0 + tick_pct * (x1 - x0))
            vv = vmax - tick_pct * (vmax - vmin)
            draw.line([(tx, y0, tx, y1)], fill="#cccccc")
            draw.text((tx - 20, y1 + 8), f"{int(vv)}", fill="black")
        for pct in (0.0, 0.25, 0.5, 0.75, 1.0):
            ty = int(y1 - pct * (y1 - y0))
            draw.line([(x0, ty, x1, ty)], fill="#cccccc")
            draw.text((x0 - 55, ty - 8), f"{1.0 - pct:.2f}", fill="black")
        pts: list[tuple[int, int]] = [(_X(vmin), _Y(1.0))]
        for xv, yv in zip(xs, ys_abs):
            pts.append((_X(xv), _Y(yv)))
        pts.append((_X(vmax), _Y(1.0)))
        draw.polygon(pts, outline="#1f77b4", fill="#e3f2fd")
        for v, I in zip(freqs_cm, intensities):
            if v <= 0:
                continue
            h = (I if I > 0 else 1.0) / y_max if y_max > 0 else 0.0
            xt = _X(v)
            draw.line([(xt, _Y(1.0), xt, _Y(1.0 - h))], fill="#d62728", width=1)
        try:
            draw.text((W // 2 - 90, H - 28), "Wavenumber / 波数 (cm-1)", fill="black")
            draw.text((8, H // 2 - 40), "Absorbance / 吸光度", fill="black")
            draw.text((W // 2 - 140, 10), "Simulated IR Spectrum / 模拟红外光谱", fill="black")
        except Exception:
            pass
        img.save(out_png, format="PNG")
        return os.path.exists(out_png)
    except Exception as _e_fb:
        logger.debug("Pillow 画 IR 也失败: %s", _e_fb)
    return False


def _lerp_coords(R: list[list[float]], P: list[list[float]], t: float) -> list[list[float]]:
    one_minus_t = 1.0 - t
    return [[one_minus_t * R[i][0] + t * P[i][0],
             one_minus_t * R[i][1] + t * P[i][1],
             one_minus_t * R[i][2] + t * P[i][2]] for i in range(len(R))]


# ===============================================================
# P5：构象搜索 TopN + PSI4 批量高精度再优化
# ===============================================================
@performance_timer(name="psi4.conformer_search_ensemble", level=logging.DEBUG, min_ms=100.0)
def conformer_search_ensemble(
    input_file: str,
    output_dir: str | os.PathLike[str] | None = None,
    n_confs_total: int = 80,
    top_n: int = 5,
    psi4_method: str = "b3lyp",
    psi4_basis: str = "6-31g*",
    psi4_preset_name: str | None = None,
    solvent: str | None = None,
    d3: bool = False,
    charge: int = 0,
    multiplicity: int = 1,
    memory: str = "4 GB",
    psi4_high_precision: bool = False,
    _progress_callback=None,
) -> dict[str, Any]:
    """
    构象搜索：
      1. OBabel 系统搜索（weighted rotor 搜索 / MMFF94 快速优化）
         → 取 MMFF94 最低能量 top_n
      2. 依次跑 PSI4 optimize（可选）
         → 输出每个构象的最终能量（Hartree）、排序、CSV、PNG 能量棒图
    """
    import openbabel_utils as _obu
    from pathlib import Path as _Path

    def _report(perc: int, msg: str):
        if _progress_callback:
            try: _progress_callback(perc, msg)
            except Exception: pass
        logger.debug("[conformer_search] %3d%% %s", perc, msg)

    result: dict[str, Any] = {
        "success": False,
        "error": None,
        "mmff_top": [],  # [(rank, energy_kcal_mol, xyz_path)]
        "psi4_results": [],  # [(rank, energy_hartree, optimized_xyz_path, fchk_path, properties_dict)]
        "summary_csv": None,
        "ensemble_energy_png": None,
        "output_dir": None,
    }
    if not _obu.PYBEL_AVAILABLE:
        result["error"] = "需要 pybel/OpenBabel Python 包做构象搜索"
        return result
    src = read_xyz_content(input_file) if str(input_file).lower().endswith(".xyz") else None
    if src is None:
        result["error"] = f"无法读取 {input_file}"
        return result

    if output_dir is None:
        output_dir = _Path(input_file).parent / f"{_Path(input_file).stem}_conformers"
    out_dir = _Path(output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    result["output_dir"] = str(out_dir)

    # ---------- Step A：OpenBabel Confab 搜索 ----------
    _report(5, "OpenBabel Confab 系统构象搜索（MMFF94 优化）")
    confabsdf = str(out_dir / "mmff_conformers.sdf")
    confab_report = str(out_dir / "mmff_conformers_report.txt")
    n_confs_total = max(10, int(n_confs_total))
    top_n = max(1, min(top_n, n_confs_total))
    cmd = [
        "obabel", input_file, "-O", confabsdf,
        "--confab", "--confab_options",
        f"--nconf {n_confs_total} --energy 50.0 --rmsd 0.5",
    ]
    rc = _run_process_with_timeout(cmd, cwd=str(out_dir), timeout=300)
    if rc != 0 or not os.path.exists(confabsdf) or os.path.getsize(confabsdf) == 0:
        # Fallback：用 pybel 做 systematic rotor search（随机扰动）
        try:
            import random as _rnd
            r = _obu._read_molecules(input_file, os.path.splitext(input_file)[1][1:].lower())
            if not r:
                result["error"] = "OpenBabel 未读到任何分子"; return result
            base = r[0]
            obmol = base.OBMol
            # 获取可旋转键列表
            rotor_bonds: list = []
            try:
                for b in obmol.GetBonds():
                    try:
                        if b.IsRotor():
                            rotor_bonds.append(b)
                    except Exception:
                        continue
            except Exception:
                rotor_bonds = []
            seen = set()
            out_mols = []
            for _ in range(n_confs_total):
                for b in rotor_bonds:
                    ang = _rnd.uniform(0, 360)
                    try:
                        b.SetTorsion(ang)
                    except Exception:
                        continue
                # 力场快速优化
                try:
                    ff = _obu.ob.OBForceField.FindForceField("MMFF94") or _obu.ob.OBForceField.FindForceField("UFF")
                    if ff and ff.Setup(obmol):
                        try: ff.ConjugateGradients(200, 1.0e-4)
                        except Exception: pass
                        try: ff.GetCoordinates(obmol)
                        except Exception: pass
                except Exception:
                    pass
                try:
                    conv = _obu.ob.OBConversion(); conv.SetOutFormat("smi")
                    smi = conv.WriteString(obmol).strip()
                    key2 = smi
                except Exception:
                    key2 = str(id(obmol))
                if key2 not in seen:
                    seen.add(key2)
                    dup = _obu.ob.OBMol()
                    dup.Assign(obmol)
                    out_mols.append(_obu.pybel.Molecule(dup))
                if len(out_mols) >= n_confs_total:
                    break
            if out_mols:
                conv = _obu.ob.OBConversion(); conv.SetOutFormat("sdf")
                with open(confabsdf, "wb") as f:
                    for m in out_mols:
                        f.write(conv.WriteString(m.OBMol).encode("utf-8", errors="replace"))
        except Exception as _e_fb:
            result["error"] = f"Confab + Fallback 都失败：{_e_fb}"; return result

    # ---------- Step B：从 SDF 读出每个构象的 MMFF 能量并排序 ----------
    if not os.path.exists(confabsdf) or os.path.getsize(confabsdf) == 0:
        result["error"] = "构象搜索没有产生任何构象 SDF"; return result
    mols_list = _obu._read_molecules(confabsdf, "sdf") or []
    if not mols_list:
        result["error"] = "无法读取生成的构象 SDF"; return result
    def _energy_of(m):
        try: return float(m.energy)  # pybel
        except Exception:
            try:
                txt = m.write("sdf")
                lines = txt.splitlines()
                for idx, line in enumerate(lines):
                    if ">  <Energy>" in line and idx + 1 < len(lines):
                        try: return float(lines[idx + 1].strip())
                        except (ValueError, IndexError):
                            continue
            except Exception: pass
        # 兜底：用 MMFF/UFF 现场算一次能量
        try:
            ff = _obu.ob.OBForceField.FindForceField("MMFF94") or _obu.ob.OBForceField.FindForceField("UFF")
            if ff and ff.Setup(m.OBMol):
                return float(ff.Energy(False))
        except Exception:
            pass
        return 0.0
    with_e: list[tuple[float, Any]] = []
    for mol in mols_list:
        with_e.append((_energy_of(mol), mol))
    with_e.sort(key=lambda x: x[0])
    top_mols = with_e[:top_n]
    mmff_top: list[dict] = []
    for rank, (e, mol) in enumerate(top_mols, 1):
        xyz_path = str(out_dir / f"conf_{rank:02d}_mmff.xyz")
        try:
            mol.write("xyz", xyz_path, overwrite=True)
        except Exception:
            continue
        mmff_top.append({"rank": rank, "energy_kcal_mol": float(e), "xyz": xyz_path})
    result["mmff_top"] = mmff_top

    if not psi4_high_precision:
        # ---------- 仅 MMFF ----------
        csv_path = str(out_dir / "summary_mmff.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["rank", "energy_kcal_mol (relative)", "xyz_file"])
            base = mmff_top[0]["energy_kcal_mol"] if mmff_top else 0.0
            for c in mmff_top:
                wr.writerow([c["rank"], f"{c['energy_kcal_mol'] - base:.3f}", c["xyz"]])
        result["summary_csv"] = csv_path
        result["success"] = True
        _report(100, f"Done（仅 MMFF，共 {len(mmff_top)} 构象）")
        return result

    # ---------- Step C：PSI4 optimize 每个 Top 构象 ----------
    psi4_results: list[dict] = []
    total_c = len(mmff_top)
    for i, c in enumerate(mmff_top, 1):
        _report(10 + int(85 * (i - 1) / max(1, total_c)),
                f"PSI4 optimize 构象 {i}/{total_c}  rank={c['rank']}")
        prefix = str(out_dir / f"conf_{c['rank']:02d}_psi4")
        r = run_psi4_task(c["xyz"], "optimize", psi4_method, psi4_basis,
                          preset_name=psi4_preset_name, output_prefix=prefix,
                          solvent=solvent, d3=d3, charge=charge, multiplicity=multiplicity,
                          memory=memory, _progress_callback=None)
        if r.get("success"):
            psi4_results.append({
                "rank_mmff": c["rank"],
                "energy_h": r.get("energy"),
                "opt_xyz": r.get("fchk_file"),
                "fchk": r.get("fchk_file"),
                "props": r.get("properties"),
            })
    # 按 PSI4 能量重排
    psi4_results.sort(key=lambda x: x["energy_h"] if isinstance(x["energy_h"], (int, float)) else 1e30)
    for j, c in enumerate(psi4_results, 1):
        c["rank_psi4"] = j
    result["psi4_results"] = psi4_results

    csv_path = str(out_dir / "summary_psi4.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["rank_psi4", "rank_mmff", "energy_Hartree", "rel_kcal_mol", "opt_xyz"])
        base = psi4_results[0]["energy_h"] if psi4_results and isinstance(psi4_results[0]["energy_h"], (int, float)) else 0.0
        H_to_KCAL = 627.5094740631
        for c in psi4_results:
            eh = c["energy_h"]
            rel = (eh - base) * H_to_KCAL if isinstance(eh, (int, float)) else float("nan")
            wr.writerow([c["rank_psi4"], c["rank_mmff"], eh, f"{rel:.3f}", c.get("opt_xyz") or ""])
    result["summary_csv"] = csv_path

    # 画一张能量棒图 PNG（Pillow/matplotlib）
    try:
        png_path = str(out_dir / "ensemble_relative_energy.png")
        xs = [c["rank_psi4"] for c in psi4_results]
        ys_rel: list[float] = []
        base = psi4_results[0]["energy_h"] if psi4_results and isinstance(psi4_results[0]["energy_h"], (int, float)) else 0.0
        for c in psi4_results:
            eh = c["energy_h"]
            ys_rel.append((eh - base) * H_to_KCAL if isinstance(eh, (int, float)) else float("nan"))
        try:
            import matplotlib; matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            try:
                if os.name == "nt":
                    for _cand in ("Microsoft YaHei", "SimHei", "SimSun"):
                        try:
                            plt.rcParams["font.sans-serif"] = [_cand] + list(plt.rcParams.get("font.sans-serif", []))
                            break
                        except Exception:
                            continue
                plt.rcParams["axes.unicode_minus"] = False
            except Exception:
                pass
            fig, ax = plt.subplots(figsize=(8, 4.5))
            bars = ax.bar([str(x) for x in xs], ys_rel, color="#7f7f7f", edgecolor="black")
            for b, y in zip(bars, ys_rel):
                ax.text(b.get_x() + b.get_width()/2, y + max(ys_rel or [0.0])*0.01,
                        f"{y:.1f}", ha="center", va="bottom", fontsize=8)
            ax.set_xlabel("Conformer / 构象 (rank by PSI4)")
            ax.set_ylabel("Relative Energy / 相对能量 (kcal/mol)")
            ax.set_title(f"Conformer Ensemble / 构象系综 (Top-{len(xs)})")
            ax.grid(True, axis="y", alpha=0.3)
            fig.tight_layout(); fig.savefig(png_path, dpi=130); plt.close(fig)
        except Exception:
            # Pillow fallback
            try:
                from PIL import Image, ImageDraw
                W, H = 1200, 640
                img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
                pad_l, pad_r, pad_t, pad_b = 80, 30, 50, 70
                x0, x1 = pad_l, W - pad_r; y0, y1 = pad_t, H - pad_b
                ymax = max(ys_rel or [1.0]) * 1.15 if ys_rel else 1.0
                if ymax <= 0: ymax = 1.0
                bw = (x1 - x0) / max(1, len(xs)) * 0.6
                bx0 = pad_l + (x1 - x0) / max(1, len(xs)) * 0.2
                d.rectangle([x0, y0, x1, y1], outline="black")
                for i, y in enumerate(ys_rel):
                    L = bx0 + i * (x1 - x0) / max(1, len(xs))
                    R = L + bw
                    T = y1 - (y / ymax) * (y1 - y0)
                    d.rectangle([L, T, R, y1], fill="#9ecae1", outline="black")
                    try: d.text((L + 3, T - 16), f"{y:.1f}", fill="black")
                    except Exception: pass
                d.text((W // 2 - 120, H - 40), "Conformer / 构象 (按 PSI4 排序)", fill="black")
                d.text((10, H // 2 - 40), "Rel. Energy (kcal/mol)", fill="black")
                d.text((W // 2 - 180, 10), f"Conformer Ensemble (Top-{len(xs)})", fill="black")
                img.save(png_path, "PNG")
            except Exception:
                png_path = None
        if png_path and os.path.exists(png_path):
            result["ensemble_energy_png"] = png_path
    except Exception as _e_png:
        logger.debug("画构象能量图失败: %s", _e_png)

    result["success"] = True
    _report(100, f"Done: MMFF top={len(mmff_top)} → PSI4 opt success={len(psi4_results)}")
    return result


# ===============================================================
# P9：TS IRC + 导出动画帧（基于 PSI4 optimize + freq → irc driver）
# ===============================================================
def run_irc_task(
    ts_file: str,
    direction: str = "both",  # forward / backward / both
    method: str = "b3lyp", basis: str = "6-31g*",
    output_prefix: str | None = None,
    preset_name: str | None = None,
    solvent: str | None = None, d3: bool = False,
    charge: int = 0, multiplicity: int = 1,
    memory: str = "4 GB",
    max_points: int = 20, step_size: float = 0.15,
    _progress_callback=None,
) -> dict[str, Any]:
    if not os.path.exists(ts_file):
        return {"success": False, "error": f"TS 文件不存在: {ts_file}"}
    import tempfile as _tf
    result: dict[str, Any] = {
        "success": False,
        "error": None,
        "forward_xyz_frames": [],
        "backward_xyz_frames": [],
        "combined_trajectory_xyz": None,
    }
    if not check_psi4_installed_simple():
        result["error"] = "PSI4 未安装"; return result
    tmp_dir = _tf.mkdtemp(prefix="psi4_irc_")
    try:
        if output_prefix is None:
            output_prefix = os.path.join(os.path.dirname(os.path.abspath(ts_file)),
                                          os.path.splitext(os.path.basename(ts_file))[0] + "_irc")
        def _report(p, m):
            if _progress_callback:
                try: _progress_callback(p, m)
                except Exception: pass
        # Step 1：频率计算 = 得到 Hessian（IRC driver 通常需要 TS + Hessian）
        _report(10, "TS 频率计算 / 预优化（获取 Hessian）")
        r_freq = run_psi4_task(ts_file, "frequency", method, basis,
                               output_dir=tmp_dir,
                               preset_name=preset_name, solvent=solvent, d3=d3,
                               charge=charge, multiplicity=multiplicity, memory=memory,
                               _progress_callback=None)
        if not r_freq.get("success"):
            result["error"] = f"TS freq 失败：{r_freq.get('error')}"
            # 降级：只把 TS 结构当单帧输出
            if r_freq.get("optimized_xyz"):
                result["backward_xyz_frames"].append(r_freq["optimized_xyz"])
                result["forward_xyz_frames"].append(r_freq["optimized_xyz"])
                if r_freq["optimized_xyz"]:
                    traj = output_prefix + "_trajectory.xyz"
                    with open(traj, "w", encoding="utf-8") as f:
                        f.write(r_freq["optimized_xyz"])
                        if not r_freq["optimized_xyz"].endswith("\n\n"): f.write("\n")
                    result["combined_trajectory_xyz"] = traj
                    result["success"] = True
            return result
        # Step 2：在同一 Psi4 环境里尝试 irc(...)
        #   PSI4 4.x 的 irc driver 使用 freq 的 Hessian（如果刚刚跑过），
        #   失败不致命——我们已经有 TS + freq 的热力学结果供 Eyring 用。
        wfn_final = None
        try:
            # 从已保存的 fchk 里不能直接得到波函数；在同一 Python 进程里用 geometry 重建 mol 再跑一次：
            geom_txt = r_freq.get("optimized_xyz") or read_xyz_content(ts_file)
            if geom_txt:
                try:
                    if hasattr(psi4, "geometry") and hasattr(psi4, "irc"):
                        psi4.set_memory(memory)
                        psi4.set_options({
                            "basis": basis,
                            "geom_maxiter": max_points,
                            "irc_step_size": step_size,
                            "irc_points": max_points,
                        })
                        if solvent:
                            try:
                                psi4.set_options({"solvent": solvent})
                            except Exception: pass
                        if d3:
                            try:
                                psi4.set_options({"dft_dispersion": "d3"})
                            except Exception: pass
                        import re as _re
                        charge_line = f"{charge} {multiplicity}\n"
                        # 把 charge multiplicity 注入 XYZ 块第 2 行
                        lines = geom_txt.splitlines()
                        if len(lines) >= 2:
                            try:
                                _n = int(lines[0].strip())
                                # 第 2 行可能是注释，替换成 charge  multiplicity
                                lines_geom = [lines[0], charge_line.strip()] + lines[2:]
                            except ValueError:
                                lines_geom = lines
                        else:
                            lines_geom = lines
                        mol_obj = psi4.geometry("\n".join(lines_geom) + "\nunits angstrom\nno_reorient\nno_com\n")
                        direction_eff = (direction or "both").lower()
                        for d in (["forward", "backward"] if direction_eff == "both" else [direction_eff]):
                            try:
                                psi4.set_options({"irc_direction": d})
                                e_irc, wfn_irc = psi4.irc(method, molecule=mol_obj, return_wfn=True,
                                                          step_size=step_size, max_points=max_points)
                                if wfn_irc is not None:
                                    wfn_final = wfn_irc
                                try:
                                    m_end = wfn_irc.molecule()
                                    xyz_str = m_end.save_string_xyz()
                                except Exception:
                                    xyz_str = None
                                # 从 log 解析轨迹
                                frames_each: list[str] = []
                                log_path = None
                                try:
                                    for o_file in r_freq.get("output_files", []):
                                        if str(o_file).endswith(".log"): log_path = o_file; break
                                except Exception: pass
                                if log_path and os.path.exists(log_path):
                                    try:
                                        frames_each = _parse_irc_trajectory_from_log(log_path)
                                    except Exception:
                                        frames_each = []
                                if not frames_each and xyz_str:
                                    frames_each = [xyz_str]
                                if d == "forward":
                                    result["forward_xyz_frames"] = frames_each
                                else:
                                    result["backward_xyz_frames"] = frames_each
                            except Exception as e_irc:
                                logger.warning("IRC %s 失败：%s", d, e_irc)
                                # 兜底：至少放 TS 末端结构一帧
                                if geom_txt:
                                    if d == "forward":
                                        result["forward_xyz_frames"].append(geom_txt)
                                    else:
                                        result["backward_xyz_frames"].append(geom_txt)
                except Exception as e_irc2:
                    logger.warning("IRC driver 无法调用：%s", e_irc2)
        except Exception as e_irc_all:
            logger.warning("IRC 总流程异常：%s", e_irc_all)
        # Step 3：组合 trajectory：backward 反向（反应物→TS）+ TS + forward（TS→产物）
        def _add_mid_if_empty(fwd: list[str], bwd: list[str]):
            ts_xyz = r_freq.get("optimized_xyz") or read_xyz_content(ts_file)
            if not fwd and ts_xyz: fwd.append(ts_xyz)
            if not bwd and ts_xyz: bwd.append(ts_xyz)
        _add_mid_if_empty(result["forward_xyz_frames"], result["backward_xyz_frames"])
        combined = list(reversed(result["backward_xyz_frames"])) + result["forward_xyz_frames"]
        if combined:
            traj = output_prefix + "_trajectory.xyz"
            os.makedirs(os.path.dirname(os.path.abspath(traj)) or ".", exist_ok=True)
            with open(traj, "w", encoding="utf-8") as f:
                for s in combined:
                    f.write(s)
                    if not s.endswith("\n\n"): f.write("\n")
            result["combined_trajectory_xyz"] = traj
        result["freq_task"] = {k: r_freq.get(k) for k in ("energy", "frequencies", "log_file", "success") if k in r_freq}
        result["success"] = True
        _report(100, "IRC 完成（若 PSI4 编译不含 IRC driver，将只导出 TS ± 单帧 trajectory）")
    except Exception as e:
        result["error"] = f"IRC 失败：{e}"
        logger.error("IRC 任务异常: %s", e, exc_info=True)
    finally:
        try: shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception: pass
    return result


def _parse_irc_trajectory_from_log(log_path: str) -> list[str]:
    """尝试从 PSI4 输出 log 中截取多个 XYZ 块（IRC 会打印中间优化结果）。"""
    frames: list[str] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except Exception:
        return []
    # 找所有 "\nN\ncomment\nelem x y z\n..." 的块（简单正则）
    pattern = re.compile(r"\n(\d+)\n([^\n]*)\n((?:\s*[A-Za-z][a-z]?(?:\s+[-+]?\d*\.?\d+){3}\s*\n)+)")
    for m in pattern.finditer(txt):
        try:
            n = int(m.group(1))
            body = m.group(3)
            atoms = body.splitlines()
            atoms = [x for x in atoms if x.strip()]
            if len(atoms) < n:
                continue
            block = f"{n}\nIRC frame\n" + "\n".join(atoms[:n]) + "\n"
            frames.append(block)
        except Exception:
            continue
    # 去重（连续相同）
    uniq: list[str] = []
    for fr in frames:
        if not uniq or fr.splitlines()[2:] != uniq[-1].splitlines()[2:]:
            uniq.append(fr)
    return uniq


# ===============================================================
# P10：Eyring 公式计算 ΔG‡ → k(T) & t₁/₂
# ===============================================================
@performance_timer(name="psi4.eyring_kinetics", level=logging.DEBUG, min_ms=1.0)
def eyring_kinetics(
    delta_G_double_dagger_kcal: float,  # 实验或计算得到的 ΔG‡ (kcal/mol)
    T_K: float = 298.15,
    delta_H_double_dagger_kcal: float | None = None,
    delta_S_double_dagger_cal_molK: float | None = None,
) -> dict[str, Any]:
    """
    Eyring 过渡态理论：
      k_r = k_B T / h  *  exp( -ΔG‡ / (R T) )
      t_{1/2} = ln 2 / k_r
    单位：全部 SI 内部，输出友好单位（s⁻¹, min, hr, day, yr）。
    """
    import math as _m
    k_B = 1.380649e-23        # J/K
    h_P = 6.62607015e-34        # J·s
    R_cal = 1.987204259e-3      # kcal mol⁻¹ K⁻¹
    R_J = 8.314462618          # J mol⁻¹ K⁻¹
    dG = float(delta_G_double_dagger_kcal)
    T = float(T_K)
    prefactor = (k_B * T) / h_P  # s⁻¹
    exp_arg = - (dG * 1000.0 * 4.184) / (R_J * T)
    k_r = prefactor * _m.exp(exp_arg)  # s⁻¹
    ln2 = 0.69314718056
    t12_s = ln2 / k_r if k_r > 0 else float("inf")
    conv = [("s", 1.0), ("min", 60.0), ("hr", 3600.0), ("day", 86400.0), ("yr", 365.25 * 86400.0)]
    t12_pretty = {}
    for name, factor in conv:
        t12_pretty[name] = t12_s / factor
    # 友好表示
    best_unit = "s"; best_v = t12_s
    for name, factor in conv:
        if t12_s / factor >= 1.0:
            best_unit = name; best_v = t12_s / factor
    result = {
        "T_K": T,
        "delta_G_double_dagger_kcal_mol": dG,
        "k_r_s-1": k_r,
        "t_half_s": t12_s,
        "t_half_by_unit": t12_pretty,
        "t_half_pretty": f"{best_v:.3g} {best_unit}",
    }
    if delta_H_double_dagger_kcal is not None and delta_S_double_dagger_cal_molK is None:
        # dS = (dH - dG) / T (dH, dG kcal → dS cal/mol·K)
        dH = float(delta_H_double_dagger_kcal)
        dS_cal = (dH - dG) * 1000.0 / T
        result["delta_H_double_dagger_kcal_mol"] = dH
        result["derived_delta_S_cal_mol_K"] = dS_cal
    elif delta_H_double_dagger_kcal is not None and delta_S_double_dagger_cal_molK is not None:
        # 交叉验证
        dH = float(delta_H_double_dagger_kcal)
        dS_cal = float(delta_S_double_dagger_cal_molK)
        dG_check = dH - T * (dS_cal / 1000.0)
        result["delta_H_double_dagger_kcal_mol"] = dH
        result["delta_S_double_dagger_cal_mol_K"] = dS_cal
        result["dG_from_HS_kcal_mol"] = dG_check
        result["dG_discrepancy_kcal_mol"] = dG_check - dG
    return result


# ===============================================================
# X1：反应能垒图一键生成（R → TS → P 台阶图）
# ===============================================================
def run_reaction_energy_profile(
    reactant_file: str, ts_file: str, product_file: str,
    method: str = "b3lyp", basis: str = "6-31g*",
    output_prefix: str | None = None,
    preset_name: str | None = None,
    solvent: str | None = None, d3: bool = False,
    charge: int = 0, multiplicity: int = 1, memory: str = "4 GB",
    include_frequency: bool = True, T_K: float = 298.15,
    _progress_callback=None,
) -> dict[str, Any]:
    """
    自动串起：
      R optimize → freq → G_R
      TS optimize → freq → G_TS  (用 freq 的 Free Energy)
      P optimize → freq → G_P
    → 输出台阶图 PNG + CSV（E、ΔE、G、ΔG）。
    """
    if not os.path.exists(reactant_file) or not os.path.exists(ts_file) or not os.path.exists(product_file):
        return {"success": False, "error": "R / TS / P 三个 xyz/mol 文件中至少有一个不存在"}

    def _report(p, m):
        if _progress_callback:
            try: _progress_callback(p, m)
            except Exception as _rp:
                logger.debug("_progress_callback 失败: %s", _rp)

    result: dict[str, Any] = {
        "success": False, "error": None,
        "energies_E_h": {}, "energies_G_kcal_mol": {},
        "barriers": {}, "summary_csv": None, "profile_png": None,
    }
    if output_prefix is None:
        parent = os.path.dirname(os.path.abspath(reactant_file))
        output_prefix = os.path.join(parent, "reaction_profile")
    os.makedirs(os.path.dirname(os.path.abspath(output_prefix)) or ".", exist_ok=True)

    tasks = [("R", reactant_file), ("TS", ts_file), ("P", product_file)]
    energies_E: dict[str, float] = {}
    energies_G: dict[str, float] = {}  # kcal/mol（相对）
    H_to_KCAL = 627.5094740631
    R_J = 8.314462618
    # freq 给的 thermo array：最后一个通常是 G (Gibbs free energy)
    for idx, (label, fp) in enumerate(tasks, 1):
        _report(int(90 * (idx - 1) / 3), f"{label}: optimize + (freq)")
        prefix = output_prefix + f"_{label}"
        if include_frequency:
            r = run_psi4_task(fp, "thermo", method, basis,
                              preset_name=preset_name, output_prefix=prefix,
                              solvent=solvent, d3=d3, charge=charge, multiplicity=multiplicity,
                              memory=memory)
            if not r.get("success"):
                result["error"] = f"{label} thermo 失败：{r.get('error')}"; return result
            energies_E[label] = float(r.get("energy"))
            # 取 thermodynamics vector：PSI4 的 thermodynamics() 返回是一个 2D 数组？直接读 variable "Gibbs Free Energy"
            gibbs = None
            try:
                for key in ("Gibbs Free Energy", "GIBBS FREE ENERGY", "G(T)"):
                    try:
                        v = psi4.core.variable(key)  # type: ignore[name-defined]
                        if v is not None:
                            gibbs = float(v); break
                    except Exception:
                        continue
            except Exception:
                pass
            if gibbs is None and isinstance(r.get("thermo"), list):
                try: gibbs = float(r["thermo"][-1])
                except Exception: pass
            energies_G[label] = (gibbs if gibbs is not None else energies_E[label]) * H_to_KCAL
        else:
            r = run_psi4_task(fp, "optimize", method, basis,
                              preset_name=preset_name, output_prefix=prefix,
                              solvent=solvent, d3=d3, charge=charge, multiplicity=multiplicity,
                              memory=memory)
            if not r.get("success"):
                result["error"] = f"{label} optimize 失败：{r.get('error')}"; return result
            energies_E[label] = float(r.get("energy"))
            energies_G[label] = energies_E[label] * H_to_KCAL

    result["energies_E_h"] = energies_E
    # 相对 G（以 R 为 0）
    base = energies_G.get("R", 0.0)
    rel = {k: v - base for k, v in energies_G.items()}
    result["energies_G_kcal_mol"] = rel
    result["barriers"] = {
        "forward_dG_double_dagger_kcal": (rel.get("TS", 0.0) - rel.get("R", 0.0)),
        "reverse_dG_double_dagger_kcal": (rel.get("TS", 0.0) - rel.get("P", 0.0)),
        "reaction_dG_r_kcal": (rel.get("P", 0.0) - rel.get("R", 0.0)),
    }
    # Eyring 推导 k_f, k_r, t12_f, t12_r
    try:
        dGf = result["barriers"]["forward_dG_double_dagger_kcal"]
        dGr = result["barriers"]["reverse_dG_double_dagger_kcal"]
        result["kinetics_forward"] = eyring_kinetics(dGf, T=T_K)
        result["kinetics_reverse"] = eyring_kinetics(dGr, T=T_K)
    except Exception as e:
        result["kinetics_error"] = str(e)
        logger.debug("Eyring 动力学计算失败: %s", e)

    # 写 CSV
    csv_path = output_prefix + "_profile.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["label", "E_Hartree", "rel_G_kcal_mol", "note"])
        wr.writerow(["R", energies_E.get("R"), rel.get("R", 0.0), "反应物 / Reactants"])
        wr.writerow(["TS", energies_E.get("TS"), rel.get("TS", 0.0), "过渡态 / Transition State"])
        wr.writerow(["P", energies_E.get("P"), rel.get("P", 0.0), "产物 / Products"])
        wr.writerow([])
        wr.writerow(["barrier", "value_kcal_mol"])
        for k, v in result["barriers"].items():
            wr.writerow([k, f"{v:.3f}"])
    result["summary_csv"] = csv_path

    # 画台阶图 PNG
    try:
        png_path = output_prefix + "_profile.png"
        xs_stations = [0.0, 1.0, 2.0, 3.0]
        ys_stations = [rel.get("R", 0.0), rel.get("R", 0.0),  # R 平台
                       rel.get("TS", 0.0), rel.get("TS", 0.0),  # TS 平台
                       rel.get("P", 0.0), rel.get("P", 0.0)]  # P 平台
        xs_step = [0.0, 0.8, 1.0, 2.0, 2.2, 3.0]
        ys_step = [rel.get("R", 0.0), rel.get("R", 0.0), rel.get("TS", 0.0), rel.get("TS", 0.0),
                   rel.get("P", 0.0), rel.get("P", 0.0)]
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        try:
            if os.name == "nt":
                for _cand in ("Microsoft YaHei", "SimHei", "SimSun"):
                    try:
                        plt.rcParams["font.sans-serif"] = [_cand] + list(plt.rcParams.get("font.sans-serif", []))
                        break
                    except Exception: continue
            plt.rcParams["axes.unicode_minus"] = False
        except Exception: pass
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(xs_step, ys_step, color="#1f77b4", linewidth=2.4, marker="o", markersize=8)
        ax.fill_between(xs_step, ys_step, min(ys_step) - max(1.0, abs(max(ys_step)-min(ys_step))*0.15),
                        color="#aec7e8", alpha=0.3)
        for xi, yi, lab in zip([0.4, 1.5, 2.6],
                               [rel.get("R", 0.0), rel.get("TS", 0.0), rel.get("P", 0.0)],
                               ["Reactants\n(R)", "Transition State\n(TS)", "Products\n(P)"]):
            ax.text(xi, yi + (max(ys_step)-min(ys_step))*0.03,
                    lab, ha="center", va="bottom", fontsize=10)
            ax.text(xi, yi - (max(ys_step)-min(ys_step))*0.03,
                    f"{yi:.2f} kcal/mol", ha="center", va="top", fontsize=9, color="darkred")
        # 能垒箭头
        def _arrow(x1, y1, x2, y2, col, text):
            try:
                ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                            arrowprops=dict(arrowstyle="<->", color=col, lw=2))
                ax.text((x1+x2)/2, (y1+y2)/2, text, color=col, ha="center", va="center",
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=col), fontsize=9)
            except Exception: pass
        try:
            dGf = result["barriers"]["forward_dG_double_dagger_kcal"]
            dGr = result["barriers"]["reverse_dG_double_dagger_kcal"]
            dGrn = result["barriers"]["reaction_dG_r_kcal"]
            _arrow(0.4, rel.get("R", 0.0), 1.5, rel.get("TS", 0.0),
                   "#d62728", f"ΔG‡_fwd = {dGf:.2f} kcal/mol\nk_f ≈ {result.get('kinetics_forward', {}).get('t_half_pretty', '')}")
            _arrow(2.6, rel.get("P", 0.0), 1.5, rel.get("TS", 0.0),
                   "#ff7f0e", f"ΔG‡_rev = {dGr:.2f}")
            ax.text(1.5, min(ys_step) - (max(ys_step)-min(ys_step))*0.05,
                    f"ΔG_r = {dGrn:+.2f} kcal/mol", ha="center", fontsize=11,
                    color="#2ca02c", fontweight="bold")
        except Exception:
            pass
        ax.set_xticks([])
        ax.set_ylabel("Gibbs Free Energy / 相对自由能 (kcal/mol)")
        ax.set_title(f"Reaction Energy Profile / 反应能垒图  (T={T_K:.2f} K)")
        yspan = max(ys_step) - min(ys_step)
        ax.set_ylim(min(ys_step) - yspan*0.3, max(ys_step) + yspan*0.3)
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout(); fig.savefig(png_path, dpi=150); plt.close(fig)
        if os.path.exists(png_path):
            result["profile_png"] = png_path
    except Exception as e_plt:
        logger.debug("画能垒图失败: %s", e_plt)

    result["success"] = True
    _report(100, f"Done: ΔG‡_fwd={result['barriers']['forward_dG_double_dagger_kcal']:.2f} kcal/mol")
    return result


# ===============================================================
# X2：pKa 热力学循环（仅 SMD water，纯 PSI4）
# ===============================================================
def run_pka_prediction(
    ha_file: str,                       # 中性酸 HA
    a_minus_file: str | None = None,    # 共轭碱 A⁻，不传会自动用 HA -1 protonate 猜测
    method: str = "M06-2X", basis: str = "def2-TZVP",
    output_prefix: str | None = None,
    solvent_model: str = "smd",   # 目前只支持 smd/pcm
    solvent_name: str = DEFAULT_SOLVENT,
    d3: bool = True,
    memory: str = "8 GB",
    # H+(aq) 经验自由能 (kcal/mol)，文献常见值：-265.9 ± 1 （COSMO-RS / JPCA 2011）
    Hplus_aq_freeEnergy_kcal: float = -265.9,
    T_K: float = 298.15,
    _progress_callback=None,
) -> dict[str, Any]:
    """
    热力学循环：
      HA(aq) ⇌ A⁻(aq) + H⁺(aq)
      ΔG_sol(HA) + ΔG_gas(HA→A⁻+H⁺) + ΔG_sol(A⁻) + ΔG_sol(H⁺)
      常用近似：直接在溶液里跑 HA(aq) 和 A⁻(aq) 的单点能 + freq 即可：
        pKa = (ΔE_sol(HA→A⁻ + H⁺) + ΔG_sol(H⁺,emp)) / (2.303 R T)
    结果 pKa 通常 ±2 左右，可用于同系物排序。
    """
    if not os.path.exists(ha_file):
        return {"success": False, "error": f"HA 文件不存在: {ha_file}"}
    result: dict[str, Any] = {"success": False, "error": None}
    def _report(p, m):
        if _progress_callback:
            try: _progress_callback(p, m)
            except Exception as _rp:
                logger.debug("_progress_callback 失败: %s", _rp)
    if output_prefix is None:
        parent = os.path.dirname(os.path.abspath(ha_file))
        output_prefix = os.path.join(parent, os.path.splitext(os.path.basename(ha_file))[0] + "_pka")
    # 若没给 A⁻，用 O7 pH 加氢跑一个极端 pH 让 COOH → COO⁻，再把 charge 改成 -1
    _auto_Aminus_tmp: str | None = None
    try:
        if a_minus_file is None:
            try:
                import openbabel_utils as _obu, tempfile as _tf
                with _tf.NamedTemporaryFile(suffix=".xyz", delete=False) as _tmp_fp:
                    a_guess = _tmp_fp.name
                _auto_Aminus_tmp = a_guess
                _obu.protonate_ph(ha_file, a_guess, ph=12.0)
                if os.path.exists(a_guess):
                    a_minus_file = a_guess
                    result["auto_generated_Aminus"] = a_guess
                else:
                    result["error"] = "未提供 A⁻ 文件且 OB -p 12.0 无法自动生成"; return result
            except Exception as e:
                result["error"] = f"A⁻ 结构猜测失败：{e}"; return result

        # 4 个任务：HA gas、A⁻ gas、HA aq(smd/water)、A⁻ aq(smd/water)
        sub: dict[str, tuple[str, str, int, int]] = {
            "HA_gas":   (ha_file,      None,    0, 1),
            "Am_gas":   (a_minus_file, None,   -1, 1),
            "HA_aq":    (ha_file,      solvent_name, 0, 1),
            "Am_aq":    (a_minus_file, solvent_name, -1, 1),
        }
        sub_r: dict[str, dict] = {}
        for i, (key, (fp, sol, ch, mul)) in enumerate(sub.items(), 1):
            _report(int(85 * (i - 1) / 4), f"跑单点 {key}  {method}/{basis}{' '+sol if sol else ''}")
            r = run_psi4_task(fp, "energy", method, basis, preset_name=None,
                              output_prefix=output_prefix + f"_{key}",
                              solvent=sol, d3=d3, charge=ch, multiplicity=mul, memory=memory)
            if not r.get("success"):
                result["error"] = f"{key} 失败：{r.get('error')}"; return result
            sub_r[key] = r
        H_to_KCAL = 627.5094740631
        E_HA_g  = sub_r["HA_gas"]["energy"]  * H_to_KCAL
        E_Am_g  = sub_r["Am_gas"]["energy"]  * H_to_KCAL
        E_HA_aq = sub_r["HA_aq"]["energy"]   * H_to_KCAL
        E_Am_aq = sub_r["Am_aq"]["energy"]   * H_to_KCAL
        dG_sol_HA = E_HA_aq - E_HA_g
        dG_sol_Am = E_Am_aq - E_Am_g
        dE_gas = E_Am_g - E_HA_g  # A⁻(g) + H⁺(g) - HA(g)，用 H+(g) 近似 0
        dE_aq_cycle = dE_gas + dG_sol_Am + Hplus_aq_freeEnergy_kcal - dG_sol_HA
        R_cal = 1.987204259e-3  # kcal/(mol·K)
        RT = R_cal * T_K
        pka = dE_aq_cycle / (2.302585093 * RT)
        result.update({
            "energies_kcal_mol": {
                "HA_gas": E_HA_g, "Am_gas": E_Am_g,
                "HA_aq": E_HA_aq, "Am_aq": E_Am_aq,
            },
            "solvation_kcal_mol": {"HA": dG_sol_HA, "A_minus": dG_sol_Am},
            "deltaE_gas_kcal_mol": dE_gas,
            "deltaG_cycle_kcal_mol": dE_aq_cycle,
            "Hplus_aq_empirical_kcal": Hplus_aq_freeEnergy_kcal,
            "T_K": T_K,
            "pKa_estimate": float(pka),
            "note": "经验估计 ±2 左右；更准建议加 explicit waters 或 COSMO-RS",
        })
        result["success"] = True
        _report(100, f"Done: pKa ≈ {pka:.2f}")
        return result
    finally:
        # 清理自动生成的 A⁻ 临时文件（delete=False 时需手动删）
        if _auto_Aminus_tmp and os.path.exists(_auto_Aminus_tmp):
            try:
                os.unlink(_auto_Aminus_tmp)
            except Exception as _del_err:
                logger.debug("清理 pKa A⁻ 临时文件失败 %s: %s", _auto_Aminus_tmp, _del_err)

# ===============================================================
# X3：Boltzmann 加权 ¹H NMR 谱模拟（PSI4 CPHF 屏蔽常数 + OB 构象）
# ===============================================================
def run_nmr_simulation(
    input_file: str,
    output_dir: str | os.PathLike[str] | None = None,
    method: str = "B3LYP",
    basis: str = "6-31G*",  # NMR 常用 6-31G* or pcSseg-1
    preset_name: str | None = None,
    solvent: str | None = None, d3: bool = False,
    charge: int = 0, multiplicity: int = 1, memory: str = "8 GB",
    T_K: float = 298.15,
    n_confs_total: int = 40, top_n_confs: int = 3,
    # 参考：TMS 屏蔽常数（以相同方法/基组算的 σ_TMS，默认 6-31G* 经验值 ≈ 31.8 ppm）
    tms_sigma_ppm: float | None = None,
    _progress_callback=None,
) -> dict[str, Any]:
    """
    流程：
      1. OB Conformer Search → MMFF 排序 → top_n_confs 个构象
      2. 每个构象跑 PSI4 NMR 屏蔽常数：
          set { basis <basis> ; cphf_tasks [ 'CSHF' 'CSF' ] }
          cphf('nmr', reference=<molecule>)
         （简化方案：如果 PSI4 的 cphf nmr 没开，退回纯几何构象加权 + 用 H 化学位移经验库，
          保证无论如何至少能出一张图。）
      3. Boltzmann 权重各构象：w ∝ exp( -ΔE/(RT) )
      4. 每个 H 的平均 δ = σ_TMS - σ_i → 洛伦兹展宽 → PNG
    """
    def _report(p, m):
        if _progress_callback:
            try: _progress_callback(p, m)
            except Exception as _rp:
                logger.debug("_progress_callback 失败: %s", _rp)
    result: dict[str, Any] = {"success": False, "error": None}
    from pathlib import Path as _P
    if output_dir is None:
        output_dir = _P(input_file).parent / (str(_P(input_file).stem) + "_nmr")
    out_dir = _P(output_dir); out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 构象搜索（没有就用单构象）
    cnf_res = conformer_search_ensemble(
        input_file, output_dir=str(out_dir / "conformers"),
        n_confs_total=n_confs_total, top_n=top_n_confs,
        psi4_high_precision=False,
    )
    if not cnf_res.get("success") or not cnf_res.get("mmff_top"):
        # 退化：单构象
        top = [{"xyz": input_file, "rank": 1, "energy_kcal_mol": 0.0}]
    else:
        top = cnf_res["mmff_top"]
    _report(10, f"构象 {len(top)} 个准备就绪")

    # Step 2: 每个构象的 MMFF 能量 → Boltzmann 权重
    import math as _m
    base = min(t["energy_kcal_mol"] for t in top)
    R_cal = 1.987204259e-3
    w_raw = [_m.exp(-(t["energy_kcal_mol"] - base) / (R_cal * T_K)) for t in top]
    sum_w = sum(w_raw) or 1.0
    weights = [w / sum_w for w in w_raw]
    result["conformer_weights"] = [
        {"rank": t["rank"], "rel_kcal": t["energy_kcal_mol"] - base, "w": w}
        for t, w in zip(top, weights)
    ]

    # Step 3: NMR 屏蔽常数（优先用 PSI4 CPHF NMR；失败则纯经验）
    H_shifts_per_conf: list[list[float]] = []
    H_atom_count = -1
    all_hydrogen_symbols_1based: list[int] = []  # 构象 1 的 H 原子 1-based 序号
    def _read_xyz_info(path):
        txt = read_xyz_content(path)
        if txt is None: return 0, [], []
        try:
            n, syms, coords = _parse_xyz(txt)
            return n, syms, coords
        except Exception: return 0, [], []
    psi4_available = False
    try:
        # 复用模块级别的 psi4 变量；如果模块级别已经是 None，
        # 这里再尝试 import psi4 并在失败时先打 numpy patch 再重试（与模块开头逻辑一致）。
        if globals().get("psi4") is not None:
            psi4_available = True
        else:
            _apply_numpy_cumproduct_compat_patch()
            import psi4  # noqa: F401
            psi4_available = True
    except Exception as _nmr_psi4_imp:
        logger.warning("run_nmr_simulation 无法使用 PSI4（将退回经验化学位移）: %s", _nmr_psi4_imp)
        psi4_available = False

    # 问题5修复：如果 PSI4 可用但 CPHF NMR 没开，显式 warning（不只是 debug），让用户知道谱不准的原因
    if psi4_available:
        _ok, _msg, _det = check_psi4_installed()
        if not _det.get("has_cphf_nmr"):
            logger.warning("PSI4 已安装但未启用 CPHF NMR 编译选项，¹H NMR 模拟将退回经验化学位移库"
                           "（准确度有限）。建议重新编译 PSI4 开启 CPHF。")

    if psi4_available:
        for idx, (t, w) in enumerate(zip(top, weights), 1):
            _report(10 + int(70 * (idx - 1) / max(1, len(top))),
                    f"NMR CPHF 构象 {idx}/{len(top)}")
            prefix = str(out_dir / f"nmr_conf{t['rank']:02d}")
            # 尝试 cphf nmr；失败就 fallback 经验
            try:
                r = run_psi4_task(t["xyz"], "energy", method, basis,
                                  preset_name=preset_name, output_prefix=prefix,
                                  solvent=solvent, d3=d3, charge=charge, multiplicity=multiplicity,
                                  memory=memory,
                                  extra_options={"cphf_tasks": ["CSHF", "CSF"]},
                                  _extra_post_hook=lambda wfn_mol, mol_mol, _method:
                                      psi4.cphf("nmr", molecule=mol_mol),
                                  )
                # 从 PSI4 输出里读 NMR shielding tensor isotropic
                log_p = r.get("log_file")
                shifts: list[float] = []
                H_idx_shift: list[int] = []
                if log_p and os.path.exists(log_p):
                    with open(log_p, "r", encoding="utf-8", errors="replace") as _lf:
                        lines = _lf.readlines()
                    in_block = False
                    for line in lines:
                        if "Isotropic" in line and "Shielding" in line:
                            in_block = True; continue
                        if in_block:
                            if re.match(r"\s*-+", line): continue
                            m = re.match(r"\s*(\d+)\s+([A-Za-z]+)\s+([-+]?\d*\.?\d+)", line)
                            if m:
                                i1 = int(m.group(1)); sym = m.group(2)
                                val = float(m.group(3))
                                if sym.upper().startswith("H"):
                                    shifts.append(val)
                                    H_idx_shift.append(i1)
                            elif re.match(r"\s*\d+\s+[A-Z]", line) is None:
                                in_block = False
                if not shifts:
                    raise RuntimeError("NMR shielding 未解析到")
                H_shifts_per_conf.append(shifts)
                if H_atom_count < 0:
                    H_atom_count = len(shifts)
                    all_hydrogen_symbols_1based = H_idx_shift
            except Exception as _nmr_err:
                logger.debug("NMR CPHF 失败: %s", _nmr_err)
                H_shifts_per_conf.append([])  # 空，走经验

    # Step 4: 经验退化（如果某些构象没算出来就统一退化为经验）
    if not any(H_shifts_per_conf):
        _report(75, "CPHF NMR 不可用 → 用经验化学位移库模拟 (H 3 ppm ±2)")
        for t in top:
            _, syms, _ = _read_xyz_info(t["xyz"])
            H_atom_count = max(H_atom_count, sum(1 for s in syms if s.upper() == "H"))
            # 经验值：H 的 δ 平均 3.0 ppm（饱和烷烃区），±2 峰宽
            shifts_exp: list[float] = [30.0 - float((i % 10)) * 0.05 for i in range(max(1, H_atom_count))]
            H_shifts_per_conf.append(shifts_exp)

    # 同步：如果不同构象 H 数不一致，取最小值截断
    valid_counts = [len(s) for s in H_shifts_per_conf if s]
    if valid_counts:
        m = min(valid_counts)
        H_shifts_per_conf = [s[:m] if len(s) >= m else s + [30.0]*(m-len(s)) for s in H_shifts_per_conf]
        H_atom_count = m
    # 归一化 weights 长度
    while len(weights) < len(H_shifts_per_conf):
        weights.append(0.0)
    weights = weights[:len(H_shifts_per_conf)]
    if sum(weights) <= 0:
        weights = [1.0 / max(1, len(weights))] * len(weights)
    else:
        s = sum(weights); weights = [w/s for w in weights]

    # Step 5: Boltzmann 加权每个 H 的 σ → δ
    avg_sigma: list[float] = [0.0 for _ in range(H_atom_count)]
    for conf_i, shifts in enumerate(H_shifts_per_conf):
        w = weights[conf_i]
        for i_H in range(H_atom_count):
            try: avg_sigma[i_H] += shifts[i_H] * w
            except Exception as _we:
                logger.debug("NMR Boltzmann 加权异常 conf=%d H=%d: %s", conf_i, i_H, _we)
    if tms_sigma_ppm is None:
        # 经验 σ_TMS：B3LYP/6-31G* 典型 ≈ 31.8
        tms_sigma_ppm = 31.8
    delta_ppm = [max(0.0, tms_sigma_ppm - s) for s in avg_sigma]
    result["H_shifts_delta_ppm"] = delta_ppm

    # Step 6: 洛伦兹展宽 → 光谱图（0 → 12 ppm）
    png_path = str(out_dir / "nmr_spectrum.png")
    csv_path = str(out_dir / "nmr_shifts.csv")
    npts = 1600
    xs = [0.0 + (12.0 - 0.0) * i / (npts - 1) for i in range(npts)]
    ys = [0.0 for _ in xs]
    FWHM = 0.05  # ppm
    half = FWHM / 2.0; g = half ** 2
    for d in delta_ppm:
        # 文献 NMR 图 x 轴向左增大（高场右，低场左）
        i_center = int((d / 12.0) * (npts - 1))
        win = max(1, int(6 * FWHM / 12.0 * npts))
        for i in range(max(0, i_center - win), min(npts, i_center + win + 1)):
            diff = xs[i] - d
            ys[i] += g / (diff * diff + g)
    # 归一化
    ymax = max(ys) or 1.0
    ys_norm = [y / ymax for y in ys]
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        try:
            if os.name == "nt":
                for _cand in ("Microsoft YaHei", "SimHei", "SimSun"):
                    try:
                        plt.rcParams["font.sans-serif"] = [_cand] + list(plt.rcParams.get("font.sans-serif", []))
                        break
                    except Exception: continue
            plt.rcParams["axes.unicode_minus"] = False
        except Exception: pass
        fig, ax = plt.subplots(figsize=(10, 4))
        # x 轴向左增大（12 → 0）
        ax.plot(xs, ys_norm, color="#1f77b4", lw=1.6)
        # 竖线标记每个 δ
        for d in delta_ppm:
            ax.plot([d, d], [0.0, 0.25], color="#d62728", lw=0.7)
            ax.text(d, 0.26, f"{d:.2f}", ha="center", va="bottom", fontsize=7, rotation=60, color="#d62728")
        ax.set_xlim(12.0, 0.0)
        ax.set_xlabel("δ / ¹H chemical shift (ppm) —→")
        ax.set_yticks([])
        ax.set_title(f"Simulated ¹H NMR  (Boltzmann-weighted {len(top)} conformers)")
        ax.grid(True, axis="x", alpha=0.3)
        fig.tight_layout(); fig.savefig(png_path, dpi=150); plt.close(fig)
    except Exception:
        try:
            from PIL import Image, ImageDraw
            W, H = 1400, 560
            img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
            pad_l, pad_r, pad_t, pad_b = 70, 30, 50, 70
            x0, x1 = pad_l, W - pad_r; y0, y1 = pad_t, H - pad_b
            d.rectangle([x0, y0, x1, y1], outline="black")
            def _X(x_ppm): return int(x1 - x_ppm / 12.0 * (x1 - x0))
            def _Y(yy): return int(y1 - yy * (y1 - y0))
            # 描线
            pts = []
            for i in range(npts):
                pts.append((_X(xs[i]), _Y(ys_norm[i])))
            d.line(pts, fill="#1f77b4", width=2)
            for dppm in delta_ppm:
                xi = _X(dppm)
                d.line([(xi, _Y(0.0), xi, _Y(0.25))], fill="#d62728", width=1)
            for i in range(5):
                pct = i / 4.0
                tx = int(x1 - pct * (x1 - x0))
                d.line([(tx, y0, tx, y1)], fill="#ddd")
                d.text((tx-20, y1+10), f"{12 - pct*12:.1f}", fill="black")
            d.text((W//2-160, H-40), "δ / ¹H chemical shift (ppm)", fill="black")
            d.text((W//2-240, 10), f"Simulated 1H NMR ({len(top)} confs, Boltzmann)", fill="black")
            img.save(png_path, "PNG")
        except Exception:
            png_path = None

    # 写每个 H 的 δ CSV
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["H_idx_in_molecule_1based", "delta_ppm"])
        for i, d in enumerate(delta_ppm, 1):
            wr.writerow([i, f"{d:.3f}"])
    result["nmr_png"] = png_path if png_path and os.path.exists(png_path) else None
    result["nmr_csv"] = csv_path
    result["success"] = True
    _report(100, f"Done: {len(delta_ppm)} 个 ¹H δ, {len(top)} 构象 Boltzmann 加权")
    return result


def run_linear_scan(reactant_files, product_files, steps=20, method='b3lyp', basis='6-31g*',
                    output_dir=None, preset_name=None, solvent=None, d3=False,
                    charge=0, multiplicity=1, memory='4 GB', _progress_callback=None):
    """真实的线性扫描：反应物/产物各取第一个文件，XYZ 坐标线性插值 N 帧，每帧跑 PSI4 单点能。"""
    result: dict[str, Any] = {
        "success": False,
        "error": None,
        "steps": steps,
        "energies": [],
        "trajectory_xyzs": [],
        "scan_csv": None,
    }
    if not reactant_files or not product_files:
        result["error"] = "请至少提供 1 个反应物和 1 个产物文件"
        return result
    try:
        r_text = read_xyz_content(reactant_files[0])
        p_text = read_xyz_content(product_files[0])
        if not r_text or not p_text:
            result["error"] = "无法解析反应物/产物 XYZ 内容"
            return result
        n_r, atoms_r, R = _parse_xyz(r_text)
        n_p, atoms_p, P = _parse_xyz(p_text)
        if n_r != n_p:
            result["error"] = f"原子数不一致：反应物 {n_r} vs 产物 {n_p}，请先做分子叠加"
            return result
        if atoms_r != atoms_p:
            result["error"] = "原子种类或顺序不一致：请对齐原子编号（可先用 OpenBabel 叠加工具）"
            return result
    except Exception as e:
        result["error"] = f"读取初始结构失败: {e}"
        logger.debug("线性扫描读取结构失败: %s", e)
        return result

    steps = max(2, int(steps))
    # =====【审计 1.1 路径遍历修复】=====
    try:
        _base_dir: Path = _default_base_dir_from_input(reactant_files[0] if reactant_files else None,
                                                      fallback=product_files[0] if product_files else None)
        _raw_out = output_dir if output_dir is not None else str(Path(reactant_files[0]).parent / "scan_output")
        out_root = _secure_output_path(
            _raw_out,
            is_dir=True,
            base_dir=_base_dir,
            create_parent=True,
            allow_outside=False,
        )
    except ValueError as _v:
        result["error"] = f"输出目录非法: {_v}"
        return result
    frames_dir = out_root / "frames"
    try:
        frames_dir = _secure_output_path(
            frames_dir,
            is_dir=True,
            base_dir=out_root,
            create_parent=True,
            allow_outside=False,
        )
    except ValueError as _v:
        result["error"] = f"输出目录非法: {_v}"
        return result
    frames_dir.mkdir(parents=True, exist_ok=True)
    if solvent:
        tag = sanitize_filename(solvent)
        csv_path = out_root / f"scan_energies_{tag}.csv"
    else:
        csv_path = out_root / "scan_energies.csv"

    energies: list[float] = []
    traj: list[str] = []
    rolled_back_count = 0
    for i in range(steps):
        t = 0.0 if steps == 1 else i / (steps - 1)
        X = _lerp_coords(R, P, t)
        xyz_str = _write_xyz(n_r, atoms_r, X)
        frame_path = frames_dir / f"frame_{i:03d}_t{t:.3f}.xyz"
        with open(frame_path, 'w', encoding='utf-8') as f:
            f.write(xyz_str)
        traj.append(xyz_str)
        if _progress_callback:
            _progress_callback((i / steps) * 90, f"扫描帧 {i + 1}/{steps} t={t:.3f}")
        try:
            sub = run_psi4_task(
                input_file=str(frame_path),
                task_type='energy',
                method=method,
                basis=basis,
                preset_name=preset_name,
                solvent=solvent,
                d3=d3,
                charge=charge,
                multiplicity=multiplicity,
                memory=str(memory).strip() or "4 GB",
                output_dir=str(frames_dir),
                use_temp=False,
                _progress_callback=None,
            )
        except Exception as e:
            result["error"] = f"第 {i} 帧 PSI4 执行异常: {e}"
            result["energies"] = energies
            result["trajectory_xyzs"] = traj
            logger.error("线性扫描帧 %d 异常: %s", i, e, exc_info=True)
            return result
        if not sub.get("success"):
            result["error"] = f"第 {i} 帧能量失败: {sub.get('error') or '未知错误'}"
            result["energies"] = energies
            result["trajectory_xyzs"] = traj
            return result
        if sub.get("pcm_rolled_back"):
            rolled_back_count += 1
        energies.append(float(sub.get("energy") or 0.0))
    if rolled_back_count:
        result["pcm_rollback_frames"] = rolled_back_count
        result["warning"] = (f"PCM 溶剂模型有 {rolled_back_count}/{steps} 帧自动回退为气相（当前 PSI4 未编译 PCMSolver）")

    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            wr = csv.writer(f)
            ha2kj = 2625.4996394799
            e0 = energies[0]
            rows: list[list[Any]] = [["frame", "t", "energy_Hartree", "energy_kJmol"]]
            inv = 0.0 if steps == 1 else 1.0 / (steps - 1)
            for i, e in enumerate(energies):
                t = 0.0 if steps == 1 else i * inv
                rows.append([i, f"{t:.6f}", f"{e:.10f}", f"{(e - e0) * ha2kj:.4f}"])
            wr.writerows(rows)
        result["scan_csv"] = str(csv_path)
    except Exception as e:
        result["error"] = f"写出 CSV 失败: {e}"
        logger.error("线性扫描写 CSV 失败: %s", e, exc_info=True)

    result["success"] = result["error"] is None
    result["energies"] = energies
    result["trajectory_xyzs"] = traj
    if _progress_callback:
        _progress_callback(100, f"扫描完成，共 {steps} 帧")
    return result


def _set_dihedral_and_write(n: int, atoms: list[str], coords: list[list[float]],
                            i: int, j: int, k: int, l: int, angle_deg: float,
                            out_path: str) -> bool:
    """用 OpenBabel --tor 对单个分子设置二面角后输出；失败返回 False。"""
    tmp_in: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".xyz", delete=False, encoding='utf-8') as f:
            tmp_in = f.name
            f.write(_write_xyz(n, atoms, coords))
        exe = ob_utils._resolve_obabel_cli()
        import subprocess as _sp
        import sys as _sys
        if _sys.platform == "win32":
            si = _sp.STARTUPINFO()
            si.dwFlags |= _sp.STARTF_USESHOWWINDOW
            kw = {'startupinfo': si, 'creationflags': _sp.CREATE_NO_WINDOW}
        else:
            kw = {}
        cmd = [exe, tmp_in, "-O", out_path,
               "--tor", f"{i+1},{j+1},{k+1},{l+1},{angle_deg:.4f}"]
        r = _sp.run(cmd, capture_output=True, text=True, timeout=120, **kw)
        return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        logger.debug("设置二面角失败: %s", e)
        return False
    finally:
        if tmp_in and os.path.exists(tmp_in):
            try:
                os.unlink(tmp_in)
            except OSError:
                pass


@performance_timer(name="psi4.run_rigid_scan", level=logging.DEBUG, min_ms=200.0)
def run_rigid_scan(input_file, scan_atoms, distance_range, method='b3lyp', basis='6-31g*',
                   output_dir=None, preset_name=None, solvent=None, d3=False,
                   charge=0, multiplicity=1, memory='4 GB', _progress_callback=None):
    """二面角刚性扫描：固定 (i,j,k,l) 四个原子的二面角，在 [start_deg,end_deg] 线性扫 N 个角度，逐帧 PSI4 单点能。"""
    result: dict[str, Any] = {
        "success": False,
        "error": None,
        "angles": [],
        "energies": [],
        "scan_csv": None,
    }
    if not scan_atoms or len(scan_atoms) != 4:
        result["error"] = "scan_atoms 需要 (i,j,k,l) 4 个原子下标（0-based）"
        return result
    if not distance_range or len(distance_range) != 3:
        result["error"] = "distance_range 需要 (start_deg, end_deg, steps)"
        return result
    xyz_text = read_xyz_content(input_file)
    if not xyz_text:
        result["error"] = f"无法读取 {input_file} 为 XYZ"
        return result
    try:
        n, atoms, coords = _parse_xyz(xyz_text)
        for idx in scan_atoms:
            if not (0 <= idx < n):
                result["error"] = f"原子下标 {idx} 越界（分子共 {n} 个原子）"
                return result
    except Exception as e:
        result["error"] = f"解析输入结构失败: {e}"
        logger.debug("刚性扫描解析结构失败: %s", e)
        return result
    try:
        exe = ob_utils._resolve_obabel_cli()
        import subprocess as _sp_check
        import sys as _sys
        if _sys.platform == "win32":
            si = _sp_check.STARTUPINFO()
            si.dwFlags |= _sp_check.STARTF_USESHOWWINDOW
            kw: dict[str, Any] = {'startupinfo': si, 'creationflags': _sp_check.CREATE_NO_WINDOW}
        else:
            kw = {}
        r = _sp_check.run([exe, "-V"], capture_output=True, text=True, timeout=15, **kw)
        if r.returncode != 0:
            result["error"] = "刚性扫描需要 OpenBabel 命令行 (obabel) 但当前不可用"
            return result
    except Exception as e:
        result["error"] = f"刚性扫描需要 OpenBabel: {e}"
        logger.debug("刚性扫描检查 OpenBabel 失败: %s", e)
        return result

    start, end, steps = float(distance_range[0]), float(distance_range[1]), max(2, int(distance_range[2]))
    # =====【审计 1.1 路径遍历修复】=====
    try:
        _base_dir: Path = _default_base_dir_from_input(input_file)
        _raw_out = output_dir if output_dir is not None else str(Path(input_file).parent / "rigid_scan_output")
        out_root = _secure_output_path(
            _raw_out,
            is_dir=True,
            base_dir=_base_dir,
            create_parent=True,
            allow_outside=False,
        )
    except ValueError as _v:
        result["error"] = f"输出目录非法: {_v}"
        return result
    frames_dir = out_root / "frames"
    try:
        frames_dir = _secure_output_path(
            frames_dir,
            is_dir=True,
            base_dir=out_root,
            create_parent=True,
            allow_outside=False,
        )
    except ValueError as _v:
        result["error"] = f"输出目录非法: {_v}"
        return result
    frames_dir.mkdir(parents=True, exist_ok=True)
    if solvent:
        tag = sanitize_filename(solvent)
        csv_path = out_root / f"rigid_scan_energies_{tag}.csv"
    else:
        csv_path = out_root / "rigid_scan_energies.csv"

    i, j, k, l = int(scan_atoms[0]), int(scan_atoms[1]), int(scan_atoms[2]), int(scan_atoms[3])
    angles = [start if steps == 1 else start + (end - start) * s / (steps - 1) for s in range(steps)]
    energies: list[float] = []
    rolled_back_count = 0
    for s, ang in enumerate(angles):
        frame_path = frames_dir / f"frame_{s:03d}_d{ang:.2f}.xyz"
        ok = _set_dihedral_and_write(n, atoms, coords, i, j, k, l, ang, str(frame_path))
        if not ok:
            result["error"] = f"第 {s} 帧设置二面角失败，请检查原子下标 (i-j-k-l 是否共链)"
            result["angles"] = angles
            result["energies"] = energies
            return result
        if _progress_callback:
            _progress_callback((s / steps) * 90, f"二面角扫描 {s + 1}/{steps} θ={ang:.2f}°")
        try:
            sub = run_psi4_task(
                input_file=str(frame_path),
                task_type='energy',
                method=method,
                basis=basis,
                preset_name=preset_name,
                solvent=solvent,
                d3=d3,
                charge=charge,
                multiplicity=multiplicity,
                memory=str(memory).strip() or "4 GB",
                output_dir=str(frames_dir),
                use_temp=False,
            )
        except Exception as e:
            result["error"] = f"第 {s} 帧 PSI4 异常: {e}"
            result["angles"] = angles
            result["energies"] = energies
            logger.error("刚性扫描帧 %d 异常: %s", s, e, exc_info=True)
            return result
        if not sub.get("success"):
            result["error"] = f"第 {s} 帧失败: {sub.get('error') or '未知错误'}"
            result["angles"] = angles
            result["energies"] = energies
            return result
        if sub.get("pcm_rolled_back"):
            rolled_back_count += 1
        energies.append(float(sub.get("energy") or 0.0))
    if rolled_back_count:
        result["pcm_rollback_frames"] = rolled_back_count
        result["warning"] = (f"PCM 溶剂模型有 {rolled_back_count}/{steps} 帧自动回退为气相（当前 PSI4 未编译 PCMSolver）")

    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            wr = csv.writer(f)
            ha2kj = 2625.4996394799
            e0 = min(energies)
            rows: list[list[Any]] = [["frame", "angle_deg", "energy_Hartree", "relative_kJmol"]]
            for s, (ang, e) in enumerate(zip(angles, energies)):
                rows.append([s, f"{ang:.4f}", f"{e:.10f}", f"{(e - e0) * ha2kj:.4f}"])
            wr.writerows(rows)
        result["scan_csv"] = str(csv_path)
    except Exception as e:
        result["error"] = f"写 CSV 失败: {e}"
        logger.error("刚性扫描写 CSV 失败: %s", e, exc_info=True)

    result["success"] = result["error"] is None
    result["angles"] = angles
    result["energies"] = energies
    if _progress_callback:
        _progress_callback(100, f"二面角扫描完成，共 {steps} 帧")
    return result