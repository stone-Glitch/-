#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PSI4 计算模块 - 路由
保持原 psi4_compute 所有函数接口不变。
"""
from chem.psi4.core import (
    check_psi4_installed,
    check_psi4_installed_simple,
    get_preset_info,
    sanitize_filename,
    convert_with_obabel,
    read_xyz_content,
    run_psi4_task,
    parse_psi4_output,
    _run_process_with_timeout,
)
from chem.psi4.scans import run_linear_scan, run_rigid_scan
from chem.psi4.conformer import conformer_search_ensemble
from chem.psi4.irc import run_irc_task, _parse_irc_trajectory_from_log
from chem.psi4.thermo import run_reaction_energy_profile, eyring_kinetics
from chem.psi4.pka import run_pka_prediction
from chem.psi4.nmr import run_nmr_simulation
from chem.psi4.utils import (
    _parse_xyz,
    _write_xyz,
    _lerp_coords,
    _plot_ir,
    _set_dihedral_and_write,
)

__all__ = [
    # 核心
    "check_psi4_installed",
    "check_psi4_installed_simple",
    "get_preset_info",
    "sanitize_filename",
    "convert_with_obabel",
    "read_xyz_content",
    "run_psi4_task",
    "parse_psi4_output",
    "_run_process_with_timeout",
    # 扫描
    "run_linear_scan",
    "run_rigid_scan",
    # 构象
    "conformer_search_ensemble",
    # IRC
    "run_irc_task",
    "_parse_irc_trajectory_from_log",
    # 热化学
    "run_reaction_energy_profile",
    "eyring_kinetics",
    # pKa
    "run_pka_prediction",
    # NMR
    "run_nmr_simulation",
    # 工具
    "_parse_xyz",
    "_write_xyz",
    "_lerp_coords",
    "_plot_ir",
    "_set_dihedral_and_write",
]