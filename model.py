#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model - 核心业务逻辑
整合文件管理、PSI4计算、OpenBabel调用
"""
import os
import re
import csv
import shutil
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from logger import default_logger as logger
from constants import SUPPORTED_EXTS
import openbabel_utils as ob_utils
import psi4_compute as psi4_utils


class MolManagerModel:
    def __init__(self, work_dir="output"):
        self.work_dir = Path(work_dir)
        try:
            self._work_dir_resolved: Path = self.work_dir.resolve()
        except OSError:
            self._work_dir_resolved = self.work_dir
        self.mapping = {}
        self._reverse_mapping = {}
        self.history = []
        self.redo_stack: list = []
        self.log_callback = None
        self._suppress_history = False
        self._scan_cache: tuple[int, tuple, list] | None = None

    def set_log_callback(self, callback):
        self.log_callback = callback

    def _log(self, msg, level='info'):
        if self.log_callback:
            self.log_callback(msg, level)
        else:
            getattr(logger, level, logger.info)(msg)

    def set_mapping(self, mapping_dict):
        self.mapping = mapping_dict
        self._reverse_mapping = {v: k for k, v in mapping_dict.items()}

    # ---------- 映射加载 ----------
    def load_mapping_file(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        mapping = {}
        duplicate_count = 0
        with open(path, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()
        if len(lines) < 2:
            raise ValueError("映射文件为空或格式错误")
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                eng = parts[0].strip()
                chn = parts[1].strip()
                if not eng or not chn:
                    continue
                if eng in mapping:
                    duplicate_count += 1
                    continue
                mapping[eng] = chn
        self.set_mapping(mapping)
        return len(mapping), duplicate_count

    def invalidate_scan_cache(self):
        self._scan_cache = None

    def filter_files(self, entries: list[dict], keyword: str="", status: str="全部", ext: str="全部") -> list[dict]:
        result = entries
        if keyword:
            kw = keyword.lower()
            result = [
                e for e in result
                if kw in str(e.get('name', '')).lower()
                or kw in str(e.get('base', '')).lower()
                or kw in str(e.get('eng', '')).lower()
                or kw in str(e.get('chn', '')).lower()
            ]
        if status != "全部":
            result = [e for e in result if e.get('status') == status]
        if ext != "全部":
            target = "." + ext.lower()
            result = [e for e in result if e.get('ext', '').lower() == target]
        return result

    # ---------- 扫描文件 ----------
    def scan_files(self, ext_filter=None):
        wd = self.work_dir
        if not wd.exists():
            raise FileNotFoundError(f"工作目录不存在: {wd}")
        if ext_filter is None:
            ext_filter = list(SUPPORTED_EXTS)
        ext_filter = tuple(e.lower() if e.startswith('.') else '.' + e.lower() for e in ext_filter)

        try:
            wd_mtime = wd.stat().st_mtime_ns
        except OSError as e:
            logger.debug("无法读取工作目录 mtime，跳过缓存: %s", e)
            wd_mtime = 0

        cached = self._scan_cache
        if cached and cached[0] == wd_mtime and cached[1] == ext_filter:
            return cached[2]

        result: list[dict] = []
        trash_dir_name = ".trash_backup"
        mapping = self.mapping
        reverse_mapping = self._reverse_mapping
        for entry in wd.rglob('*'):
            if not entry.is_file():
                continue
            if trash_dir_name in entry.parts:
                continue
            ext = entry.suffix.lower()
            if ext not in ext_filter:
                continue
            try:
                rel = entry.relative_to(wd)
            except ValueError:
                rel = entry
            display_name = str(rel).replace('\\', '/')
            base = entry.stem
            has_chinese = '（' in base and '）' in base
            if has_chinese:
                eng, chn = base.split('（', 1)
                chn = chn.rstrip('）')
            else:
                eng, chn = base, ''

            if ext in ('.mol', '.xyz'):
                if eng in mapping:
                    mapped_chn = mapping[eng]
                    status = "✅ 已正确命名" if (has_chinese and chn == mapped_chn) else "⏳ 待重命名"
                elif base in reverse_mapping:
                    status = "⏳ 纯中文，待修复"
                else:
                    status = "❌ 无映射"
                mapped_chn_out = mapping.get(eng, '')
            else:
                status = "📄 计算文件"
                mapped_chn_out = ''

            result.append({
                'name': display_name,
                'base': base,
                'ext': ext,
                'eng': eng,
                'chn': chn,
                'has_chinese': has_chinese,
                'status': status,
                'mapped_chn': mapped_chn_out,
            })
        result.sort(key=lambda x: x['name'])

        if wd_mtime:
            self._scan_cache = (wd_mtime, ext_filter, result)
        return result

    # ---------- 生成缺失映射列表（修复版） ----------
    def generate_missing_list(self):
        files = self.scan_files(ext_filter=['.mol', '.xyz'])
        missing = set()
        for f in files:
            if f['status'] == "❌ 无映射":
                if f['eng']:
                    missing.add(f['eng'])
        missing = sorted(missing)
        if missing:
            out_file = self.work_dir / "missing_eng_names.txt"
            with open(out_file, 'w', encoding='utf-8') as f:
                f.write("英文名\n")
                for name in missing:
                    f.write(f"{name}\n")
            self._log(f"📋 缺失列表已保存: {out_file} (共 {len(missing)} 个)", 'info')
        else:
            self._log("🎉 所有 .mol/.xyz 文件均有映射", 'success')
        return missing

    def export_missing_csv(self, csv_path: str) -> int:
        missing_eng = self.generate_missing_list()
        if isinstance(missing_eng, dict):
            missing_list = list(missing_eng.keys())
        elif isinstance(missing_eng, (list, tuple, set)):
            missing_list = list(missing_eng)
        else:
            missing_list = []
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=['english', 'chinese'])
            writer.writeheader()
            for eng in missing_list:
                writer.writerow({'english': eng, 'chinese': ''})
        return len(missing_list)

    def import_mapping_csv(self, csv_path: str, overwrite: bool=False) -> dict:
        added = 0
        skipped = 0
        errors = 0
        total_rows = 0
        with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_rows += 1
                try:
                    eng = row.get('english', '').strip()
                    chn = row.get('chinese', '').strip()
                    if not eng or not chn:
                        continue
                    if not overwrite and eng in self.mapping:
                        skipped += 1
                    else:
                        self.mapping[eng] = chn
                        added += 1
                except Exception:
                    errors += 1
        self._reverse_mapping = {v: k for k, v in self.mapping.items()}
        self.invalidate_scan_cache()
        return {"added": added, "skipped": skipped, "errors": errors, "total_rows": total_rows}

    def _strict_basename(self, name: str, allow_subdir: bool = False) -> str:
        """
        语义上是「文件名」时做严格校验，防止
          a. 绝对路径 / 盘符前缀，会让拼接后落到 work_dir 外部；
          b. .. 段导致向上穿越到 work_dir 父目录；
          c. 纯 . 或 .. 的文件名；
          d. allow_subdir=False 时出现子目录段。

        关键点：**单级文件名（allow_subdir=False）不需要 resolve**，
        否则 Windows 下 OneDrive/非 ASCII 长路径/Junction 会让
        `(work_dir / pure_filename).resolve()` 得到一个 canonicalized
        前缀不等于 `self._work_dir_resolved`，造成合法文件被误判。
        """
        if not isinstance(name, str) or not name:
            raise ValueError("文件名不能为空")
        if Path(name).is_absolute():
            raise ValueError(f"仅接受文件名或相对子目录，禁止绝对路径: {name!r}")
        # 在规范化 *之前* 先按原始分隔符拆分段，防止 normpath 把 a/../b 压缩成 b 后漏过穿越
        raw_segs: list[str] = []
        for ch in ("/", "\\"):
            if ch in name:
                raw_segs = name.replace("\\", "/").split("/")
                break
        else:
            raw_segs = [name]
        if any(seg == ".." for seg in raw_segs):
            raise ValueError(f"文件名不能包含 '..' 段（禁止向上穿越）: {name!r}")
        norm = os.path.normpath(name)
        if norm in ("", "."):
            raise ValueError(f"无效的文件名: {name!r}")
        parts = Path(norm).parts
        if not parts:
            raise ValueError(f"无效的文件名: {name!r}")
        if any(p == ".." for p in parts):
            raise ValueError(f"文件名不能包含 '..' 段（禁止向上穿越）: {name!r}")
        if any(p == "." for p in parts):
            raise ValueError(f"文件名段不能为 '.': {name!r}")
        if not allow_subdir and len(parts) != 1:
            raise ValueError(f"仅接受单级文件名，禁止子目录: {name!r}")
        if allow_subdir:
            wd_resolved = self._work_dir_resolved
            wd_norm = os.path.normpath(os.fspath(wd_resolved))
            candidate_norm = os.path.normpath(os.fspath(self.work_dir / norm))
            ok_by_norm = False
            try:
                common = os.path.commonpath([wd_norm, candidate_norm])
                ok_by_norm = os.path.normcase(common) == os.path.normcase(wd_norm)
            except (ValueError, OSError):
                ok_by_norm = False
            if not ok_by_norm:
                try:
                    candidate = (self.work_dir / norm).resolve()
                except OSError:
                    candidate = Path(candidate_norm)
                try:
                    candidate.relative_to(wd_resolved)
                except ValueError as exc:
                    raise ValueError(f"解析后位置超出工作目录范围: {name!r}") from exc
        return name


    # ---------- 命名修复 ----------
    def _plan_rename(self, file_entry, new_base: str | None, skip_reason: str | None = None):
        if skip_reason is not None:
            return ('skip', skip_reason)
        if new_base is None:
            return ('skip', '未提供新名称')
        # M-1：new_base 是「不含扩展名的文件名」，必须是单级且不允许绝对路径/..
        try:
            self._strict_basename(f"{new_base}{file_entry.get('ext', '')}")
        except ValueError as exc:
            return ('skip', f"非法的文件名 {new_base!r}: {exc}")
        new_name = f"{new_base}{file_entry['ext']}"
        old_path = self.work_dir / file_entry['name']
        parent = old_path.parent
        new_path = parent / new_name
        if old_path == new_path:
            return ('skip', None)
        if new_path.exists():
            return ('skip', f"目标文件已存在，跳过: {new_path.name}")
        return ('rename', (file_entry['name'], new_name, str(old_path), str(new_path)))

    def _execute_rename_plan(self, plans, action_label: str, history_type: str,
                             history_desc: str, dry_run: bool,
                             _filtered_changes: list[dict] | None = None):
        """
        _filtered_changes 来自预览 confirm 对话框：只有用户勾选的 changes 会进来。
        每条 change 形如 {"from": old_display, "to": new_display, ...}。
        _filtered_changes=None 表示全部执行；空 list 表示用户全部取消，直接返回 0。
        """
        if _filtered_changes is not None and len(_filtered_changes) == 0:
            return 0, 0, 0
        _ok_set: set[tuple[str, str]] | None = None
        if _filtered_changes is not None:
            _ok_set = set()
            for c in _filtered_changes:
                _ok_set.add((str(c.get("from", "")), str(c.get("to", ""))))
        success = failed = skipped = 0
        file_pairs = []
        for plan in plans:
            kind, payload = plan
            if kind == 'skip':
                if payload:
                    self._log(f"⚠️ {payload}", 'warning')
                skipped += 1
                continue
            old_display, new_display, old_str, new_str = payload
            # 如果用户筛选了 changes，用 (from,to) 精确匹配（跳过用户取消的）
            if _ok_set is not None and (str(old_display), str(new_display)) not in _ok_set:
                skipped += 1
                continue
            if dry_run:
                self._log(f"[预览] {action_label}: {old_display} -> {new_display}", 'info')
                success += 1
            else:
                try:
                    Path(old_str).rename(new_str)
                    self._log(f"✅ {action_label}: {old_display} -> {new_display}", 'success')
                    file_pairs.append((old_str, new_str))
                    success += 1
                except Exception as e:
                    self._log(f"❌ {action_label}失败 {old_display}: {e}", 'error')
                    failed += 1
        if file_pairs:
            self._add_history(history_type, file_pairs, history_desc)
        return success, failed, skipped

    def rename_by_mapping(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        plans = []
        for f in self.scan_files(ext_filter=['.mol', '.xyz']):
            if f['status'] != "⏳ 待重命名":
                continue
            eng = f['eng']
            chn = self.mapping.get(eng)
            if not chn:
                plans.append(('skip', f"跳过 {f['name']}: 映射中无此英文名 {eng}"))
                continue
            plans.append(self._plan_rename(f, f"{eng}（{chn}）"))
        return self._execute_rename_plan(plans, "重命名", "rename", "映射重命名", dry_run, _filtered_changes)

    def fix_chinese_names(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        plans = []
        for f in self.scan_files(ext_filter=['.mol', '.xyz']):
            if f['status'] != "⏳ 纯中文，待修复":
                continue
            base = f['base']
            eng = self._reverse_mapping.get(base)
            if not eng:
                plans.append(('skip', f"无法找到对应的英文名: {f['name']}"))
                continue
            plans.append(self._plan_rename(f, f"{eng}（{base}）"))
        return self._execute_rename_plan(plans, "修复", "fix", "修复中文名", dry_run, _filtered_changes)

    def fix_all_names(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        plans = []
        for f in self.scan_files(ext_filter=['.mol', '.xyz']):
            correct_name = None
            if f['has_chinese']:
                eng = f['eng']
                if eng in self.mapping:
                    correct_name = f"{eng}（{self.mapping[eng]}）"
            elif f['status'] == "⏳ 待重命名":
                eng = f['eng']
                if eng in self.mapping:
                    correct_name = f"{eng}（{self.mapping[eng]}）"
            if correct_name is None:
                plans.append(('skip', None))
                continue
            plans.append(self._plan_rename(f, correct_name))
        return self._execute_rename_plan(plans, "修正", "rename", "修复命名错误", dry_run, _filtered_changes)

    def fix_incorrect_chinese(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        plans = []
        for f in self.scan_files(ext_filter=['.mol', '.xyz']):
            if not f['has_chinese']:
                continue
            eng = f['eng']
            chn_in_file = f['chn']
            correct_base = None
            skip_reason = None
            if eng in self.mapping:
                correct_chn = self.mapping[eng]
                if chn_in_file == correct_chn:
                    plans.append(('skip', None))
                    continue
                correct_base = f"{eng}（{correct_chn}）"
            elif chn_in_file in self._reverse_mapping:
                correct_base = f"{self._reverse_mapping[chn_in_file]}（{chn_in_file}）"
            else:
                skip_reason = (f"无法处理: {f['name']} (英文名 '{eng}' 和中文名 "
                               f"'{chn_in_file}' 均不在映射中)")
            plans.append(self._plan_rename(f, correct_base, skip_reason))
        return self._execute_rename_plan(plans, "修正中文", "rename", "修正中文内容", dry_run, _filtered_changes)

    def fix_all(self, dry_run=False, *, _filtered_changes: list[dict] | None = None):
        results = {}
        prev_suppress = getattr(self, '_suppress_history', False)
        self._suppress_history = True
        try:
            self._log("🔧 步骤1: 修复纯中文文件名...", 'info')
            r1 = self.fix_chinese_names(dry_run, _filtered_changes=_filtered_changes)
            results['fix_chinese'] = r1
            self._log("🔧 步骤2: 修复命名错误...", 'info')
            r2 = self.fix_all_names(dry_run, _filtered_changes=_filtered_changes)
            results['fix_all'] = r2
            self._log("🔧 步骤3: 修正中文内容...", 'info')
            r3 = self.fix_incorrect_chinese(dry_run, _filtered_changes=_filtered_changes)
            results['fix_content'] = r3
            self._log("🔧 步骤4: 映射重命名...", 'info')
            r4 = self.rename_by_mapping(dry_run, _filtered_changes=_filtered_changes)
            results['rename'] = r4
        finally:
            self._suppress_history = prev_suppress

        total = sum(r[0] for r in [r1, r2, r3, r4])
        self._log(f"🎉 一键修复完成！共修复 {total} 个文件", 'success')

        if not dry_run and not prev_suppress:
            collected = []
            while self.history and self.history[-1]['description'] in (
                "映射重命名", "修复命名错误", "修正中文内容", "修复中文名"
            ):
                collected.insert(0, self.history.pop())
            merged_pairs = []
            for entry in collected:
                merged_pairs.extend(entry['files'])
            if merged_pairs:
                self._add_history('fix', merged_pairs, f"一键修复（{total} 个文件）")
        return results

    # ---------- 补全 mol ----------
    def supplement_mol(self, progress_callback=None):
        files = [f for f in self.work_dir.iterdir() if f.suffix.lower() == '.xyz']
        total = len(files)
        supplemented = 0
        for idx, xyz in enumerate(files):
            base = xyz.stem
            mol_path = self.work_dir / f"{base}.mol"
            if mol_path.exists():
                continue
            if progress_callback and total > 0:
                progress_callback((idx / total) * 80, f"处理: {xyz.name}")
            try:
                success, _ = ob_utils.convert_file(str(xyz), str(mol_path), 'mol')
                if success:
                    self._log(f"✅ 补全: {mol_path.name} (从 xyz 转换)", 'success')
                    supplemented += 1
                else:
                    self._log(f"❌ 转换失败 {mol_path.name}", 'error')
            except Exception as e:
                self._log(f"❌ 转换异常 {mol_path.name}: {e}", 'error')
        if progress_callback:
            progress_callback(100, "补全完成")
        self._log(f"🎉 补全完成，共 {supplemented} 个 .mol 文件", 'success')
        return supplemented

    # ---------- 整理功能 ----------
    def _move_files_with_progress(self, moves, total: int, progress_label: str,
                                  history_desc: str, progress_callback=None,
                                  *,
                                  _filtered_changes: list[dict] | None = None):
        """
        _filtered_changes 来自预览：[{"from": src_name, "to": dir/, ...}]
        传空列表表示用户全选取消（直接返回 0）；None 表示全部执行。
        额外增加：resolve 二次越界校验 + 拒绝把工作目录/.trash_backup 当源或目标。
        """
        if _filtered_changes is not None and len(_filtered_changes) == 0:
            return 0
        _ok_set: set[tuple[str, str]] | None = None
        if _filtered_changes is not None:
            _ok_set = set()
            for c in _filtered_changes:
                _ok_set.add((str(c.get("from", "")), str(c.get("to", ""))))
        moved = 0
        file_pairs = []
        processed = 0
        wd_resolved = self._work_dir_resolved
        trash = (self.work_dir / ".trash_backup").resolve(strict=False)
        for src, dst, display_rel in moves:
            if progress_callback and total > 0:
                progress_callback((processed / total) * 100, f"{progress_label}: {Path(src).name}")
            processed += 1
            src_name = Path(src).name
            if _ok_set is not None and (str(src_name), str(display_rel)) not in _ok_set:
                # 用户在预览中取消了这一项
                continue
            dst_path = Path(dst)
            # --- 越界二次校验（resolve）：理论上防万一 _strict_basename 漏网 ---
            try:
                dst_real = dst_path.parent.resolve(strict=False)
                try:
                    dst_real.relative_to(wd_resolved)
                except ValueError:
                    self._log(f"⚠️ 拒绝移动 {src_name}: 目标解析后不在工作目录中", 'warning')
                    continue
            except OSError:
                pass
            # --- 拒绝误操作 .trash_backup 本身 ---
            try:
                src_real = Path(src).resolve(strict=True)
                if src_real == trash:
                    self._log(f"⚠️ 跳过保护目录 {src_name}", 'warning')
                    continue
            except OSError:
                pass
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if dst_path.exists():
                self._log(f"⚠️ 跳过 {Path(src).name}: 目标已存在", 'warning')
                continue
            try:
                shutil.move(str(src), str(dst))
                self._log(f"📁 移动: {Path(src).name} -> {display_rel}", 'info')
                file_pairs.append((str(src), str(dst)))
                moved += 1
            except Exception as e:
                self._log(f"❌ 移动失败 {Path(src).name}: {e}", 'error')
        if file_pairs:
            self._add_history('move', file_pairs, history_desc)
        return moved

    def organize_by_type(self, progress_callback=None, *, _filtered_changes: list[dict] | None = None):
        ext_map = {
            '.mol': 'mol_files',
            '.xyz': 'xyz_files',
            '.fchk': 'fchk_files',
            '.out': 'out_files',
            '.inp': 'inp_files',
        }
        moves = []
        for entry in self.work_dir.iterdir():
            if not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if ext not in ext_map:
                continue
            # M-1：ext_map[ext] 语义上是子目录名，严格校验后再拼接
            try:
                self._strict_basename(ext_map[ext], allow_subdir=False)
            except ValueError as exc:
                self._log(f"⚠️  跳过按类型整理 {entry.name}: 目录名非法（{exc}）", 'warning')
                continue
            dest_dir = self.work_dir / ext_map[ext]
            dst = dest_dir / entry.name
            moves.append((str(entry), str(dst), f"{ext_map[ext]}/{entry.name}"))
        total = len(moves)
        moved = self._move_files_with_progress(
            moves, total, "移动", "按类型整理", progress_callback,
            _filtered_changes=_filtered_changes
        )
        if progress_callback:
            progress_callback(100, "整理完成")
        return moved

    def organize_by_basename(self, progress_callback=None, *, _filtered_changes: list[dict] | None = None):
        groups = {}
        for entry in self.work_dir.iterdir():
            if not entry.is_file():
                continue
            groups.setdefault(entry.stem, []).append(entry)
        moves = []
        for base, entries in groups.items():
            # M-1：stem 作为子目录名，严格校验（stem 可能含非法字符或碰巧是绝对路径/..）
            try:
                self._strict_basename(base, allow_subdir=False)
            except ValueError as exc:
                for entry in entries:
                    self._log(f"⚠️  跳过按 stem 整理 {entry.name}: 目录名非法（{exc}）", 'warning')
                continue
            dest_dir = self.work_dir / base
            for entry in entries:
                dst = dest_dir / entry.name
                moves.append((str(entry), str(dst), f"{base}/{entry.name}"))
        total = len(moves)
        moved = self._move_files_with_progress(
            moves, total, "分组", "按文件名分组", progress_callback,
            _filtered_changes=_filtered_changes
        )
        if progress_callback:
            progress_callback(100, "分组完成")
        return moved


    def prefix_rename(self, prefix, file_list, dry_run=False):
        if not prefix:
            raise ValueError("前缀不能为空")
        if not file_list:
            raise ValueError("文件列表为空")
        has_placeholder = bool(re.search(r'\{[a-zA-Z_]+\}', prefix))
        desc_cache = {}
        date_str = datetime.now().strftime("%Y%m%d")
        renamed = 0
        file_pairs = []

        def _get_desc(path_str):
            if path_str in desc_cache:
                return desc_cache[path_str]
            result = self.calculate_descriptors(path_str)
            desc = {}
            if result and result.get('success') and result.get('descriptors'):
                desc = result['descriptors']
            desc_cache[path_str] = desc
            return desc

        def _fmt_num(val, digits):
            try:
                if val is None or val == '':
                    return 'N/A'
                return f"{round(float(val), digits):.{digits}f}"
            except Exception:
                return 'N/A'

        def _fmt_int(val):
            try:
                if val is None or val == '':
                    return 'N/A'
                return str(int(val))
            except Exception:
                return 'N/A'

        def _render_prefix(f, full_path):
            result = prefix
            if not has_placeholder:
                return result
            desc = _get_desc(str(full_path))
            replacements = {
                'stem': f['base'],
                'ext': f['ext'].lstrip('.'),
                'date': date_str,
                'mw': _fmt_num(desc.get('molecular_weight'), 1),
                'logP': _fmt_num(desc.get('logP'), 2),
                'tpsa': _fmt_num(desc.get('tpsa'), 1),
                'hbd': _fmt_int(desc.get('hbd')),
                'hba': _fmt_int(desc.get('hba')),
                'rotors': _fmt_int(desc.get('rotors')),
                'rings': _fmt_int(desc.get('rings')),
                'atoms': _fmt_int(desc.get('heavy_atoms')),
            }
            for key, val in replacements.items():
                result = result.replace('{' + key + '}', str(val))
            return result

        for idx, f in enumerate(sorted(file_list, key=lambda x: x['name']), 1):
            try:
                self._strict_basename(f['name'])
            except ValueError as exc:
                self._log(f"⚠️  跳过 {f['name']}: 原始名称非法（{exc}）", 'warning')
                continue
            old_path = self.work_dir / f['name']
            rendered = _render_prefix(f, old_path)
            if rendered and rendered[-1] not in ('_', '-'):
                rendered += '_'
            base_stem = f"{rendered}{idx:03d}"
            new_name = f"{base_stem}{f['ext']}"
            # M-1：new_name 必须是严格的文件名（单级 + 无 .. + 解析后在 work_dir 内）
            try:
                self._strict_basename(new_name)
            except ValueError as exc:
                self._log(f"⚠️  跳过 {f['name']}: 生成的新文件名非法（{exc}）", 'warning')
                continue
            new_path = self.work_dir / new_name
            final_new_path = new_path
            if not dry_run:
                counter = 1
                while final_new_path.exists():
                    new_name = f"{base_stem}_{counter}{f['ext']}"
                    try:
                        self._strict_basename(new_name)
                    except ValueError:
                        counter += 1
                        continue
                    final_new_path = self.work_dir / new_name
                    counter += 1
                    if counter > 10000:
                        break
            if final_new_path.exists() and not dry_run:
                self._log(f"⚠️ 跳过 {f['name']}: {new_name} 已存在", 'warning')
                continue
            if dry_run:
                self._log(f"[预览] 重命名 {f['name']} -> {new_name}", 'info')
                renamed += 1
            else:
                try:
                    old_path.rename(final_new_path)
                    self._log(f"✅ 重命名: {f['name']} -> {new_name}", 'success')
                    file_pairs.append((str(old_path), str(final_new_path)))
                    renamed += 1
                except Exception as e:
                    self._log(f"❌ 重命名失败 {f['name']}: {e}", 'error')
        if file_pairs:
            self._add_history('rename', file_pairs, f"前缀重命名 '{prefix}'")
        return renamed

    # ---------- 批量删除（可撤销：移到备份目录） ----------
    def _trash_dir(self) -> Path:
        d = self.work_dir / ".trash_backup"
        d.mkdir(exist_ok=True)
        return d

    def delete_files(self, filenames: List[str], *, _filtered_names: List[str] | None = None):
        """
        删除文件（移动到 .trash_backup）。
        _filtered_names: 预览确认后用户保留的文件子集（只对这些真删）；None = 全部。
        """
        if not filenames:
            return 0, []
        filenames = list(filenames)
        if _filtered_names is not None:
            allowed = set(_filtered_names)
            filenames = [x for x in filenames if x in allowed]
            if not filenames:
                return 0, []
        trash = self._trash_dir()
        deleted = 0
        errors = []
        file_pairs = []
        wd_resolved = self._work_dir_resolved
        # 保护 .trash_backup 目录本身（路径解析相同就拒绝，避免删 trash 目录下的备份）
        trash_resolved = trash.resolve(strict=False)
        for name in filenames:
            try:
                self._strict_basename(name, allow_subdir=False)
            except ValueError as exc:
                errors.append(f"非法文件名 {name!r}: {exc}")
                continue
            src = self.work_dir / name
            if not src.exists():
                errors.append(f"文件不存在: {name}")
                continue
            try:
                src_real = src.resolve(strict=True)
                src_real.relative_to(wd_resolved)
            except (OSError, ValueError):
                errors.append(f"文件解析后不在工作目录中，拒绝删除: {name}")
                continue
            # M15：保护 .trash_backup 本身 & 其下所有文件（用户误选备份不允许再删）
            try:
                if src_real == trash_resolved:
                    errors.append(f"拒绝删除保护目录: {name}")
                    continue
                trash_resolved.relative_to(src_real)  # 如果 src 是 trash 的父级，会抛
                errors.append(f"拒绝删除回收站保护路径: {name}")
                continue
            except ValueError:
                # 正常情况：src 既不是 trash 也不是 trash 的父级
                pass
            try:
                # 也禁止删除 trash 目录内的现有备份文件（防止"撤销"被破坏）
                src_rel_tp = src_real.relative_to(trash_resolved)
                # 能走到这里，说明文件在 trash 里
                errors.append(f"跳过回收站内部文件: {name}")
                continue
            except ValueError:
                # 正常：不在 trash 内，继续
                pass
            if src.is_symlink() or not src_real.is_file():
                errors.append(f"仅删除工作目录中的真实文件，跳过: {name}")
                continue
            dst = trash / name
            counter = 1
            while dst.exists():
                stem, ext = src.stem, src.suffix
                dst = trash / f"{stem}_{counter}{ext}"
                counter += 1
            try:
                shutil.move(str(src), str(dst))
                self._log(f"🗑️ 删除（已备份）: {name}", 'info')
                file_pairs.append((str(src), str(dst)))
                deleted += 1
            except Exception as e:
                errors.append(f"删除失败 {name}: {e}")
        if file_pairs:
            self._add_history('delete', file_pairs, f"删除文件 ({deleted} 个)")
        return deleted, errors

    # ---------- 删除重复文件 ----------
    def remove_duplicate_files(self, ext_list=None, progress_callback=None):
        if ext_list is None:
            ext_list = list(SUPPORTED_EXTS)
        files_to_check = [p for p in self.work_dir.iterdir() if p.suffix.lower() in ext_list]
        if not files_to_check:
            self._log("📂 没有找到需要检查的文件", 'info')
            return 0, []
        hash_map = {}
        errors = []
        total = len(files_to_check)
        for idx, path in enumerate(files_to_check):
            if progress_callback and total > 0:
                progress_callback((idx / total) * 80, f"扫描: {path.name}")
            try:
                with open(path, 'rb') as f:
                    file_hash = hashlib.md5(f.read()).hexdigest()
                hash_map.setdefault(file_hash, []).append(str(path))
            except Exception as e:
                errors.append(f"无法读取 {path.name}: {e}")
        duplicates_found = 0
        deleted = 0
        for hash_val, file_list in hash_map.items():
            if len(file_list) <= 1:
                continue
            duplicates_found += len(file_list) - 1
            file_list.sort()
            for path in file_list[1:]:
                try:
                    Path(path).unlink()
                    self._log(f"🗑️ 删除重复文件: {Path(path).name}", 'info')
                    deleted += 1
                except Exception as e:
                    errors.append(f"删除失败 {Path(path).name}: {e}")
        if progress_callback:
            progress_callback(100, "清理完成")
        self._log(f"✅ 重复文件清理完成：发现 {duplicates_found} 个重复副本，已删除 {deleted} 个", 'success')
        return deleted, errors

    # ---------- 历史记录 ----------
    def _add_history(self, op_type, file_pairs, description=''):
        if getattr(self, '_suppress_history', False):
            return
        if not file_pairs:
            return
        self.history.append({
            'type': op_type,
            'files': file_pairs,
            'description': description or f"{op_type} ({len(file_pairs)} 个文件)"
        })
        self.redo_stack.clear()
        self._log(f"📝 已记录历史: {self.history[-1]['description']}", 'info')
        self.invalidate_scan_cache()

    def undo_last(self):
        if not self.history:
            self._log("⚠️ 没有可撤销的操作", 'warning')
            return False
        entry = self.history[-1]
        self.redo_stack.append(entry)
        self.history.pop()
        op_type = entry['type']
        file_pairs = entry['files']
        success_count = error_count = 0
        if op_type in ('rename', 'move', 'fix'):
            for src, dst in file_pairs:
                try:
                    if Path(dst).exists():
                        if Path(src).exists():
                            self._log(f"⚠️ 撤销跳过 {Path(dst).name}: 原位置已存在文件", 'warning')
                            error_count += 1
                            continue
                        Path(dst).rename(src)
                        self._log(f"↩️ 撤销: {Path(dst).name} -> {Path(src).name}", 'info')
                        success_count += 1
                    else:
                        self._log(f"⚠️ 撤销失败: 目标文件不存在 {dst}", 'warning')
                        error_count += 1
                except Exception as e:
                    self._log(f"❌ 撤销失败 {Path(dst).name}: {e}", 'error')
                    error_count += 1
        elif op_type == 'delete':
            for src, dst in file_pairs:
                try:
                    if Path(dst).exists():
                        if Path(src).exists():
                            self._log(f"⚠️ 恢复跳过 {Path(src).name}: 原位置已存在文件", 'warning')
                            error_count += 1
                            continue
                        Path(dst).rename(src)
                        self._log(f"↩️ 恢复文件: {Path(src).name}", 'info')
                        success_count += 1
                    else:
                        self._log(f"⚠️ 恢复失败: 备份不存在 {dst}", 'warning')
                        error_count += 1
                except Exception as e:
                    self._log(f"❌ 恢复失败 {Path(src).name}: {e}", 'error')
                    error_count += 1
        else:
            self._log(f"❌ 不支持撤销的操作类型: {op_type}", 'error')
        self._log(f"🔁 撤销完成: 成功 {success_count}, 失败 {error_count}", 'info' if error_count==0 else 'warning')
        return success_count > 0

    def redo_last(self):
        if not self.redo_stack:
            self._log("⚠️ 没有可重做的操作", 'warning')
            return {'success_count': 0, 'error_count': 0}
        entry = self.redo_stack.pop()
        op_type = entry['type']
        file_pairs = entry['files']
        success_count = error_count = 0
        if op_type in ('rename', 'move', 'fix'):
            for src, dst in file_pairs:
                try:
                    if Path(src).exists():
                        if Path(dst).exists():
                            self._log(f"⚠️ 重做跳过 {Path(src).name}: 目标位置已存在文件", 'warning')
                            error_count += 1
                            continue
                        Path(src).rename(dst)
                        self._log(f"↪️ 重做: {Path(src).name} -> {Path(dst).name}", 'info')
                        success_count += 1
                    else:
                        self._log(f"⚠️ 重做失败: 源文件不存在 {src}", 'warning')
                        error_count += 1
                except Exception as e:
                    self._log(f"❌ 重做失败 {Path(src).name}: {e}", 'error')
                    error_count += 1
        elif op_type == 'delete':
            for src, dst in file_pairs:
                try:
                    if Path(src).exists():
                        if Path(dst).exists():
                            self._log(f"⚠️ 重做跳过 {Path(src).name}: 备份位置已存在文件", 'warning')
                            error_count += 1
                            continue
                        Path(src).rename(dst)
                        self._log(f"↪️ 重做删除: {Path(src).name}", 'info')
                        success_count += 1
                    else:
                        self._log(f"⚠️ 重做失败: 源文件不存在 {src}", 'warning')
                        error_count += 1
                except Exception as e:
                    self._log(f"❌ 重做失败 {Path(src).name}: {e}", 'error')
                    error_count += 1
        else:
            self._log(f"❌ 不支持重做的操作类型: {op_type}", 'error')
        self.history.append(entry)
        self._log(f"🔜 重做完成: 成功 {success_count}, 失败 {error_count}", 'info' if error_count==0 else 'warning')
        return {'success_count': success_count, 'error_count': error_count}

    def can_undo(self) -> bool:
        return len(self.history) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def undo_until(self, target_index: int) -> dict:
        total_success = 0
        total_error = 0
        steps = 0
        if target_index < 0:
            target_index = 0
        while len(self.history) > target_index and self.history:
            entry = self.history[-1]
            self.redo_stack.append(entry)
            self.history.pop()
            op_type = entry['type']
            file_pairs = entry['files']
            step_success = 0
            step_error = 0
            if op_type in ('rename', 'move', 'fix'):
                for src, dst in file_pairs:
                    try:
                        if Path(dst).exists():
                            if Path(src).exists():
                                step_error += 1
                                continue
                            Path(dst).rename(src)
                            step_success += 1
                        else:
                            step_error += 1
                    except Exception:
                        step_error += 1
            elif op_type == 'delete':
                for src, dst in file_pairs:
                    try:
                        if Path(dst).exists():
                            if Path(src).exists():
                                step_error += 1
                                continue
                            Path(dst).rename(src)
                            step_success += 1
                        else:
                            step_error += 1
                    except Exception:
                        step_error += 1
            total_success += step_success
            total_error += step_error
            steps += 1
        self.invalidate_scan_cache()
        return {"total_success": total_success, "total_error": total_error, "steps": steps}

    def redo_until(self, target_index: int) -> dict:
        total_success = 0
        total_error = 0
        steps = 0
        if target_index > len(self.history) + len(self.redo_stack):
            target_index = len(self.history) + len(self.redo_stack)
        while len(self.history) < target_index and self.redo_stack:
            entry = self.redo_stack.pop()
            op_type = entry['type']
            file_pairs = entry['files']
            step_success = 0
            step_error = 0
            if op_type in ('rename', 'move', 'fix'):
                for src, dst in file_pairs:
                    try:
                        if Path(src).exists():
                            if Path(dst).exists():
                                step_error += 1
                                continue
                            Path(src).rename(dst)
                            step_success += 1
                        else:
                            step_error += 1
                    except Exception:
                        step_error += 1
            elif op_type == 'delete':
                for src, dst in file_pairs:
                    try:
                        if Path(src).exists():
                            if Path(dst).exists():
                                step_error += 1
                                continue
                            Path(src).rename(dst)
                            step_success += 1
                        else:
                            step_error += 1
                    except Exception:
                        step_error += 1
            self.history.append(entry)
            total_success += step_success
            total_error += step_error
            steps += 1
        self.invalidate_scan_cache()
        return {"total_success": total_success, "total_error": total_error, "steps": steps}

    def get_history_snapshot(self) -> list[dict]:
        result = []
        for idx, entry in enumerate(self.history):
            result.append({
                "idx": idx,
                "type": entry.get("type", ""),
                "description": entry.get("description", ""),
                "file_count": len(entry.get("files", []))
            })
        return result

    def get_redo_snapshot(self) -> list[dict]:
        result = []
        start_idx = len(self.history)
        for i, entry in enumerate(self.redo_stack):
            result.append({
                "idx": start_idx + i,
                "type": entry.get("type", ""),
                "description": entry.get("description", ""),
                "file_count": len(entry.get("files", []))
            })
        return result

    # ---------- PSI4 计算代理 ----------
    def run_linear_scan(self, reactant_files, product_files, steps=20, method='b3lyp', basis='6-31g*',
                        output_dir=None, preset_name=None, solvent=None, d3=False,
                        charge=0, multiplicity=1, progress_callback=None):
        return psi4_utils.run_linear_scan(
            reactant_files, product_files, steps, method, basis, output_dir,
            preset_name, solvent, d3, charge, multiplicity,
            _progress_callback=progress_callback
        )

    def run_rigid_scan(self, input_file, scan_atoms, distance_range, method='b3lyp', basis='6-31g*',
                       output_dir=None, preset_name=None, solvent=None, d3=False,
                       charge=0, multiplicity=1, progress_callback=None):
        """
        刚性扫描（固定原子对距离）
        :param input_file: 输入文件路径
        :param scan_atoms: 元组 (atom1_index, atom2_index) 0-based
        :param distance_range: 元组 (start, end, steps)
        :param method: 计算方法
        :param basis: 基组
        :param output_dir: 输出目录
        :param preset_name: 预设名称
        :param solvent: 溶剂
        :param d3: DFT-D3
        :param charge: 电荷
        :param multiplicity: 多重度
        :param progress_callback: 进度回调
        :return: 结果字典
        """
        return psi4_utils.run_rigid_scan(
            input_file, scan_atoms, distance_range, method, basis, output_dir,
            preset_name, solvent, d3, charge, multiplicity,
            _progress_callback=progress_callback
        )

    def run_psi4_task(self, input_file, task_type='energy', method='b3lyp', basis='6-31g*',
                      output_dir=None, preset_name=None, solvent=None, d3=False,
                      charge=0, multiplicity=1, progress_callback=None):
        return psi4_utils.run_psi4_task(
            input_file, task_type, method, basis, output_dir, preset_name,
            solvent, d3, charge, multiplicity, _progress_callback=progress_callback
        )

    # ---------- OpenBabel 工具代理 ----------
    def convert_file(self, input_path, output_path, output_format):
        return ob_utils.convert_file(input_path, output_path, output_format)

    def generate_from_smiles(self, smiles, output_prefix, generate_3d=True, optimize=True):
        return ob_utils.generate_from_smiles(smiles, output_prefix, str(self.work_dir), generate_3d, optimize)

    def optimize_geometry(self, input_path, output_path, forcefield='mmff94'):
        return ob_utils.optimize_geometry(input_path, output_path, forcefield)

    def calculate_descriptors(self, input_path):
        return ob_utils.calculate_descriptors(input_path)

    def align_molecules(self, ref_path, mobile_path, output_path):
        return ob_utils.align_molecules(ref_path, mobile_path, output_path)

    def render_png_2d(self, input_name, width=800, height=600):
        input_path = (self.work_dir / input_name).resolve()
        preview_dir = (self.work_dir / ".preview").resolve()
        preview_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(input_name).stem
        output_path = (preview_dir / f"{stem}.png").resolve()
        return ob_utils.render_png_2d(str(input_path), str(output_path), width, height)

    def _read_summary_json(self, path: Path) -> dict:
        import json
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def collect_results(self) -> list[dict]:
        import json
        rows = []
        if not self.work_dir.exists():
            return rows
        for summary_json in self.work_dir.rglob("*_summary.json"):
            try:
                summary_dir = summary_json.parent
                base_with_suffix = summary_json.stem
                base = base_with_suffix[:-len("_summary")] if base_with_suffix.endswith("_summary") else base_with_suffix

                log_path = summary_dir / f"{base}.log"
                fchk_path = summary_dir / f"{base}.fchk"
                optxyz_path = summary_dir / f"{base}_opt.xyz"

                data = self._read_summary_json(summary_json)
                task_type = data.get("task_type", "")
                method = data.get("method", "")
                basis = data.get("basis", "")
                energy = data.get("energy")
                success = data.get("success", False)

                extra = {}
                if log_path.exists():
                    try:
                        extra_data = psi4_utils.parse_psi4_output(str(log_path), task_type)
                        if extra_data:
                            for k, v in extra_data.items():
                                if k not in ("energy", "optimized_xyz") and v is not None:
                                    extra[k] = v
                    except Exception:
                        pass

                row = {
                    "base": base,
                    "task_type": task_type,
                    "method": method,
                    "basis": basis,
                    "energy_Ha": energy,
                    "success": bool(success),
                    "log": str(log_path) if log_path.exists() else "",
                    "fchk": str(fchk_path) if fchk_path.exists() else "",
                    "opt_xyz": str(optxyz_path) if optxyz_path.exists() else "",
                    "summary": str(summary_json),
                    **extra
                }
                rows.append(row)
            except Exception:
                continue

        rows.sort(key=lambda r: (r.get("base", ""), r.get("task_type", "")))
        return rows

    def compute_deltas(self, rows: list[dict], operation: str) -> list[dict]:
        HA_TO_KJ = 2625.4996
        HA_TO_KCAL = 627.5095
        results = []
        if len(rows) < 2:
            return results

        def get_e(r):
            v = r.get("energy_Ha")
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        if operation == "A-B（单分子差）":
            C = rows[0]
            A = rows[1]
            c_base = C.get("base", "C")
            a_base = A.get("base", "A")
            delta_ha = get_e(C) - get_e(A)
            delta_kj = delta_ha * HA_TO_KJ
            delta_kcal = delta_ha * HA_TO_KCAL
            label = f"{c_base} - {a_base}"
            comment = "ΔE = E(C) - E(A)"
            results.append({
                "label": label,
                "delta_Ha": delta_ha,
                "delta_kJ": delta_kj,
                "delta_kcal": delta_kcal,
                "comment": comment
            })

        elif operation == "C - A - B（反应/结合能）":
            if len(rows) >= 3:
                C = rows[0]
                A = rows[1]
                B = rows[2]
                c_base = C.get("base", "C")
                a_base = A.get("base", "A")
                b_base = B.get("base", "B")
                delta_ha = get_e(C) - get_e(A) - get_e(B)
                delta_kj = delta_ha * HA_TO_KJ
                delta_kcal = delta_ha * HA_TO_KCAL
                label = f"{c_base} - {a_base} - {b_base}"
                comment = "ΔE = E(C) - E(A) - E(B)"
                results.append({
                    "label": label,
                    "delta_Ha": delta_ha,
                    "delta_kJ": delta_kj,
                    "delta_kcal": delta_kcal,
                    "comment": comment
                })
        return results

    def _file_signature(self, path: Path) -> Tuple[int, float]:
        try:
            st = path.stat()
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return (-1, 0.0)

    def compare_directories(self, left: str | Path, right: str | Path) -> dict:
        left_path = Path(left)
        right_path = Path(right)
        left_files: Dict[str, Tuple[int, float]] = {}
        right_files: Dict[str, Tuple[int, float]] = {}
        only_left: list[dict] = []
        only_right: list[dict] = []
        diff_content: list[dict] = []
        if left_path.is_dir():
            for entry in left_path.iterdir():
                if entry.is_file():
                    size, mtime = self._file_signature(entry)
                    left_files[entry.name] = (size, mtime)
        if right_path.is_dir():
            for entry in right_path.iterdir():
                if entry.is_file():
                    size, mtime = self._file_signature(entry)
                    right_files[entry.name] = (size, mtime)
        left_names = set(left_files.keys())
        right_names = set(right_files.keys())
        for name in left_names - right_names:
            size, mtime = left_files[name]
            only_left.append({"name": name, "size": size, "mtime": mtime})
        for name in right_names - left_names:
            size, mtime = right_files[name]
            only_right.append({"name": name, "size": size, "mtime": mtime})
        for name in left_names & right_names:
            ls, lm = left_files[name]
            rs, rm = right_files[name]
            if ls != rs or lm != rm:
                diff_content.append({
                    "name": name,
                    "left_size": ls,
                    "left_mtime": lm,
                    "right_size": rs,
                    "right_mtime": rm
                })
        only_left.sort(key=lambda x: x["name"])
        only_right.sort(key=lambda x: x["name"])
        diff_content.sort(key=lambda x: x["name"])
        return {
            "only_left": only_left,
            "only_right": only_right,
            "diff_content": diff_content
        }

    def copy_from_left_to_right(self, names: list[str], left, right):
        left_path = Path(left)
        right_path = Path(right)
        right_path.mkdir(parents=True, exist_ok=True)
        success = 0
        errors: list[str] = []
        for name in names:
            src = left_path / name
            dst = right_path / name
            try:
                shutil.copy2(str(src), str(dst))
                self._log(f"✅ 复制: {name} (左→右)", "success")
                success += 1
            except Exception as e:
                self._log(f"❌ 复制失败 {name}: {e}", "error")
                errors.append(str(e))
        return success, errors

    def copy_from_right_to_left(self, names: list[str], left, right):
        left_path = Path(left)
        right_path = Path(right)
        left_path.mkdir(parents=True, exist_ok=True)
        success = 0
        errors: list[str] = []
        for name in names:
            src = right_path / name
            dst = left_path / name
            try:
                shutil.copy2(str(src), str(dst))
                self._log(f"✅ 复制: {name} (右→左)", "success")
                success += 1
            except Exception as e:
                self._log(f"❌ 复制失败 {name}: {e}", "error")
                errors.append(str(e))
        return success, errors

    def sync_overwrite_left_to_right(self, names: list[str], left, right):
        left_path = Path(left)
        right_path = Path(right)
        right_path.mkdir(parents=True, exist_ok=True)
        success = 0
        errors: list[str] = []
        for name in names:
            src = left_path / name
            dst = right_path / name
            try:
                shutil.copy2(str(src), str(dst))
                self._log(f"🔁 覆盖: {name} (左→右)", "success")
                success += 1
            except Exception as e:
                self._log(f"❌ 覆盖失败 {name}: {e}", "error")
                errors.append(str(e))
        return success, errors

    def sync_overwrite_right_to_left(self, names: list[str], left, right):
        left_path = Path(left)
        right_path = Path(right)
        left_path.mkdir(parents=True, exist_ok=True)
        success = 0
        errors: list[str] = []
        for name in names:
            src = right_path / name
            dst = left_path / name
            try:
                shutil.copy2(str(src), str(dst))
                self._log(f"🔁 覆盖: {name} (右→左)", "success")
                success += 1
            except Exception as e:
                self._log(f"❌ 覆盖失败 {name}: {e}", "error")
                errors.append(str(e))
        return success, errors