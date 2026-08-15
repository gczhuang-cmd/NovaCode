"""prompt 包单测：模块装配、空槽跳过、确定性、双重强化、环境与 reminder。"""

from novacode.prompt import (
    ActiveSkillEntry,
    Environment,
    Module,
    SkillCatalogItem,
    assemble_system,
    build_system_prompt,
    gather_environment,
    plan_reminder,
    render_active_skills_block,
    render_skills_catalog,
)


def test_assemble_order_and_separator():
    """AC1：身份段在工具使用段之前，模块间以空行分隔。"""
    text = build_system_prompt()
    blocks = text.split("\n\n")
    assert len(blocks) == 7  # 七固定模块，空槽跳过
    assert "AI 编程助手" in blocks[0]  # 身份在最前
    assert "read_file" in blocks[4]  # 工具使用在第五块
    assert text.index("AI 编程助手") < text.index("read_file")  # 身份在工具使用之前


def test_optional_empty_modules_skipped():
    """AC2：空 content 模块装配时跳过、不留多余空行。"""
    mods = [
        Module("身份", 10, "A"),
        Module("空槽", 90, ""),
        Module("输出", 70, "B"),
    ]
    text = assemble_system(mods)
    assert text == "A\n\nB"  # 无连续空行
    assert "空槽" not in text


def test_stable_determinism():
    """N1：两次装配稳定系统提示逐字节相等。"""
    assert build_system_prompt() == build_system_prompt()


def test_double_reinforcement():
    """F5：关键约定（专用工具、编辑前先读）在系统提示文本中出现。"""
    text = build_system_prompt()
    assert "read_file" in text and "bash" in text
    assert "编辑文件前必须先" in text or "read_file 读取确认" in text


def test_environment_render_and_git_degrade(tmp_path, monkeypatch):
    """AC3/AC13：render 含各项；非 git 目录 git_status 为空且不抛异常。"""
    monkeypatch.chdir(tmp_path)  # 临时目录非 git
    env = gather_environment(version="0.1.0", model="test")
    rendered = env.render()
    assert "工作目录" in rendered
    assert "平台" in rendered
    assert "日期" in rendered
    assert "版本" in rendered
    assert "模型" in rendered
    assert env.git_status == ""  # 非 git 目录降级


def test_environment_manual_render():
    """AC3：手工构造的 Environment.render 逐行 Key: Value，空值省略。"""
    env = Environment(
        working_dir="/tmp",
        platform="linux",
        date="2026-01-01",
        git_status="",
        version="0.1.0",
        model="m",
    )
    rendered = env.render()
    assert "工作目录: /tmp" in rendered
    assert "git" not in rendered  # 空值省略


def test_system_reminder_wrapped():
    """AC8：plan_reminder 以 <system-reminder> 标签包裹；完整/精简不同。"""
    full = plan_reminder(True)
    assert full.startswith("<system-reminder>")
    assert full.endswith("</system-reminder>")
    concise = plan_reminder(False)
    assert concise != full
    assert "<system-reminder>" in concise


# ─────────────────────────── ch09 参数化 instructions / memory ───────────────────────────


def test_build_system_prompt_with_instructions_and_memory():
    """ch09 F43/AC27：非空参数填入「自定义指令」(80) 与「长期记忆」(100) 模块，按优先级排列。"""
    text = build_system_prompt("项目规范：用中文", "记忆：用户偏好简洁")
    assert "项目规范：用中文" in text
    assert "记忆：用户偏好简洁" in text
    # 自定义指令(80) 排在 已激活 Skill(90) 之前，长期记忆(100) 最后
    assert text.index("项目规范：用中文") < text.index("记忆：用户偏好简洁")
    # 模块间以空行分隔
    blocks = text.split("\n\n")
    assert blocks[-1] == "记忆：用户偏好简洁"
    # 与 ch08 固定模块并存
    assert "AI 编程助手" in text


def test_build_system_prompt_empty_params_backward_compatible():
    """ch09 AC27：空参数与旧无参调用逐字节一致。"""
    assert build_system_prompt() == build_system_prompt("", "")
    # 与旧版行为一致：7 固定模块，空槽跳过
    assert len(build_system_prompt("", "").split("\n\n")) == 7


def test_optional_modules_only_fills_given_slots():
    """ch09：只填 memory 时 instructions 槽仍为空，装配跳过不留空行。"""
    text = build_system_prompt(memory="记住")
    assert "记住" in text
    assert "自定义指令" not in text
    assert text.split("\n\n")[-1] == "记住"
    # instructions 单独填时不出现 memory
    text2 = build_system_prompt(instructions="规则")
    assert "规则" in text2 and "记住" not in text2


# ─────────────────────────── ch11 skills_catalog / active-skills ───────────────────────────


def test_build_system_prompt_with_skills_catalog():
    """ch11 F21：非空 skills_catalog 注入「技能目录」槽（priority 90）。"""
    catalog = render_skills_catalog(
        [SkillCatalogItem("commit", "提交"), SkillCatalogItem("test", "测试")]
    )
    text = build_system_prompt(skills_catalog=catalog)
    assert "## Available Skills" in text
    assert "- commit: 提交" in text
    assert "- test: 测试" in text
    assert "LoadSkill" in text
    # 排在长期记忆之前（priority 90 < 100）
    mem_text = build_system_prompt(memory="记忆", skills_catalog=catalog)
    assert mem_text.index("- commit") < mem_text.index("记忆")


def test_build_system_prompt_skills_catalog_empty_backward_compatible():
    """ch11：空 skills_catalog 与 ch09 输出逐字节一致（向后兼容）。"""
    assert build_system_prompt("", "", "") == build_system_prompt("", "")
    assert len(build_system_prompt("", "").split("\n\n")) == 7  # 空槽跳过


def test_render_skills_catalog_empty():
    assert render_skills_catalog([]) == ""


def test_render_active_skills_block_empty():
    assert render_active_skills_block([]) == ""


def test_render_active_skills_block_non_empty():
    out = render_active_skills_block(
        [ActiveSkillEntry("commit", "SOP 正文 A"), ActiveSkillEntry("review", "SOP 正文 B")]
    )
    assert "## Active Skills" in out
    assert "### Skill: commit" in out
    assert "SOP 正文 A" in out
    assert "### Skill: review" in out
    # 保持激活顺序
    assert out.index("commit") < out.index("review")
