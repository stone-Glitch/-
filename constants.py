#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
常量定义
"""
from typing import Any, Dict, List

PSI4_PRESETS = {
    "快速 (HF/STO-3G)": {"method": "hf", "basis": "sto-3g"},
    "标准 (B3LYP/6-31G*)": {"method": "b3lyp", "basis": "6-31g*"},
    "标准 (B3LYP/def2-SVP)": {"method": "b3lyp", "basis": "def2-svp"},
    "高精度 (MP2/cc-pVTZ)": {"method": "mp2", "basis": "cc-pvtz"},
    "高精度 (CCSD/cc-pVDZ)": {"method": "ccsd", "basis": "cc-pvdz"},
    "DFT-D3 (B3LYP-D3/def2-TZVP)": {"method": "b3lyp", "basis": "def2-tzvp", "d3": True},
    "溶剂效应 (PCM-水/B3LYP/6-31G*)": {"method": "b3lyp", "basis": "6-31g*", "solvent": "water"},
}

PSI4_TASKS = {
    "energy": "单点能",
    "optimize": "几何优化",
    "frequency": "频率分析",
    "scan": "势能面扫描",
    "ts": "过渡态搜索",
    "excited": "激发态",
    "sapt": "SAPT 相互作用",
    "thermo": "热化学分析",
}

PSI4_UNSUPPORTED_TASKS = frozenset()

RUN_PRESETS: dict[str, dict[str, Any]] = {
    "快速（力场，不走 PSI4，仅 OpenBabel 优化）": {"task_type": "_ff_optimize", "method": "mmff94", "basis": "", "preset_name": "快速（力场）", "solvent": None, "d3": False, "memory_gb": 1},
    "标准（B3LYP/6-31G*）": {"task_type": "optimize", "method": "b3lyp", "basis": "6-31g*", "preset_name": "标准（B3LYP/6-31G*）", "solvent": None, "d3": False, "memory_gb": 4},
    "高精度（M062X/def2-TZVP + D3）": {"task_type": "optimize", "method": "m062x", "basis": "def2-tzvp", "preset_name": "高精度（M062X/def2-TZVP+D3）", "solvent": None, "d3": True, "memory_gb": 8},
    "高精度单点（DLPNO-CCSD(T)/cc-pVTZ）": {"task_type": "energy", "method": "ccsd(t)", "basis": "cc-pvtz", "preset_name": "高精度单点（CCSD(T)/cc-pVTZ）", "solvent": None, "d3": False, "memory_gb": 16},
    "溶剂化水相（SMD-water/B3LYP/6-31G*）": {"task_type": "optimize", "method": "b3lyp", "basis": "6-31g*", "preset_name": "水溶液（SMD/B3LYP/6-31G*）", "solvent": "water", "d3": False, "memory_gb": 4},
}

SUPPORTED_EXTS = {'.mol', '.xyz', '.fchk', '.out', '.inp'}
COLORS = {
    "info": "black",
    "success": "green",
    "warning": "orange",
    "error": "red",
}

__all__ = [
    "SUPPORTED_EXTS",
    "PSI4_PRESETS",
    "PSI4_TASKS",
    "PSI4_UNSUPPORTED_TASKS",
    "RUN_PRESETS",
    "COLORS",
]