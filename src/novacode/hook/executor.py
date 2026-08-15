"""Hook 动作执行器：四类 action（shell / prompt / http / subagent stub）（ch12 F17-F26）。"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass

import httpx

from .rule import ActionType, Payload, Rule


@dataclass
class ExecutionResult:
    """一次动作执行的结果：拦截信号 / 注入 prompt / hook 自身失败。"""

    blocked: bool = False
    reason: str = ""
    prompt: str = ""  # 仅 prompt 动作非空
    err: Exception | None = None  # hook 自身失败（不拦截）


class Executor:
    """四类动作执行入口。单实例复用 httpx 连接池。"""

    def __init__(self) -> None:
        # 默认 timeout=30s，可被 rule.timeout_s 覆盖；trust_env=False：hook 通知直连目标，
        # 不读系统代理（否则本地回环/测试请求也会被代理拦截）
        self._http_client = httpx.AsyncClient(timeout=30.0, trust_env=False)

    async def run(self, rule: Rule, payload: Payload, *, blocking: bool) -> ExecutionResult:
        """按 rule.action.type 分发执行；返回 ExecutionResult。"""
        action = rule.action
        if action.type is ActionType.SHELL:
            return await self._run_shell(action.shell, payload, blocking, rule.timeout_s)
        if action.type is ActionType.PROMPT:
            assert action.prompt is not None
            return ExecutionResult(prompt=action.prompt.text)
        if action.type is ActionType.HTTP:
            return await self._run_http(action.http, payload, blocking, rule.timeout_s)
        if action.type is ActionType.SUBAGENT:
            # 本期占位：仅记一行 stderr，不报错不拦截（F26/N8）
            assert action.subagent is not None
            print(
                f"[hook subagent] not yet implemented, skipped: {action.subagent.agent_name}",
                file=sys.stderr,
            )
            return ExecutionResult()
        return ExecutionResult(err=RuntimeError(f"unknown action type: {action.type}"))

    async def _run_shell(
        self, sa, payload: Payload, blocking: bool, timeout_s: float
    ) -> ExecutionResult:
        """shell 动作：stdin 喂 payload JSON；exit 2 拦截、0 放行、其它非 0 视为失败（F17-F19）。

        平台差异（对齐 bash 工具）：Linux/WSL 由 sh 解释；Windows 走 cmd.exe，输出按 GBK 解码。
        """
        payload_json = json.dumps(payload, sort_keys=True).encode()
        proc = await asyncio.create_subprocess_shell(
            sa.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload_json), timeout=timeout_s
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecutionResult(err=TimeoutError(f"command timed out after {timeout_s}s"))
        code = proc.returncode or 0
        err_text = _decode_output(stderr)
        if blocking and code == 2:
            # 拒绝原因：stderr 优先、stdout 兜底，去尾换行（Windows 为 \r\n）
            reason = (err_text or _decode_output(stdout)).rstrip("\r\n")
            return ExecutionResult(blocked=True, reason=reason)
        if code == 0:
            return ExecutionResult()
        return ExecutionResult(err=RuntimeError(f"exit {code}: {err_text}"))

    async def _run_http(
        self, ha, payload: Payload, blocking: bool, timeout_s: float
    ) -> ExecutionResult:
        """http 动作：POST JSON / 模板 body；2xx 且 decision=block 拦截（F23-F25）。"""
        if ha.body is not None:
            try:
                body = ha.body.format_map(payload)
            except (KeyError, ValueError) as e:
                return ExecutionResult(err=ValueError(f"body template render failed: {e}"))
        else:
            body = json.dumps(payload, sort_keys=True)
        try:
            resp = await self._http_client.request(
                ha.method or "POST",
                ha.url,
                content=body,
                headers=ha.headers or {},
                timeout=timeout_s,
            )
        except httpx.HTTPError as e:
            return ExecutionResult(err=e)
        if resp.status_code >= 500:
            # 5xx：server 内部错误，按 hook 失败处理（F25 网络错误同语义），不拦截
            return ExecutionResult(err=RuntimeError(f"http {resp.status_code}: {resp.text[:200]}"))
        if 200 <= resp.status_code < 300:
            try:
                data = json.loads(resp.text)
            except (json.JSONDecodeError, TypeError) as e:
                return ExecutionResult(err=ValueError(f"bad response JSON: {e}"))
            if isinstance(data, dict) and data.get("decision") == "block":
                return ExecutionResult(blocked=True, reason=str(data.get("reason", "")))
        return ExecutionResult()


def _decode_output(b: bytes) -> str:
    """解码子进程输出：Windows 按 GBK（系统 ANSI），其它按 UTF-8；失败回退 replace（对齐 bash 工具）。"""
    if sys.platform == "win32":
        try:
            return b.decode("gbk")
        except UnicodeDecodeError:
            return b.decode("utf-8", errors="replace")
    return b.decode("utf-8", errors="replace")
