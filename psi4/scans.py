#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSI4 扫描模块 - 线性插值扫描和刚性二面角扫描
"""
import os
import csv
import shutil
import tempfile
import logging  # ← 添加这一行！
from pathlib import Path
from typing import Any

from logger import default_logger as logger, performance_timer
from path_utils import secure_output_path, default_base_dir_from_input
from .core import run_psi4_task, read_xyz_content, sanitize_filename
from .utils import _parse_xyz, _write_xyz, _lerp_coords, _set_dihedral_and_write


@performance_timer(name="psi4.run_linear_scan", level=logging.DEBUG, min_ms=200.0)
def run_linear_scan(reactant_files, product_files, steps=20, method='b3lyp', basis='6-31g*',
                    output_dir=None, preset_name=None, solvent=None, d3=False,
                    charge=0, multiplicity=1, memory='4 GB', _progress_callback=None):
    """真实的线性扫描：反应物/产物各取第一个文件，XYZ 坐标线性插值 N 帧，每帧跑 PSI4 单点能"""
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
            result["error"] = f"原子数不一致：反应物 {n_r} vs 产物 {n_p}"
            return result
        if atoms_r != atoms_p:
            result["error"] = "原子种类或顺序不一致：请对齐原子编号"
            return result
    except Exception as e:
        result["error"] = f"读取初始结构失败: {e}"
        logger.debug("线性扫描读取结构失败: %s", e)
        return result

    steps = max(2, int(steps))
    try:
        _base_dir = default_base_dir_from_input(reactant_files[0] if reactant_files else None,
                                                fallback=product_files[0] if product_files else None)
        _raw_out = output_dir if output_dir is not None else str(Path(reactant_files[0]).parent / "scan_output")
        out_root = secure_output_path(
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
        frames_dir = secure_output_path(
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
        result["warning"] = (f"PCM 溶剂模型有 {rolled_back_count}/{steps} 帧自动回退为气相")

    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            wr = csv.writer(f)
            ha2kj = 2625.4996394799
            e0 = energies[0]
            rows = [["frame", "t", "energy_Hartree", "energy_kJmol"]]
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


@performance_timer(name="psi4.run_rigid_scan", level=logging.DEBUG, min_ms=200.0)
def run_rigid_scan(input_file, scan_atoms, distance_range, method='b3lyp', basis='6-31g*',
                   output_dir=None, preset_name=None, solvent=None, d3=False,
                   charge=0, multiplicity=1, memory='4 GB', _progress_callback=None):
    """二面角刚性扫描：固定 (i,j,k,l) 四个原子的二面角，在 [start_deg,end_deg] 线性扫 N 个角度，逐帧 PSI4 单点能"""
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
        import subprocess as _sp_check
        import sys as _sys
        exe = ob_utils._resolve_obabel_cli()
        if _sys.platform == "win32":
            si = _sp_check.STARTUPINFO()
            si.dwFlags |= _sp_check.STARTF_USESHOWWINDOW
            kw = {'startupinfo': si, 'creationflags': _sp_check.CREATE_NO_WINDOW}
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
    try:
        _base_dir = default_base_dir_from_input(input_file)
        _raw_out = output_dir if output_dir is not None else str(Path(input_file).parent / "rigid_scan_output")
        out_root = secure_output_path(
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
        frames_dir = secure_output_path(
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
        result["warning"] = (f"PCM 溶剂模型有 {rolled_back_count}/{steps} 帧自动回退为气相")

    try:
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            wr = csv.writer(f)
            ha2kj = 2625.4996394799
            e0 = min(energies)
            rows = [["frame", "angle_deg", "energy_Hartree", "relative_kJmol"]]
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