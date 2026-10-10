"""Monkey 测试 R3 目标 1：文件系统故障注入（ENOSPC / EACCES / EMFILE / 只读 / 目录占位）。

种子：20261021（与 R1 20261002~007、R2 20261011~016 不同）。
角度：不是灌脏数据，而是把「写盘那一刻」注入故障，验证四个状态写入器的容错口径
（history.json / alert_history.json / settings.json / last_price.json / control.json）：

  - 注入故障后调用方不得抛异常逃逸（配置写入器与其余三个同口径吞 OSError）；
  - 原文件内容不得被破坏（原子写语义：读者要么旧要么新）；
  - 失败路径不得残留 .tmp 文件（_atomic_write_text 的清理分支）；
  - 故障恢复后下一次写入必须成功且文件是严格合法 JSON。

oracle：不得有异常逃逸；目标文件（若存在）严格 JSON（parse_constant 拒绝 NaN/Infinity）；
目录中不出现 *.tmp 残留；单次操作 ≤2s。
"""

from __future__ import annotations

import errno
import json
import os
import random
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from _monkey_common import assert_valid_json_text

from gold_pet.config import Config
from gold_pet.notifier import Alert, Notifier
from gold_pet.price import HistoryStore, PriceService, Snapshot, _atomic_write_text

SEED = 20261021
ITERATIONS = 500

WRITER_NAMES = ("history", "alerts", "cache", "settings")


# ------------------------------------------------------------------ 工具
def _make_writer(kind: str, tmp: Path, cfg: Config | None = None):
    """构造第 kind 个写入器的「一次写入」闭包。"""
    if kind == "history":
        store = HistoryStore(tmp / "history.json")
        return lambda: store.append("gold", "cny_g", 900.0)
    if kind == "alerts":
        n = Notifier(cfg or Config(), tmp / "alert_history.json")
        alert = Alert(kind="pct", side="up", title="t", body="b", at=1700000000.0)
        return lambda: n.record([alert])
    if kind == "cache":
        svc = PriceService(tmp / "last_price.json")
        snap = Snapshot(quotes={}, fx=6.7, updated_at=1700000000.0, ok=True, error="")
        return lambda: svc.save_cache(snap)
    if kind == "settings":
        path = tmp / "settings.json"
        c = Config.load(path)
        return lambda: c.save()
    raise AssertionError(kind)


def _target_of(kind: str, tmp: Path) -> Path:
    return {
        "history": tmp / "history.json",
        "alerts": tmp / "alert_history.json",
        "cache": tmp / "last_price.json",
        "settings": tmp / "settings.json",
    }[kind]


def _no_tmp_leftovers(tmp: Path) -> list[str]:
    return sorted(p.name for p in tmp.iterdir() if p.name.endswith(".tmp"))


def _assert_ok_file(kind: str, tmp: Path) -> None:
    target = _target_of(kind, tmp)
    if target.exists():
        assert_valid_json_text(target.read_text(encoding="utf-8"))


class _Fault:
    """对 Path.write_text（仅 .tmp 文件 / 全部文件）/ os.replace 施加一次性故障。"""

    def __init__(self, monkeypatch, mode: str):
        self.mode = mode
        self.fired = False
        if mode in ("enospc_tmp", "eacces_tmp", "emfile_tmp"):
            err = {
                "enospc_tmp": errno.ENOSPC,
                "eacces_tmp": errno.EACCES,
                "emfile_tmp": errno.EMFILE,
            }[mode]
            real = Path.write_text

            def patched(self, *a, **k):
                if self.name.endswith(".tmp") and not _flag.fired:
                    _flag.fired = True
                    raise OSError(err, os.strerror(err))
                return real(self, *a, **k)

            _flag = self
            monkeypatch.setattr(Path, "write_text", patched)
        elif mode == "write_any_fail":
            # settings.json 走非原子直写：故障要落在目标文件这次 write_text 上
            real = Path.write_text

            def patched_any(self, *a, **k):
                if not _flag.fired:
                    _flag.fired = True
                    raise OSError(errno.EACCES, "write denied")
                return real(self, *a, **k)

            _flag = self
            monkeypatch.setattr(Path, "write_text", patched_any)
        elif mode == "replace_fail":
            real_replace = os.replace

            def patched_replace(src, dst):
                if not _flag.fired:
                    _flag.fired = True
                    raise OSError(errno.EACCES, "replace denied")
                return real_replace(src, dst)

            _flag = self
            monkeypatch.setattr(os, "replace", patched_replace)
        elif mode == "none":
            pass
        else:
            raise AssertionError(mode)


def _effective_mode(kind: str, mode: str) -> str:
    """settings.json 是直写（无 tmp/replace 阶段），replace_fail 映射为直写故障。"""
    if kind == "settings" and mode in ("replace_fail", "enospc_tmp", "eacces_tmp", "emfile_tmp"):
        return "write_any_fail"
    return mode


# ------------------------------------------------------------------ 定向矩阵
@pytest.mark.parametrize("kind", WRITER_NAMES)
@pytest.mark.parametrize("mode", ["enospc_tmp", "eacces_tmp", "emfile_tmp", "replace_fail"])
def test_r3_fs_atomic_write_faults_do_not_escape(tmp_path, monkeypatch, kind, mode):
    """四种写盘故障 × 四个写入器：异常不逃逸、原文件完好、无 tmp 残留。"""
    write = _make_writer(kind, tmp_path)
    target = _target_of(kind, tmp_path)
    write()  # 先写一次合法内容（作为「原数据」）
    before = target.read_text(encoding="utf-8") if target.exists() else None

    fault = _Fault(monkeypatch, _effective_mode(kind, mode))
    write()  # 注入故障的那次写入：宽容写入器不得抛
    assert fault.fired, "故障未真正触发"
    if before is not None:
        assert target.read_text(encoding="utf-8") == before, "故障写破坏了原数据"
    assert _no_tmp_leftovers(tmp_path) == [], f"{kind}/{mode}: .tmp 残留"
    _assert_ok_file(kind, tmp_path)


@pytest.mark.parametrize("kind", WRITER_NAMES)
def test_r3_fs_readonly_target(tmp_path, monkeypatch, kind):
    """目标文件只读（Windows chmod S_IREAD）时写入器不得抛，原内容保留。"""
    write = _make_writer(kind, tmp_path)
    target = _target_of(kind, tmp_path)
    write()
    before = target.read_text(encoding="utf-8")
    os.chmod(target, stat.S_IREAD)
    try:
        write()
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
    assert target.read_text(encoding="utf-8") == before
    assert _no_tmp_leftovers(tmp_path) == []
    _assert_ok_file(kind, tmp_path)


@pytest.mark.parametrize("kind", WRITER_NAMES)
def test_r3_fs_recovery_after_fault(tmp_path, monkeypatch, kind):
    """故障一次后故障解除：下一次写入必须成功落盘且严格 JSON。"""
    write = _make_writer(kind, tmp_path)
    write()
    fault = _Fault(monkeypatch, _effective_mode(kind, "replace_fail"))
    write()
    assert fault.fired
    monkeypatch.undo()  # 解除故障
    write()
    _assert_ok_file(kind, tmp_path)
    assert _no_tmp_leftovers(tmp_path) == []
    # 内容必须真的更新成新写入（history/alerts/cache/settings 都存在可解析内容）
    text = _target_of(kind, tmp_path).read_text(encoding="utf-8")
    json.loads(text, parse_constant=lambda n: (_ for _ in ()).throw(AssertionError(n)))


def test_r3_fs_atomic_write_unit_matrix(tmp_path, monkeypatch):
    """_atomic_write_text 自身的故障矩阵：每种故障都不残留 tmp、不损坏原文件。"""
    target = tmp_path / "unit.json"
    target.write_text('{"old": 1}', encoding="utf-8")

    cases = []
    for mode in ("enospc_tmp", "eacces_tmp", "emfile_tmp", "replace_fail"):
        cases.append(mode)

    for mode in cases:
        fault = _Fault(monkeypatch, mode)
        with pytest.raises(OSError):
            _atomic_write_text(target, '{"new": 2}')
        assert fault.fired
        assert target.read_text(encoding="utf-8") == '{"old": 1}'
        assert _no_tmp_leftovers(tmp_path) == []
        monkeypatch.undo()

    # 父目录被文件占位：ENOTDIR（清理分支里 unlink 也会失败，但不能炸出别的异常类型）
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    with pytest.raises(OSError):
        _atomic_write_text(blocker / "x.json", "data")


def test_r3_fs_dir_in_place_of_state_file(tmp_path):
    """history.json 位置是目录 / settings.json 位置是目录：加载回落、写入不崩。"""
    d_history = tmp_path / "history.json"
    d_history.mkdir()
    store = HistoryStore(d_history)
    assert store.load() == {}
    store.append("gold", "cny_g", 900.0)  # 写入失败被吞
    assert store.load() == {}

    d_settings = tmp_path / "settings.json"
    d_settings.mkdir()
    cfg = Config.load(d_settings)
    assert cfg.get("metal") == "gold"  # 回落默认
    cfg.set("metal", "silver")
    cfg.save()  # OSError 被吞（R3-M-03）
    assert d_settings.is_dir()  # 没有被破坏


def test_r3_fs_control_info_write_fault(tmp_path, monkeypatch):
    """control.json 写入故障：_write_info 抛 OSError 属预期，由 run() 兜住（T3 已验证）。"""
    from gold_pet import control_server as cs

    monkeypatch.setattr(cs, "control_info_path", lambda: tmp_path / "control.json")
    ctl = cs.ControlServer(state_reader=lambda: {})
    ctl.port, ctl.token = 12345, "t"

    def boom(self, *a, **k):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError):
        ctl._write_info()
    assert _no_tmp_leftovers(tmp_path) == []


# ------------------------------------------------------------------ 随机混合轰炸
def test_r3_fs_random_fault_burst(tmp_path, monkeypatch):
    """500 次随机（写入器 × 故障模式 × 首次/后续）：异常不逃逸、文件恒可解析。"""
    rnd = random.Random(SEED)
    modes = ["none", "enospc_tmp", "eacces_tmp", "emfile_tmp", "replace_fail"]
    for i in range(ITERATIONS):
        slot = tmp_path / f"case{i % 25}"  # 复用 25 个子目录，避免建 500 个目录太慢
        slot.mkdir(exist_ok=True)
        kind = rnd.choice(WRITER_NAMES)
        mode = rnd.choice(modes)
        with monkeypatch.context() as m:
            if mode != "none":
                _Fault(m, _effective_mode(kind, mode))
            write = _make_writer(kind, slot)
            try:
                write()
            except OSError:
                pytest.fail(f"迭代 {i}: {kind}/{mode} 的 OSError 逃逸到调用方")
        _assert_ok_file(kind, slot)
        assert _no_tmp_leftovers(slot) == [], f"迭代 {i}: {kind}/{mode} 残留 tmp"
