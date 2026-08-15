"""引擎与前四层流水线单测：黑名单短路、沙箱、规则、模式矩阵、跳层、降级、启动模式。"""

import json
from pathlib import Path

from novacode.llm import ToolCall
from novacode.permission import Category, Decision, Mode
from novacode.permission.engine import Engine, mode_fallback, new_engine
from novacode.permission.matcher import compile_matcher
from novacode.permission.rule import Rule, RuleSet


def _call(name: str, **kw) -> ToolCall:
    return ToolCall(id="id", name=name, input=json.dumps(kw))


def _r(tool: str, pattern: str, *, allow: bool) -> Rule:
    """构造一条带 matcher 的规则（ch12：Rule.pattern 已改为 Rule.matcher）。"""
    return Rule(
        tool=tool,
        matcher=compile_matcher(pattern, is_command=(tool == "Bash")),
        allow=allow,
        raw=pattern,
    )


def _engine(tmp_path, local=None, project=None, user=None, start_mode=Mode.DEFAULT) -> Engine:
    return Engine(
        root=str(tmp_path.resolve()),
        local=local or RuleSet(),
        project=project or RuleSet(),
        user=user or RuleSet(),
        local_path=str(tmp_path / ".novacode" / "settings.local.yaml"),
        start_mode=start_mode,
    )


def test_blacklist_bypass_still_deny(tmp_path):
    """黑名单是最高优先级：bypass 模式也拦 rm -rf /。"""
    e = _engine(tmp_path)
    d, reason = e.check(Mode.BYPASS, _call("bash", command="rm -rf /"), read_only=False)
    assert d == Decision.DENY and "黑名单" in reason


def test_sandbox_outside_deny(tmp_path):
    e = _engine(tmp_path)
    d, reason = e.check(Mode.BYPASS, _call("read_file", path="/etc/passwd"), read_only=True)
    assert d == Decision.DENY and "项目目录之外" in reason
    d, reason = e.check(
        Mode.BYPASS, _call("write_file", path="../evil", content="x"), read_only=False
    )
    assert d == Decision.DENY


def test_file_unparseable_deny(tmp_path):
    """文件类参数 JSON 无法解析 → 安全拒绝（不静默放行）。"""
    e = _engine(tmp_path)
    bad = ToolCall(id="id", name="read_file", input="not-json")
    d, reason = e.check(Mode.DEFAULT, bad, read_only=True)
    assert d == Decision.DENY and "无法解析" in reason


def test_mode_matrix(tmp_path):
    e = _engine(tmp_path)
    read = _call("read_file", path="a.txt")
    write = _call("write_file", path="x.txt", content="hi")
    bash = _call("bash", command="echo hi")

    # default：只读 Allow / 写·命令执行 Ask
    assert e.check(Mode.DEFAULT, read, read_only=True)[0] == Decision.ALLOW
    assert e.check(Mode.DEFAULT, write, read_only=False)[0] == Decision.ASK
    assert e.check(Mode.DEFAULT, bash, read_only=False)[0] == Decision.ASK
    # acceptEdits：文件写 Allow / 命令执行 Ask
    assert e.check(Mode.ACCEPT_EDITS, write, read_only=False)[0] == Decision.ALLOW
    assert e.check(Mode.ACCEPT_EDITS, bash, read_only=False)[0] == Decision.ASK
    # bypass：全 Allow（黑名单/沙箱除外，已另测）
    assert e.check(Mode.BYPASS, write, read_only=False)[0] == Decision.ALLOW
    assert e.check(Mode.BYPASS, bash, read_only=False)[0] == Decision.ALLOW
    # plan：写/命令执行兜底仍 Ask，只读 Allow
    assert e.check(Mode.PLAN, write, read_only=False)[0] == Decision.ASK
    assert e.check(Mode.PLAN, bash, read_only=False)[0] == Decision.ASK
    assert e.check(Mode.PLAN, read, read_only=True)[0] == Decision.ALLOW


def test_skip_layer_no_misfire(tmp_path):
    """非命令执行工具不被黑名单拦、命令执行工具不被沙箱拦，均继续后续层。"""
    e = _engine(tmp_path)
    # read_file 含 /dev 字样路径也不走黑名单（黑名单只对 EXEC 类）
    d, _ = e.check(Mode.BYPASS, _call("read_file", path="dev"), read_only=True)
    assert d == Decision.ALLOW  # 不被黑名单误拦，沙箱内放行
    # bash 命令含路径也不走沙箱
    d, _ = e.check(Mode.BYPASS, _call("bash", command="cat /etc/passwd"), read_only=False)
    assert d == Decision.ALLOW  # bash 不沙箱，bypass 放行


def test_rule_allow_short_circuit(tmp_path):
    """allow 规则命中直接放行，不进模式兜底。"""
    local = RuleSet(allow=[_r("Write", "x.txt", allow=True)])
    e = _engine(tmp_path, local=local)
    d, _ = e.check(Mode.DEFAULT, _call("write_file", path="x.txt", content="hi"), read_only=False)
    assert d == Decision.ALLOW


def test_rule_deny_wins(tmp_path):
    """deny 规则命中即 Deny（同层 deny 优先于 allow）。"""
    local = RuleSet(
        allow=[_r("Bash", "git *", allow=True)],
        deny=[_r("Bash", "git push", allow=False)],
    )
    e = _engine(tmp_path, local=local)
    d, _ = e.check(Mode.BYPASS, _call("bash", command="git push"), read_only=False)
    assert d == Decision.DENY


def test_rule_cross_layer_deny_wins(tmp_path):
    """AC5：跨层冲突——任意一层 deny 全局压过其他层 allow（层级不参与裁决）。"""
    local = RuleSet(allow=[_r("Bash", "git status", allow=True)])
    user = RuleSet(deny=[_r("Bash", "git *", allow=False)])
    e = _engine(tmp_path, local=local, user=user)
    d, _ = e.check(Mode.BYPASS, _call("bash", command="git status"), read_only=False)
    assert d == Decision.DENY  # 用户层 deny 压过本地层 allow


def test_rule_cross_layer_deny_over_allow_reversed(tmp_path):
    """任意一层的 deny 都压过其他层的 allow（反方向也成立）。"""
    project = RuleSet(deny=[_r("Bash", "git push", allow=False)])
    user = RuleSet(allow=[_r("Bash", "git *", allow=True)])
    e = _engine(tmp_path, project=project, user=user)
    d, _ = e.check(Mode.BYPASS, _call("bash", command="git push"), read_only=False)
    assert d == Decision.DENY


def test_rule_file_relative_path(tmp_path):
    """文件类规则匹配项目相对路径；Write(src/**) 放行 src 下、不放行 docs。"""
    local = RuleSet(allow=[_r("Write", "src/**", allow=True)])
    e = _engine(tmp_path, local=local)
    d, _ = e.check(
        Mode.DEFAULT, _call("write_file", path="src/a/b.py", content="x"), read_only=False
    )
    assert d == Decision.ALLOW
    d, _ = e.check(
        Mode.DEFAULT, _call("write_file", path="docs/x.md", content="x"), read_only=False
    )
    assert d == Decision.ASK  # 未命中规则 → 模式兜底 Ask


def test_mode_fallback_table():
    assert mode_fallback(Mode.DEFAULT, Category.READ) == Decision.ALLOW
    assert mode_fallback(Mode.DEFAULT, Category.WRITE) == Decision.ASK
    assert mode_fallback(Mode.DEFAULT, Category.EXEC) == Decision.ASK
    assert mode_fallback(Mode.ACCEPT_EDITS, Category.WRITE) == Decision.ALLOW
    assert mode_fallback(Mode.ACCEPT_EDITS, Category.EXEC) == Decision.ASK
    assert mode_fallback(Mode.PLAN, Category.WRITE) == Decision.ASK
    assert mode_fallback(Mode.BYPASS, Category.WRITE) == Decision.ALLOW


def test_new_engine_resolve_root_failure(tmp_path):
    """项目根不可解析 → 仍返回非 None 空规则安全引擎 + err。"""
    engine, err = new_engine(str(tmp_path / "does-not-exist"))
    assert engine is not None
    assert err is not None
    assert engine.start_mode == Mode.DEFAULT
    assert engine.local.allow == [] and engine.user.deny == []


def test_new_engine_defaults(tmp_path):
    engine, err = new_engine(str(tmp_path))
    assert err is None
    assert engine.start_mode == Mode.DEFAULT
    assert engine.root == str(tmp_path.resolve())


def test_new_engine_start_mode_priority(tmp_path, monkeypatch):
    """defaultMode 按 本地 > 项目 > 用户 生效。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (tmp_path / ".novacode").mkdir()
    # 用户层 plan、项目层 bypass、本地层 acceptEdits
    (home / ".novacode").mkdir()
    (home / ".novacode" / "settings.yaml").write_text("default_mode: plan", encoding="utf-8")
    (tmp_path / ".novacode" / "settings.yaml").write_text(
        "default_mode: bypassPermissions", encoding="utf-8"
    )
    (tmp_path / ".novacode" / "settings.local.yaml").write_text(
        "default_mode: acceptEdits", encoding="utf-8"
    )
    engine, _ = new_engine(str(tmp_path))
    assert engine.start_mode == Mode.ACCEPT_EDITS  # 本地优先
    # 去掉本地 → 项目生效
    (tmp_path / ".novacode" / "settings.local.yaml").unlink()
    engine, _ = new_engine(str(tmp_path))
    assert engine.start_mode == Mode.BYPASS
    # 再去掉项目 → 用户生效
    (tmp_path / ".novacode" / "settings.yaml").unlink()
    engine, _ = new_engine(str(tmp_path))
    assert engine.start_mode == Mode.PLAN


def test_new_engine_invalid_config_degrades(tmp_path, monkeypatch):
    """某文件格式非法 → 跳过该文件、其余正常、不致引擎构造失败。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (tmp_path / ".novacode").mkdir()
    (tmp_path / ".novacode" / "settings.yaml").write_text(": : : bad yaml [", encoding="utf-8")
    (tmp_path / ".novacode" / "settings.local.yaml").write_text(
        "default_mode: acceptEdits\npermissions:\n  allow:\n    - 'Bash(git status)'\n",
        encoding="utf-8",
    )
    engine, err = new_engine(str(tmp_path))
    assert err is None  # 不抛致命异常
    assert engine.start_mode == Mode.ACCEPT_EDITS  # 本地层正常加载
    assert engine.project.allow == []  # 非法文件降级为空
    # 本地规则正常生效
    d, _ = engine.check(Mode.DEFAULT, _call("bash", command="git status"), read_only=False)
    assert d == Decision.ALLOW
