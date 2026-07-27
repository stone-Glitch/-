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


def _cache_key(path_str: str) -> tuple[str, int, int] | None:
    try:
        st = os.stat(path_str)
        return (os.fspath(Path(path_str).resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


# ======================== 子进程包装（安全、跨平台） ========================
_OBABEL_CLI_EXE: str | None = None


def _resolve_obabel_cli() -> str:
    """
    安全解析 obabel 命令行可执行文件的绝对路径，
    避免相对名 + PATH 搜索导致的本地可执行文件劫持（B607/CWE-426）。
    解析结果缓存，避免每次调用重复 which；
    加锁保护 _OBABEL_CLI_EXE 单例初始化（多线程竞态下重复 which 可能不一致）。
    """
    import shutil as _shutil
    global _OBABEL_CLI_EXE
    # 双检锁（DCL）避免频繁抢锁
    if _OBABEL_CLI_EXE is not None:
        return _OBABEL_CLI_EXE
    with _OBABEL_CLI_LOCK:
        if _OBABEL_CLI_EXE is not None:
            return _OBABEL_CLI_EXE
        resolved = _shutil.which("obabel")
        if not resolved:
            raise RuntimeError(
                "未在 PATH 中找到 obabel（OpenBabel 命令行），请安装后重试。"
                "已拒绝使用相对名 obabel 执行（防止工作目录同名恶意可执行劫持）。"
            )
        resolved_path = Path(resolved)
        try:
            resolved_path = resolved_path.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError(f"obabel 解析到的路径不存在: {resolved}") from exc
        if resolved_path.is_symlink() or not resolved_path.is_file():
            raise RuntimeError(f"obabel 路径必须是真实文件而非符号链接: {resolved_path}")
        _OBABEL_CLI_EXE = str(resolved_path)
        return _OBABEL_CLI_EXE


def _run_obabel(args: List[str], timeout: Optional[int] = None, check: bool = False) -> subprocess.CompletedProcess:
    """
    安全执行 obabel 命令，自动处理 Windows 控制台窗口隐藏。
    args[0] 应为 "obabel"（相对名占位），此函数会替换为已解析的绝对路径。
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
    if args[0] in ("obabel", "obabel.exe", str(Path("obabel"))):
        real_args.extend(args[1:])
    else:
        real_args.extend(args)
    return subprocess.run(
        real_args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        shell=False,
        **kwargs
    )


# ======================== 环境检测 ========================
def check_openbabel() -> Tuple[bool, str]:
    """
    检测 Open Babel 是否可用，优先使用 pybel 接口，其次检测命令行。
    返回 (可用性, 消息)。
    """
    # 1. 检测 pybel
    if PYBEL_AVAILABLE:
        try:
            # 尝试创建一个简单分子验证
            mol = pybel.readstring("smi", "C")
            if mol:
                return True, "pybel 接口可用"
            else:
                return False, "pybel 接口不可用（无法创建分子）"
        except Exception as e:
            # pybel 存在但有问题，降级到命令行
            warnings.warn(f"pybel 导入但不可用: {e}，尝试命令行")

    # 2. 检测命令行
    try:
        result = _run_obabel(["obabel", "-V"], timeout=2)
        if result.returncode == 0 and result.stdout.strip():
            return True, f"obabel 命令行可用 (版本: {result.stdout.strip()})"
        else:
            return False, "obabel 命令行不可用"
    except Exception as e:
        return False, f"无法运行 obabel: {e}"


def get_supported_formats() -> List[str]:
    """
    获取 Open Babel 支持的读写格式列表。
    返回格式名称列表（字符串）。
    """
    formats = set()
    if PYBEL_AVAILABLE:
        try:
            formats.update(pybel.informats.keys())
            formats.update(pybel.outformats.keys())
        except AttributeError:
            pass
    else:
        try:
            result = _run_obabel(["obabel", "-L", "formats"], timeout=5)
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    parts = line.strip().split()
                    if parts:
                        formats.add(parts[0])
        except Exception:
            pass
    return sorted(formats)


# ======================== 格式转换 ========================
_COMMON_IN_FORMATS = ("xyz", "mol", "mol2", "smi", "sdf", "cml", "pdb", "inchi", "cif")

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

    # 确保输出目录存在
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

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
            result = _run_obabel(cmd, timeout=30)
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
    forcefield: str = "mmff94"
) -> Dict[str, Any]:
    """
    从 SMILES 生成 3D 分子文件（.mol 和 .xyz）。
    返回: {'success': bool, 'message': str, 'mol': str, 'xyz': str}
    """
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

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
            result_mol = _run_obabel(cmd_mol, timeout=60)
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
def optimize_geometry(input_path: str, output_path: str, forcefield: str = "mmff94") -> Dict[str, Any]:
    """
    使用 Open Babel 力场优化分子结构。
    返回: {'success': bool, 'message': str, 'output_path': str}
    """
    # 确保输出目录存在
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

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
            result = _run_obabel(cmd, timeout=60)
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
                    except Exception:
                        pass
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
                    except OSError:
                        pass
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
        except Exception:
            pass
        try:
            mw_exact = float(obmol.GetExactMass()) if hasattr(obmol, "GetExactMass") else 0.0
        except Exception:
            pass
        try:
            mw_avg = float(obmol.GetMolWt()) if hasattr(obmol, "GetMolWt") else 0.0
        except Exception:
            pass
        try:
            atoms_iter = obmol.GetAtoms() if hasattr(obmol, "GetAtoms") else list(mol.atoms)
        except Exception:
            atoms_iter = list(mol.atoms)
        atomic_weights: dict[str, float] = {
            "H": 1.00794, "He": 4.002602, "Li": 6.941, "Be": 9.012182, "B": 10.811,
            "C": 12.0107, "N": 14.0067, "O": 15.9994, "F": 18.9984032, "Ne": 20.1797,
            "Na": 22.989770, "Mg": 24.3050, "Al": 26.981538, "Si": 28.0855, "P": 30.973762,
            "S": 32.065, "Cl": 35.453, "Ar": 39.948, "K": 39.0983, "Ca": 40.078,
            "Fe": 55.845, "Cu": 63.546, "Zn": 65.38, "Br": 79.904, "I": 126.90447,
        }
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
        except Exception:
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
                ret[fp] = None; continue
            mol = mols[0]
            obmol = mol.OBMol
            try:
                ik = str(mol.write("inchikey")).strip().split("\n")[0]
            except Exception:
                try:
                    conv = ob.OBConversion()
                    conv.SetOutFormat("inchikey")
                    ik = conv.WriteString(obmol).strip().split("\n")[0]
                except Exception:
                    ik = ""
            if "=" in ik:
                ik = ik.split("=", 1)[1].strip()
            ret[fp] = ik or None
        except Exception:
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
        with tempfile.NamedTemporaryFile(suffix="." + (os.path.splitext(input_path)[1][1:] or "xyz"), delete=False) as _t1:
            pass
        with tempfile.NamedTemporaryFile(suffix="." + (os.path.splitext(output_path)[1][1:] or "xyz"), delete=False) as _t2:
            pass
        try:
            shutil.copy2(input_path, _t1.name)
            cmd = ["obabel", _t1.name, "-O", _t2.name, "-p", f"{ph:g}"]
            r = _run_obabel(cmd, timeout=120)
            if r.returncode != 0 or not os.path.exists(_t2.name) or os.path.getsize(_t2.name) == 0:
                return {"success": False, "message": f"obabel -p 返回码 {r.returncode}: {r.stderr[:300]}"}
            shutil.copy2(_t2.name, output_path)
            return {"success": True, "output_path": output_path, "ph": ph,
                    "message": f"已在 pH={ph:g} 下加氢：-COOH→-COO⁻、-NH2→-NH3⁺ 等"}
        finally:
            for t in (_t1.name, _t2.name):
                try: os.unlink(t)
                except OSError: pass
    except Exception as e:
        return {"success": False, "message": f"pH 加氢失败：{e}"}


# ======================== O8：SDF 拆分/合并 ========================
def split_multi_sdf(input_sdf: str, out_dir: str, prefix: str = "mol", format_ext: str = "xyz") -> Dict[str, Any]:
    """把一个 SDF（或任何多分子文件，.sdf/.mol2/.xyz 都行）拆成多个单分子文件。"""
    try:
        os.makedirs(out_dir, exist_ok=True)
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
            except Exception:
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
            except Exception:
                continue
        if not all_mols:
            return {"success": False, "message": "未读取到任何分子"}
        out_dir = os.path.dirname(output_sdf)
        if out_dir: os.makedirs(out_dir, exist_ok=True)
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
                        except Exception:
                            continue
            else:
                with open(tmp_name, "w", encoding="utf-8") as f:
                    for i, m in enumerate(all_mols):
                        try:
                            f.write(m.write("sdf"))
                        except Exception:
                            continue
            shutil.copy2(tmp_name, output_sdf)
        finally:
            try: os.unlink(tmp_name)
            except OSError: pass
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
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    try:
        # 使用 obabel 的 --align 选项
        cmd = ["obabel", mobile_path, "-O", output_path, "--align", ref_path]
        result = _run_obabel(cmd, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path):
            return {"success": True, "message": "叠加成功", "output_path": output_path}
        else:
            return {"success": False, "message": f"叠加失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}


def render_png_2d(input_path: str, output_path: str, width: int=800, height: int=600) -> Dict[str, Any]:
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

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
                except Exception:
                    pass

                try:
                    mol.draw(width=width, height=height, filename=output_path)
                    if os.path.exists(output_path):
                        return {"success": True, "message": "2D PNG 渲染成功（pybel.draw）", "output_path": output_path}
                except Exception:
                    pass
            except Exception:
                pass

        cmd = ["obabel", input_path, "-O", output_path, "-xS", "-xN", str(width), "-xW", str(height)]
        result = _run_obabel(cmd, timeout=60)
        if result.returncode == 0 and os.path.exists(output_path):
            return {"success": True, "message": "2D PNG 渲染成功（obabel CLI）", "output_path": output_path}
        else:
            return {"success": False, "message": f"渲染失败: {result.stderr.strip()}", "output_path": None}
    except Exception as e:
        return {"success": False, "message": str(e), "output_path": None}