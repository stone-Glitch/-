#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
常量定义 - 集中管理硬编码字符串和数值，避免散落在各模块中
"""
from typing import Any, Dict, List, Tuple

# ============================================================
# 默认力场与溶剂（供 OpenBabel / PSI4 通用）
# ============================================================
DEFAULT_FORCEFIELD: str = "mmff94"
SUPPORTED_FORCEFIELDS: Tuple[str, ...] = ("mmff94", "uff")
DEFAULT_SOLVENT: str = "water"
COMMON_SOLVENTS: Tuple[str, ...] = (
    "water", "methanol", "ethanol", "acetone", "thf",
    "acetonitrile", "dichloromethane", "toluene", "dimethyl sulfoxide",
)

# ============================================================
# OpenBabel 命令行超时（秒） - 可配置，大分子场景可调大
# ============================================================
OB_DEFAULT_TIMEOUT_SEC: int = 60      # 普通转换/优化默认 60s
OB_LARGE_TIMEOUT_SEC: int = 300      # 大分子/质子化等较重操作 300s
OB_CONVERT_TIMEOUT_SEC: int = 30     # 轻量格式转换 30s
OB_PNG_TIMEOUT_SEC: int = 120        # PNG 2D 渲染可能较慢
OB_VERSION_TIMEOUT_SEC: int = 2      # 版本号探测 2s
OB_PROPLIST_TIMEOUT_SEC: int = 5     # 格式列表探测 5s

# ============================================================
# PSI4 超时（秒）
# ============================================================
PSI4_DEFAULT_PROCESS_TIMEOUT: float = 300.0  # 通用子进程 5 分钟

# ============================================================
# OpenBabel 常用输入格式（用于未知扩展名时的 fallback 探测）
# ============================================================
COMMON_INPUT_FORMATS: Tuple[str, ...] = (
    "xyz", "mol", "mol2", "smi", "sdf", "cml", "pdb", "inchi", "cif",
)

# ============================================================
# 常用原子量（元素→平均原子量），纯 Python 元素分析兜底用
# ============================================================
ATOMIC_WEIGHTS: Dict[str, float] = {
    "H": 1.00794, "He": 4.002602, "Li": 6.941, "Be": 9.012182, "B": 10.811,
    "C": 12.0107, "N": 14.0067, "O": 15.9994, "F": 18.9984032, "Ne": 20.1797,
    "Na": 22.989770, "Mg": 24.3050, "Al": 26.981538, "Si": 28.0855, "P": 30.973762,
    "S": 32.065, "Cl": 35.453, "Ar": 39.948, "K": 39.0983, "Ca": 40.078,
    "Fe": 55.845, "Cu": 63.546, "Zn": 65.38, "Br": 79.904, "I": 126.90447,
}

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
    "快速（力场，不走 PSI4，仅 OpenBabel 优化）": {
        "task_type": "_ff_optimize", "method": DEFAULT_FORCEFIELD, "basis": "",
        "preset_name": "快速（力场）", "solvent": None, "d3": False, "memory_gb": 1,
    },
    "标准（B3LYP/6-31G*）": {
        "task_type": "optimize", "method": "b3lyp", "basis": "6-31g*",
        "preset_name": "标准（B3LYP/6-31G*）", "solvent": None, "d3": False, "memory_gb": 4,
    },
    "高精度（M062X/def2-TZVP + D3）": {
        "task_type": "optimize", "method": "m062x", "basis": "def2-tzvp",
        "preset_name": "高精度（M062X/def2-TZVP+D3）", "solvent": None, "d3": True, "memory_gb": 8,
    },
    "高精度单点（DLPNO-CCSD(T)/cc-pVTZ）": {
        "task_type": "energy", "method": "ccsd(t)", "basis": "cc-pvtz",
        "preset_name": "高精度单点（CCSD(T)/cc-pVTZ）", "solvent": None, "d3": False, "memory_gb": 16,
    },
    "溶剂化水相（SMD-water/B3LYP/6-31G*）": {
        "task_type": "optimize", "method": "b3lyp", "basis": "6-31g*",
        "preset_name": "水溶液（SMD/B3LYP/6-31G*）", "solvent": DEFAULT_SOLVENT, "d3": False, "memory_gb": 4,
    },
}

SUPPORTED_EXTS = {'.mol', '.xyz', '.fchk', '.out', '.inp'}
COLORS = {
    "info": "black",
    "success": "green",
    "warning": "orange",
    "error": "red",
}

__all__ = [
    # 通用默认值
    "DEFAULT_FORCEFIELD", "SUPPORTED_FORCEFIELDS",
    "DEFAULT_SOLVENT", "COMMON_SOLVENTS",
    # OpenBabel 超时
    "OB_DEFAULT_TIMEOUT_SEC", "OB_LARGE_TIMEOUT_SEC",
    "OB_CONVERT_TIMEOUT_SEC", "OB_PNG_TIMEOUT_SEC",
    "OB_VERSION_TIMEOUT_SEC", "OB_PROPLIST_TIMEOUT_SEC",
    # PSI4 超时
    "PSI4_DEFAULT_PROCESS_TIMEOUT",
    # 格式/元素常量
    "COMMON_INPUT_FORMATS", "ATOMIC_WEIGHTS",
    # 已有常量
    "SUPPORTED_EXTS",
    "PSI4_PRESETS", "PSI4_TASKS", "PSI4_UNSUPPORTED_TASKS",
    "RUN_PRESETS", "COLORS",
]