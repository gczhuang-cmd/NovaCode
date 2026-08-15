"""instructions.Loader 单测：三层优先级、@include 深度/环路/逃逸/缺失/二进制。"""

from __future__ import annotations

from novacode.instructions import Loader


def _loader(root, user) -> Loader:
    """临时目录构造 Loader（root=项目根，user=用户主目录）。"""
    return Loader(project_root=str(root), user_home=str(user))


def _write(root, rel, text):
    """写入相对项目根的文本文件（自动建父目录）。"""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_three_level_priority_order(tmp_path):
    """F1/AC1：三层都有内容 → 按 项目根 → .novacode/ → ~/.novacode/ 顺序拼接。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "项目根内容")
    _write(root, ".novacode/novaCODE.md", "项目配置内容")
    _write(user, ".novacode/novaCODE.md", "用户内容")
    text = _loader(root, user).load()
    assert text.index("项目根内容") < text.index("项目配置内容") < text.index("用户内容")
    assert text.count("\n\n") == 2  # 层间以空行分隔


def test_missing_levels_silent(tmp_path):
    """AC2：只在项目根放 → 加载成功，只含项目根内容。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "仅项目根")
    text = _loader(root, user).load()
    assert "仅项目根" in text and "项目配置" not in text


def test_all_missing_returns_empty(tmp_path):
    """F6：三个路径都无 novaCODE.md → 返回空字符串（不报错）。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    assert _loader(root, user).load() == ""


def test_include_expands(tmp_path):
    """AC3：`@include sub/rules.md` → 文件内容替换该行。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "规则如下：\n@include sub/rules.md\n结束")
    _write(root, "sub/rules.md", "代码块必须带语言标记")
    text = _loader(root, user).load()
    assert "规则如下：" in text
    assert "代码块必须带语言标记" in text
    assert "@include" not in text
    assert "结束" in text


def test_include_nested_expand(tmp_path):
    """AC：A include B、B include C → A 输出包含 C 内容。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "@include sub/a.md")
    _write(root, "sub/a.md", "A-内容\n@include b.md")
    _write(root, "sub/b.md", "B-内容\n@include c.md")
    _write(root, "sub/c.md", "C-内容")
    text = _loader(root, user).load()
    assert "A-内容" in text and "B-内容" in text and "C-内容" in text


def test_max_depth_truncated(tmp_path):
    """AC4：构造 6 层嵌套 @include 链 → 引用第 6 层的 include 行不展开、出现深度警告。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "@include l1.md")
    for i in range(1, 6):
        _write(root, f"l{i}.md", f"L{i}\n@include l{i + 1}.md")
    _write(root, "l6.md", "L6")
    text = _loader(root, user).load()
    # L1..L4 展开（第 2~5 层）；l4 里的 @include l5.md 引用第 6 层，保留原文不展开
    assert "L1" in text and "L2" in text and "L3" in text and "L4" in text
    assert "L5" not in text and "L6" not in text
    assert "@include l5.md" in text  # 超过深度的 include 行保留原文
    assert "超过最大嵌套深度" in text


def test_cycle_detected(tmp_path):
    """AC5：A include B、B include A → 第二次 include 不展开，出现环路警告。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "@include a.md")
    _write(root, "a.md", "A\n@include b.md")
    _write(root, "b.md", "B\n@include a.md")
    text = _loader(root, user).load()
    assert "A" in text and "B" in text
    assert "检测到环路" in text
    assert text.count("A\n") == 1  # A 只展开一次


def test_escape_detected(tmp_path):
    """AC6：项目级 novaCODE.md 中 `@include ../../outside.md` → 越界，范围警告。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    outside = tmp_path / "outside.md"
    outside.write_text("外部", encoding="utf-8")
    # 相对 root 的 ../../ 指向 tmp_path（root 之外）
    _write(root, "novaCODE.md", "@include ../../outside.md")
    text = _loader(root, user).load()
    assert "路径超出允许范围" in text
    assert "外部" not in text


def test_user_level_boundary(tmp_path):
    """F5：用户级 novaCODE.md 的 @include 边界是 ~/.novacode/，越界报范围警告。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(user, ".novacode/novaCODE.md", "@include ../secret.md")
    (user / "secret.md").write_text("秘密", encoding="utf-8")
    text = _loader(root, user).load()
    assert "路径超出允许范围" in text
    assert "秘密" not in text


def test_include_missing_file_silent(tmp_path):
    """F6：@include 一个不存在的文件 → 静默跳过（非错误），其余内容保留。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "头部\n@include no_such.md\n尾部")
    text = _loader(root, user).load()
    assert "头部" in text and "尾部" in text
    assert "@include" not in text
    assert "头部\n尾部" in text.replace("\n\n", "\n").replace("\n\n", "\n")


def test_binary_file_skipped(tmp_path):
    """F6：@include 指向含 \x00 的文件 → 跳过并出现二进制警告。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "@include bin.dat")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02")
    text = _loader(root, user).load()
    assert "二进制" in text


def test_include_inline_not_expanded(tmp_path):
    """F2：非独占行上的 @include 保持原文不做替换。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "见 @include x.md 说明")
    _write(root, "x.md", "内容")
    text = _loader(root, user).load()
    assert "@include x.md" in text
    assert "内容" not in text


def test_empty_include_file_produces_empty(tmp_path):
    """F6：空文件产出空内容（不影响拼接）。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "上\n@include empty.md\n下")
    _write(root, "empty.md", "")
    text = _loader(root, user).load()
    assert "上" in text and "下" in text
    assert "empty" not in text


def test_project_level_include_relative_to_file_dir(tmp_path):
    """F2：路径相对当前文件所在目录解析。"""
    root = tmp_path / "proj"
    user = tmp_path / "home"
    _write(root, "novaCODE.md", "@include .novacode/rules/style.md")
    _write(root, ".novacode/rules/style.md", "代码块必须带语言标记")
    text = _loader(root, user).load()
    assert "代码块必须带语言标记" in text
    # 相对包含文件所在目录：sub/b.md 里的 @include 指向 sub/ 下的 sibling
    _write(root, "sub/b.md", "@include c.md")
    _write(root, "sub/c.md", "SIB")
    _write(root, "novaCODE.md", "@include sub/b.md")
    text2 = _loader(root, user).load()
    assert "SIB" in text2
