"""conversation 模块单测。"""

from novacode.conversation import Conversation
from novacode.llm import Message, ToolCall, ToolResult


def test_last_role():
    """last_role 返回末尾角色；空历史返回 ""。"""
    c = Conversation()
    assert c.last_role() == ""
    c.add_user("hi")
    assert c.last_role() == "user"
    c.add_tool_results([ToolResult(tool_call_id="c1", content="x")])
    assert c.last_role() == "tool"
    c.add_assistant("ok")
    assert c.last_role() == "assistant"


def test_tool_turn_sequence():
    """工具调用回合 + 结果回合 + 续答：role 序列与字段内容正确。"""
    c = Conversation()
    c.add_user("读文件")
    c.add_assistant_with_tool_calls(
        "好的", [ToolCall(id="c1", name="read_file", input='{"path": "x"}')]
    )
    c.add_tool_results([ToolResult(tool_call_id="c1", content="文件内容", is_error=False)])
    c.add_assistant("总结")
    msgs = c.messages()
    assert len(msgs) == 4
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[2].tool_results[0].content == "文件内容"


def test_empty_history():
    c = Conversation()
    assert c.messages() == []


def test_user_assistant_alternation():
    c = Conversation()
    c.add_user("你好")
    c.add_assistant("你好！有什么可以帮你？")
    c.add_user("介绍一下自己")
    c.add_assistant("我是 NovaCode。")
    msgs = c.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0].content == "你好"
    assert msgs[-1].content == "我是 NovaCode。"


def test_messages_returns_copy():
    c = Conversation()
    c.add_user("x")
    msgs = c.messages()
    msgs.append(Message(role="assistant", content="外部插入不应生效"))
    assert len(c.messages()) == 1


# ─────────────────────────── ch08 length / replace_history ───────────────────────────


def test_length():
    c = Conversation()
    assert c.length() == 0
    c.add_user("a")
    c.add_assistant("b")
    assert c.length() == 2


def test_replace_history_deep_copy():
    c = Conversation()
    c.add_user("a")
    src = [Message(role="user", content="x"), Message(role="assistant", content="y")]
    c.replace_history(src)
    src[0].content = "mutated"  # 修改原列表不影响 conversation
    assert c.messages()[0].content == "x"
    assert c.length() == 2


def test_replace_history_empty():
    c = Conversation()
    c.add_user("a")
    c.replace_history([])
    assert c.messages() == []


# ─────────────────────────── ch09 on_append / on_replace / from_messages ───────────────────────────


def test_on_append_fired_on_each_add():
    """ch09 F13/F44：每次追加消息触发 on_append，参数为刚追加的消息。"""
    seen: list[Message] = []
    c = Conversation(on_append=seen.append)
    c.add_user("u")
    c.add_assistant("a")
    c.add_assistant_with_tool_calls("t", [ToolCall(id="c1", name="read_file", input="{}")])
    c.add_tool_results([ToolResult(tool_call_id="c1", content="x")])
    assert len(seen) == 4
    assert [m.role for m in seen] == ["user", "assistant", "assistant", "tool"]
    assert seen[0].content == "u"
    assert seen[2].tool_calls[0].name == "read_file"


def test_on_replace_fired_on_replace_history():
    """ch09 F12/F44：replace_history 触发 on_replace，参数为替换后的消息列表。"""
    replaced: list[list[Message]] = []
    c = Conversation(on_replace=lambda msgs: replaced.append(msgs))
    c.add_user("u")
    c.replace_history([Message(role="user", content="new")])
    assert len(replaced) == 1
    assert [m.content for m in replaced[0]] == ["new"]


def test_on_replace_notify_false_not_fired():
    """ch09 F12：layer1 渐进替换传 notify=False → 不触发 on_replace（不写 compact 标记）。"""
    replaced: list[list[Message]] = []
    c = Conversation(on_replace=lambda msgs: replaced.append(msgs))
    c.add_user("u")
    c.replace_history([Message(role="user", content="layer1")], notify=False)
    assert replaced == []  # 内存已替换，但回调不触发
    assert c.messages()[0].content == "layer1"
    # 后续 notify=True 仍正常触发
    c.replace_history([Message(role="assistant", content="layer2")])
    assert len(replaced) == 1
    assert replaced[0][0].role == "assistant"


def test_no_callbacks_no_change():
    """ch09 AC28：未设置回调时行为与 ch08 完全一致。"""
    c = Conversation()
    c.add_user("u")
    c.add_assistant("a")
    c.replace_history([Message(role="user", content="x")])
    assert [m.content for m in c.messages()] == ["x"]


def test_from_messages_no_callbacks_and_copy():
    """ch09 F22：from_messages 拷贝初始化、不触发回调、后续追加仍触发。"""
    appended: list[Message] = []
    src = [Message(role="user", content="u"), Message(role="assistant", content="a")]
    c = Conversation.from_messages(src, on_append=appended.append)
    # 初始化不触发回调（恢复的消息不重复写盘）
    assert appended == []
    assert [m.content for m in c.messages()] == ["u", "a"]
    src[0].content = "mutated"  # 外部修改原列表不影响 conversation
    assert c.messages()[0].content == "u"
    # 后续追加仍触发回调
    c.add_assistant("b")
    assert len(appended) == 1
    assert appended[0].content == "b"
