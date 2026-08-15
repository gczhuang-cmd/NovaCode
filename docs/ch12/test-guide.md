# ch12 Hook 系统手动测试指南

> 面向在 novacode 上手动验证 hook 系统的手册。与 `checklist.md` 对应（每场景标注 checklist 场景 / AC 编号）。默认 **Windows 本机**运行，WSL 差异见文末。

## 测试前准备

1. 所有 hook 配置写在项目根 `.novacode/hooks.yaml`（没有就新建）。
2. 启动：`D:\NovaCode` 下 `uv run novacode`。
3. **改配置必须重启 novacode 才生效**（本期不支持热更新，见 spec「不做的事」）。
4. **平台差异**：hook 的 shell 动作在 Windows 走 cmd.exe——命令分隔符用 `&`，stderr 重定向用 `1>&2`；WSL 走 sh——用 `;` 与 `>&2`。

---

## 场景 1：`/hooks` 冒烟（先确认加载正常）

**对应：** checklist 场景 8 / F34。

`.novacode/hooks.yaml`：
```yaml
hooks:
  - name: hello
    event: SessionStart
    action:
      type: shell
      command: "echo hook-hello 1>&2"
```

**操作：** 启动后输入 `/hooks` 回车。
**预期：**
- 启动瞬间终端出现 `hook-hello`（SessionStart 已触发）
- `/hooks` 输出：
  ```
  SessionStart:
    hello  SessionStart  shell
  Loaded from: D:\NovaCode\.novacode\hooks.yaml
  ```

---

## 场景 2：错误配置不阻断启动

**对应：** checklist 场景 7 / AC8 / N1。

```yaml
hooks:
  - name: bad-async
    event: PreToolUse
    async: true
    action:
      type: shell
      command: "echo x 1>&2"
  - name: good
    event: SessionStart
    action:
      type: prompt
      text: "请用简体中文回复，不要用英文。"
```

**操作：** 启动。
**预期：**
- 启动期终端（banner 上方）打印 `hook "bad-async": async not allowed for blocking events, skipped`
- 进程**照常进入输入框**；`/hooks` 只列 `good`、不含 `bad-async`

---

## 场景 3：SessionStart 注入 reminder

**对应：** checklist 场景 2 / AC6。

沿用场景 2 配置（保留 `good` hook）。
**操作：** 启动后发一句英文 `hi`。
**预期：** LLM 用中文回复（`good` 的 prompt 注入进首轮 reminder）。

---

## 场景 4：UserPromptSubmit 拦截

**对应：** checklist 场景 4 / AC10。

```yaml
hooks:
  - name: warn-delete
    event: UserPromptSubmit
    if:
      all_of:
        - field: prompt
          match: { type: regex, value: "(?i)delete" }
    action:
      type: shell
      command: "echo prompt contains delete keyword 1>&2 & exit 2"
```

**操作：** 输入「请帮我 delete 那个文件」回车。
**预期：**
- scrollback 显示 `[hook warn-delete] prompt contains delete keyword`
- 消息**未进入对话历史**（无 LLM 请求、状态栏 token 不增长）
- 输入框重画为空，可重新输入（非全屏架构已提交文本不保留）

---

## 场景 5：PreToolUse 拦截 write_file

**对应：** checklist 场景 1 / AC4。

```yaml
hooks:
  - name: block-write
    event: PreToolUse
    if:
      all_of:
        - field: tool_name
          match: { type: exact, value: write_file }
    action:
      type: shell
      command: "echo blocked by hook 1>&2 & exit 2"
```

**操作：** 发「用 write_file 工具创建一个文件 hello.txt，内容是 hi」。
**预期：**
- 工具行：`● write_file({"path": "hello.txt", ...})`
- 结果回灌：`⎿ [hook block-write] blocked by hook`
- `hello.txt` **未创建**
- LLM 收到拦截反馈后调整（换 edit_file 或 bash），不死循环

---

## 场景 6：only_once + /clear 重置

**对应：** checklist 场景 6 / AC9。

```yaml
hooks:
  - name: first-turn
    event: PreUserMessage
    only_once: true
    action:
      type: shell
      command: "echo first-turn >> .novacode/first.log"
```

**操作：** 依次「首轮发消息 → 再发一条 → `/clear` → 再发消息」，每次后看 `.novacode/first.log` 行数。
**预期：**
- 首轮后 **1 行**（首次触发）
- 第二轮后仍 **1 行**（only_once 跳过）
- `/clear` 后再发变 **2 行**（新会话重置）

---

## 场景 7：Stop 触发 HTTP 通知

**对应：** checklist 场景 5 / AC13。

先起一个接受 POST 的本地 server（另开终端；`python -m http.server` 只支持 GET，需自定义 POST server，见下）：
```yaml
hooks:
  - name: notify-stop
    event: Stop
    action:
      type: http
      url: "http://127.0.0.1:9999/done"
      method: POST
```

**操作：** 发一句简单问题，等 LLM 自然回复完。
**预期：** server 收到一次 POST，body 为字典序 JSON 且含 `"event":"Stop"`：
```json
{"cwd": "...", "event": "Stop", "iter": 1, "mode": "default", "session_id": "..."}
```

> 简易 POST server（存成 `post_server.py` 后用 `.venv\Scripts\python.exe post_server.py` 起）：
> ```python
> from http.server import BaseHTTPRequestHandler, HTTPServer
> class H(BaseHTTPRequestHandler):
>     def do_POST(self):
>         n = int(self.headers.get("Content-Length", 0))
>         print(self.rfile.read(n).decode(), flush=True)
>         self.send_response(200); self.end_headers()
>     def log_message(self, *a): pass
> HTTPServer(("127.0.0.1", 9999), H).serve_forever()
> ```

---

## 场景 8（可选）：PostToolUse async 后台不阻塞

**对应：** checklist 场景 3 / AC7。

```yaml
hooks:
  - name: ruff-after-write
    event: PostToolUse
    if:
      all_of:
        - field: tool_name
          match: { type: exact, value: write_file }
        - field: tool_input.path
          match: { type: glob, value: "**/*.py" }
        - field: is_error
          match: { type: exact, value: "False" }
    action:
      type: shell
      command: "echo ruff-ran >> .novacode/ruff.log"
    async: true
    timeout: 5s
```

**操作：** 让 LLM 写一个 `.py` 文件。
**预期：**
- 写入完成后**主对话立即进入下一轮**（不停顿）
- `.novacode/ruff.log` 出现一行 `ruff-ran`（异步后台执行；失败会在终端打 `[hook ruff-after-write] ... failed:` 但不断流）
- 把 `is_error` 条件改 `"True"` 再写文件，日志**不再增加**（条件未命中）

---

## WSL 测试差异

| 项 | Windows 本机 | WSL tmux |
|----|-------------|----------|
| 启动 | `uv run novacode` | `wsl -d Ubuntu -- bash -lc 'cd /mnt/d/NovaCode && /root/.venv-novacode/bin/python -m novacode'` |
| shell 命令 | cmd：`echo x 1>&2 & exit 2` | sh：`echo x >&2; exit 2` |
| 写日志 | `.novacode/xxx.log`（正斜杠避免转义） | `/tmp/xxx.log` |
| stderr 观察 | 直接看终端 | tmux `capture-pane -p > 文件` 后 Windows 侧 Read |

## 清理

测试后删除：`.novacode/hooks.yaml`、`.novacode/first.log`、`.novacode/ruff.log`、测试产生的文件与会话目录，避免污染后续使用。
