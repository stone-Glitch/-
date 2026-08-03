#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSI4 核心模块 - run_psi4_task, check_psi4_installed, 基础辅助函数
"""
import os
import re
import json
import csv
import shutil
import subprocess
import tempfile
import logging  # ← 添加这一行！
from pathlib import Path
from typing import Dict, Optional, Callable, Any, List, Tuple

from logger import default_logger as logger, performance_timer
from constants import PSI4_PRESETS
from path_utils import secure_output_path, default_base_dir_from_input
import openbabel_utils as ob_utils

# ---------- NumPy 兼容性补丁 ----------
try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _np = None
    _HAS_NUMPY = False

def _apply_numpy_cumproduct_compat_patch() -> None:
    if not _HAS_NUMPY:
        return
    if hasattr(_np, "cumproduct"):
        return
    from logger import default_logger as _log
    try:
        _np.cumproduct = _np.cumprod
        _log.warning(
            "检测到 numpy ≥ 1.24 且 pint 较旧：已临时为 numpy 补上 cumproduct=cumprod 别名，"
            "避免 PSI4 导入崩溃。建议运行 `conda install -c conda-forge pint=0.24` "
            "或 `pip install --upgrade pint` 以彻底解决此问题。"
        )
    except Exception as _e:
        try:
            _log.debug("应用 numpy cumproduct 兼容性补丁时发生非致命错误: %s", _e)
        except Exception:
            import sys as _sys
            print(f"[compat] numpy cumproduct 补丁非致命错误: {_e}", file=_sys.stderr)

try:
    import psi4
except Exception as _psi4_first_import_err:
    try:
        from logger import default_logger as _log
        _log.warning("PSI4 首次导入失败（可能是 numpy/pint 不兼容），尝试应用兼容性补丁后重试: %s",
                     _psi4_first_import_err)
    except Exception:
        import sys as _sys
        print(f"[psi4_import] 首次导入失败：{_psi4_first_import_err}", file=_sys.stderr)
    _apply_numpy_cumproduct_compat_patch()
    try:
        import psi4
    except Exception as _psi4_second_err:
        try:
            from logger import default_logger as _log2
            _log2.warning("PSI4 第二次导入仍失败，将标记为不可用: %s", _psi4_second_err)
        except Exception:
            import sys as _sys
            print(f"[psi4_import] 第二次导入仍失败：{_psi4_second_err}", file=_sys.stderr)
        psi4 = None
else:
    _apply_numpy_cumproduct_compat_patch()

# ---------- 缓存 ----------
_XYZ_READ_CACHE: dict[tuple[str, int, int], str | None] = {}
_XYZ_READ_CACHE_MAX = 512


def _xyz_cache_key(path_str: str) -> tuple[str, int, int] | None:
    try:
        st = os.stat(path_str)
        return (os.fspath(Path(path_str).resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


# ---------- 环境检测 ----------
def check_psi4_installed() -> Tuple[bool, str, Dict[str, Any]]:
    """
    增强版 PSI4 安装与功能支持检测
    返回 (可用性, 消息, 详情字典)
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
        return False, "PSI4 未安装或导入失败", details

    try:
        details["version"] = str(getattr(psi4, "__version__", None) or
                                  getattr(psi4.core, "version", lambda: "unknown")())
    except Exception as _ve:
        logger.debug("PSI4 版本探测失败: %s", _ve)

    for attr, key in (("energy", "has_energy"),
                      ("optimize", "has_optimize"),
                      ("frequency", "has_frequency")):
        details[key] = callable(getattr(psi4, attr, None))

    details["has_cphf_nmr"] = callable(getattr(psi4, "cphf", None))
    if not details["has_cphf_nmr"]:
        wl.append("PSI4 编译时未启用 CPHF 模块，¹H NMR 模拟将自动降级为经验化学位移库")

    try:
        details["has_pcm"] = callable(getattr(psi4.core, "set_local_option", None))
    except Exception:
        details["has_pcm"] = False

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
    ok, _, _ = check_psi4_installed()
    return ok


def get_preset_info(preset_name: str) -> Dict:
    return PSI4_PRESETS.get(preset_name, {})


def sanitize_filename(name: str) -> str:
    illegal_chars = r'[\\/:*?"<>|]'
    return re.sub(illegal_chars, '_', name)


# ---------- 子进程运行 ----------
def _run_process_with_timeout(
    args: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float = 300.0,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> int:
    """安全地运行子进程"""
    if not args:
        raise ValueError("_run_process_with_timeout: args 不能为空")
    arg0 = str(args[0])
    resolved: str | None = None

    def _validate_abs_path(path_str: str) -> str | None:
        try:
            p = Path(path_str)
            if not p.is_absolute():
                return None
            rp = p.resolve(strict=True)
            import tempfile as _tempfile
            unsafe_roots = []
            for _cand in (_tempfile.gettempdir(), os.getcwd()):
                try:
                    unsafe_roots.append(Path(_cand).resolve(strict=False))
                except Exception:
                    continue
            for root in unsafe_roots:
                try:
                    rp.relative_to(root)
                    logger.warning("拒绝执行在可写目录下的可执行文件: %s", rp)
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
        try:
            if arg0 in ("obabel", "obabel.exe"):
                try:
                    resolved_exe = ob_utils._resolve_obabel_cli()
                    if resolved_exe:
                        validated2 = _validate_abs_path(resolved_exe)
                        if validated2 is not None:
                            resolved = validated2
                            args = [resolved] + list(args[1:])
                except Exception:
                    pass
            if resolved is None:
                w = shutil.which(arg0)
                if w:
                    validated3 = _validate_abs_path(w)
                    if validated3 is not None:
                        resolved = validated3
                        args = [resolved] + list(args[1:])
        except Exception:
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
        logger.error("子进程可执行文件不存在: %s", arg0)
        return 127
    except OSError as e:
        logger.error("子进程启动失败 args=%s: %s", args, e)
        return 126


# ---------- OpenBabel 转换 ----------
def convert_with_obabel(input_file: str, output_file: str) -> bool:
    try:
        res = ob_utils.convert_file(input_file, output_file, os.path.splitext(output_file)[1][1:] or 'xyz')
        success = res.get("success", False)
        output_path = res.get("output_path")
        ok = success and output_path and os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if not ok:
            logger.debug("OpenBabel 转换失败 %s → %s", input_file, output_file)
        return ok
    except Exception as e:
        logger.warning("OpenBabel 转换异常 %s → %s: %s", input_file, output_file, e)
        return False


# ---------- 读取 XYZ ----------
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
        except OSError:
            break
    if content is None:
        try:
            with open(file_path, 'rb') as f:
                raw = f.read()
                content = raw.decode('utf-8', errors='replace')
        except OSError:
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
            coord_lines = []
            _atom_re = re.compile(r'^[A-Za-z][a-z]?$')
            for line in lines[2:]:
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                    except ValueError:
                        continue
                    atom = parts[0]
                    if _atom_re.match(atom):
                        coord_lines.append((atom, x, y, z))
            if not coord_lines:
                result = None
            else:
                n = len(coord_lines)
                out_lines = [str(n), "Converted by OpenBabel"]
                out_lines.extend([f"{a:2s}  {x:12.6f}  {y:12.6f}  {z:12.6f}" for (a, x, y, z) in coord_lines])
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


# ---------- 解析 PSI4 输出 ----------
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


# ---------- run_psi4_task ----------
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

    # TempDirGuard
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
            self.path = existing_path

        def register_extra(self, p: str) -> None:
            if p and os.path.exists(p) and p not in self.extra_paths:
                self.extra_paths.append(p)

        def release(self) -> None:
            if not self.active:
                return
            self.active = False
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
                    logger.debug("TempDirGuard 清理主临时目录失败 %s: %s", p, _re)

    _td = _TempDirGuard()

    def _finalize():
        _td.release()
        try:
            psi4.core.clean()
        except Exception as _ce:
            logger.debug("PSI4 core.clean() 失败: %s", _ce)

    try:
        input_path = Path(input_file)
        has_non_ascii = any(ord(c) > 127 for c in str(input_path.resolve()))
        print(f"路径检测: has_non_ascii = {has_non_ascii}, 路径 = {input_file}")

        _base_dir = default_base_dir_from_input(input_file)
        try:
            _raw_orig_dir = output_dir if output_dir is not None else str(input_path.parent)
            _safe_orig = secure_output_path(
                _raw_orig_dir,
                is_dir=True,
                base_dir=_base_dir,
                create_parent=True,
                allow_outside=False,
            )
            original_output_dir = str(_safe_orig)
        except ValueError as _v:
            return {"success": False, "error": f"输出目录非法: {_v}"}
        output_dir = None

        use_temp: bool = False
        temp_dir: str | None = None

        def _switch_to_temp_dir() -> str:
            nonlocal use_temp, temp_dir, output_dir
            if temp_dir is None:
                td = _td.acquire(prefix="psi4_temp_")
                temp_dir = td
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
            if output_dir is None:
                output_dir = original_output_dir
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as m_err:
                raise RuntimeError(f"无法创建 PSI4 输出目录: {output_dir}") from m_err

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
            work = Path(full_temp_dir) if full_temp_dir else real_input_path.parent
            converted_xyz = os.fspath(work / "molecule.xyz")
            if full_temp_dir is None and not real_input_path.suffix.lower() == ".xyz":
                _td.register_extra(converted_xyz)
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

            if mol is None:
                return {"success": False, "error": "未能构建分子"}
            try:
                mol.set_molecular_charge(charge)
            except AttributeError:
                try:
                    mol.set_charge(charge)
                except Exception as _ch:
                    logger.debug("设置分子电荷失败 q=%d: %s", charge, _ch)
            try:
                mol.set_multiplicity(multiplicity)
            except AttributeError as _ma:
                logger.debug("设置分子多重度失败 mult=%d: %s", multiplicity, _ma)
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

            psi4.set_output_file(log_file, append=False)

            psi4.set_memory(memory)
            psi4.set_options({
                'basis': basis,
                'scf_type': 'pk',
                'e_convergence': 1e-8,
                'd_convergence': 1e-8,
            })
            if extra_options:
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
                            logger.debug("PCM 局部选项设置失败: %s", _pcm_opts_err)
                        _pcm_enabled_here = True
                    except Exception as _pcm_err:
                        logger.warning("启用 PCM 隐式溶剂失败: %s", _pcm_err)
                        try:
                            psi4.set_options({'pcm': False, 'solvent': solvent})
                        except Exception:
                            pass
                else:
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

            # 执行任务
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

            # 高级扩展：用户自定义 post hook
            if results["success"] and extra_post_hook is not None:
                try:
                    _hook_ret = extra_post_hook(wfn, mol, method)
                    if isinstance(_hook_ret, dict):
                        results.setdefault("hook", {}).update(_hook_ret)
                except Exception as _hook_err:
                    logger.warning("extra_post_hook 执行失败：%s", _hook_err)
                    results["hook_error"] = str(_hook_err)

            # P1 波函数属性
            if results["success"] and wfn is not None:
                try:
                    props: dict[str, Any] = {}
                    try:
                        na_list = wfn.nalpha()
                        eps_a = wfn.epsilon_a()
                        if eps_a is not None:
                            eps_a = eps_a.to_array()
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
                    except Exception as _e_hl:
                        logger.debug("取 HOMO/LUMO 失败: %s", _e_hl)

                    try:
                        psi4.oeprop(wfn, "MULLIKEN_CHARGES", "LOWDIN_CHARGES", "DIPOLE")
                        try:
                            mu_x = float(psi4.core.variable("DIPOLE X"))
                            mu_y = float(psi4.core.variable("DIPOLE Y"))
                            mu_z = float(psi4.core.variable("DIPOLE Z"))
                            mu_tot = (mu_x ** 2 + mu_y ** 2 + mu_z ** 2) ** 0.5
                            props["dipole"] = {"x_D": mu_x, "y_D": mu_y, "z_D": mu_z, "total_D": mu_tot}
                        except Exception as _e_d:
                            logger.debug("取偶极矩失败: %s", _e_d)
                    except Exception as _e_prop:
                        logger.debug("oeprop 属性计算失败: %s", _e_prop)

                    if props:
                        results["properties"] = props
                except Exception as _e_p1:
                    logger.debug("P1 波函数属性提取整体失败: %s", _e_p1)

            # P3 IR 光谱
            ir_png: str | None = None
            ir_csv: str | None = None
            if results["success"] and results.get("frequencies") and output_prefix:
                try:
                    ir_csv = output_prefix + "_ir_spectrum.csv"
                    ir_png = output_prefix + "_ir_spectrum.png"
                    freqs = list(results["frequencies"])
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
                    with open(ir_csv, "w", encoding="utf-8", newline="") as _f:
                        _wr = csv.writer(_f)
                        _wr.writerow(["wavenumber_cm-1", "intensity_km/mol"])
                        for fv, iv in zip(freqs, intensities):
                            _wr.writerow([fv, iv])
                    _plot_ir(freqs, intensities, ir_png)
                    results["ir_csv"] = ir_csv
                    results["ir_png"] = ir_png
                    results["output_files"].extend([ir_csv, ir_png])
                except Exception as _e_p3:
                    logger.debug("P3 IR 光谱生成失败: %s", _e_p3)

            # P2 cubeprop
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
                    for p in cube_out_dir.iterdir():
                        if p.suffix.lower() == ".cube":
                            cube_files.append(str(p))
                    results["cube_dir"] = str(cube_out_dir)
                    results["cube_files"] = cube_files
                    results["output_files"].extend(cube_files)
                except Exception as _e_p2:
                    logger.debug("P2 cubeprop 失败: %s", _e_p2)

            # 保存结果
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

            # 复制结果到原目录
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