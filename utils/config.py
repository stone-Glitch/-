#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块

重构说明：
  - 移除重复的 _app_data_dir() / _chmod_quiet()，改用 path_utils 中的统一实现
  - 保持所有外部接口不变
"""
import json
import os
from pathlib import Path

from utils.logger import default_logger as logger
from utils.path_utils import get_app_data_dir, chmod_quiet

APP_DATA_DIR = get_app_data_dir()
CONFIG_FILE = APP_DATA_DIR / "mol_manager_config.json"

DEFAULT_CONFIG = {
    "work_dir": "output",
    "mapping_file": "",
    "ext_filter": ".mol,.xyz,.fchk,.out,.inp",
    "window_geometry": "1000x750",
    "psi4_config": {
        "last_method": "b3lyp",
        "last_basis": "6-31g*",
        "last_task": "energy"
    },
    "preview_before_operation": True,
    "recent_work_dirs": [],
    # === 一、字太小：可配置字体基线（pt，默认14pt，立刻见效）===
    # 范围：10 ~ 20。用户可以直接在 mol_manager_config.json 里改。
    "font_size": 14,
    # （可选）强制与系统 DPI 一致地放大（缩放 font_size）。True=跟随DPI，False=按 pt 绝对值
    "font_follow_dpi": True,
    # === 三、OpenBabel 识别失败：用户可手动指定 obabel 可执行文件路径（绝对路径）===
    # 空串 = 自动查找（PATH / shutil.which / 常见安装位置）
    "obabel_path": "",
    # === 易用性改进新增字段 ===
    "ui_mode": "simple",                # simple / advanced
    "recent_files": [],                 # 最近使用的文件路径列表（最多10个）
    "preset_auto_load": "",             # 自动加载的预设名（空表示不自动加载）
    # first_run 由 wizard.py 管理
    "first_run": True,
}

MAX_RECENT_DIRS = 10


def _deep_merge(target: dict, defaults: dict) -> dict:
    """递归合并 defaults 到 target（target 中的键优先），返回 target。"""
    for key, def_val in defaults.items():
        cur = target.get(key)
        if isinstance(def_val, dict):
            if not isinstance(cur, dict):
                target[key] = def_val.copy()
            else:
                _deep_merge(cur, def_val)
        elif cur is None:
            target[key] = def_val
    return target


def load_config():
    try:
        if CONFIG_FILE.exists():
            # 先补一次权限（兼容旧版本创建的 0o644 文件）
            chmod_quiet(CONFIG_FILE, 0o600)
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if not isinstance(config, dict):
                    logger.warning("配置文件格式不是字典，使用默认配置")
                    return DEFAULT_CONFIG.copy()
                return _deep_merge(config, DEFAULT_CONFIG)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("加载配置文件失败，使用默认配置: %s", e)
    return DEFAULT_CONFIG.copy()


def save_config(config):
    tmp_path: Path | None = None
    try:
        # 写入方式采用「先写临时文件→重命名」+ chmod 0o600，
        # 同时避免 (a) 写一半崩溃导致配置损坏 (b) 创建后未立即 chmod 被其他用户读取
        tmp_path = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        # 先 chmod 再原子替换，防止竞态
        chmod_quiet(tmp_path, 0o600)
        if hasattr(os, 'replace'):
            os.replace(tmp_path, CONFIG_FILE)
        else:
            tmp_path.rename(CONFIG_FILE)
        chmod_quiet(CONFIG_FILE, 0o600)
    except OSError as e:
        logger.warning("保存配置文件失败: %s", e)
    finally:
        if tmp_path is not None:
            try:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "APP_DATA_DIR",
    "CONFIG_FILE",
    "DEFAULT_CONFIG",
    "load_config",
    "save_config",
]
