"""cli 层单测：日志重定向到文件（避免污染非全屏 TUI）。"""

from __future__ import annotations

import contextlib
import io
import logging

from novacode.cli import _setup_logging


def test_setup_logging_redirects_to_file(tmp_path):
    """日志重定向到 .novacode/logs/novacode.log，stderr 不再接收（TUI 不被打扰）。"""
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        _setup_logging(str(tmp_path))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            # 用自有 logger（propagate 到 root），避免 LOG015 对 root.warning 的告警
            logging.getLogger("novacode.test").warning("测试日志消息")
        log_file = tmp_path / ".novacode" / "logs" / "novacode.log"
        assert log_file.is_file()
        text = log_file.read_text(encoding="utf-8")
        assert "测试日志消息" in text
        # 关键：warning 进文件而非 stderr（修复前会打 stderr 污染输入框）
        assert "测试日志消息" not in err.getvalue()
    finally:
        # 清理本次添加的 handler，避免污染其他测试
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
