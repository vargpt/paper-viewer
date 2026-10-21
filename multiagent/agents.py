"""Running one agent: a Claude Agent SDK session with a role, a tool set and, optionally, a JSON answer.

Each agent is a Claude Code session driven through the Claude Agent SDK. By default (auth "openrouter")
Claude Code talks to OpenRouter's Anthropic-compatible API with the key in .env, the same provider the
baseline uses; "subscription" uses your claude.ai login instead, "api-key" an Anthropic API key.
"""
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, RateLimitEvent, ResultMessage,
                              SystemMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage, query)

API_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
           "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


OPENROUTER_URL = "https://openrouter.ai/api"  # OpenRouter's Anthropic-compatible endpoint


def use_auth(mode: str) -> None:
    """Point Claude Code at the right credentials before any agent starts.

    openrouter:   OPENROUTER_API_KEY (or LLM_API_KEY, as the baseline uses) from .env, billed to OpenRouter
    subscription: hide API credentials so Claude Code uses your claude.ai login (Pro/Max plan)
    api-key:      ANTHROPIC_API_KEY from .env, billed to the Anthropic API
    """
    if mode == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM_API_KEY")
        if not key:
            raise SystemExit("--auth openrouter needs OPENROUTER_API_KEY (or LLM_API_KEY) in paper-viewer/.env")
        os.environ.update(ANTHROPIC_BASE_URL=OPENROUTER_URL, ANTHROPIC_AUTH_TOKEN=key, ANTHROPIC_API_KEY="")
        # Claude Code's own small background calls (e.g. session titles) need an OpenRouter model name too
        os.environ.setdefault("ANTHROPIC_DEFAULT_HAIKU_MODEL", "anthropic/claude-haiku-5.5")
        os.environ.setdefault("ANTHROPIC_SMALL_FAST_MODEL", "anthropic/claude-haiku-5.5")
    elif mode == "subscription":
        dropped = [k for k in API_ENV if os.environ.pop(k, None) is not None]
        if dropped:
            print(f"auth: using your Claude subscription (ignoring {', '.join(dropped)})")
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("--auth api-key needs ANTHROPIC_API_KEY in paper-viewer/.env")


class UsageLimit(RuntimeError):
    """The plan's usage window is used up. Everything done so far is saved; re-run later to resume."""


@dataclass
class Run:
    name: str
    data: dict | None  # the structured answer, when a schema was given
    text: str
    cost_usd: float
    turns: int
    seconds: float


class Team:
    """Runs the agents for one paper: shared workspace, concurrency limit, logs and a usage ledger."""

    def __init__(self, ws: Path, *, model: str, effort: str, parallel: int, auth: str):
        self.ws, self.model, self.effort, self.auth = ws.resolve(), model, effort, auth
        self.logs = self.ws / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.slots = asyncio.Semaphore(parallel)
        self.started = time.time()
        self.ledger: list[dict] = []
        self.auth_seen = False
        self.limit_warned = False
        self.rate: object | None = None

    def say(self, who: str, message: str) -> None:
        m, s = divmod(int(time.time() - self.started), 60)
        print(f"[{m:3d}:{s:02d}] {who:<28} {message}", flush=True)

    async def run(self, name: str, prompt: str, *, system: str, tools=(), writable=(), mcp: dict | None = None,
                  schema: dict | None = None, need_data: bool = True, model: str | None = None,
                  effort: str | None = None, max_turns: int = 40, retries: int = 2) -> Run:
        """Run one agent to completion, retrying failures (but not an exhausted usage window)."""
        async with self.slots:
            for attempt in range(retries + 1):
                try:
                    return await self._run(name, prompt, system=system, tools=tools, writable=writable, mcp=mcp,
                                           schema=schema, need_data=need_data, model=model, effort=effort,
                                           max_turns=max_turns)
                except UsageLimit:
                    raise
                except Exception as e:
                    if _looks_like_usage_limit(str(e)):
                        raise UsageLimit(f"usage limit reached ({str(e)[:200]}). Re-run the same command to resume.") from e
                    if attempt == retries:
                        raise
                    wait = 30 * (attempt + 1)
                    self.say(name, f"failed ({type(e).__name__}: {str(e)[:160]}), retrying in {wait}s")
                    await asyncio.sleep(wait)

    async def _run(self, name, prompt, *, system, tools, writable, mcp, schema, need_data, model, effort, max_turns) -> Run:
        system_file = self.logs / f"{name}.system.md"
        system_file.write_text(system)
        log = self.logs / f"{name}.md"
        log.write_text(f"# {name}\n\n## Prompt\n\n{prompt}\n\n## Transcript\n\n")
        mcp = mcp or {}
        options = ClaudeAgentOptions(
            model=model or self.model,
            effort=effort or self.effort,
            system_prompt={"type": "file", "path": str(system_file)},
            cwd=str(self.ws),
            tools=list(tools),
            allowed_tools=list(tools) + [f"mcp__{server}" for server in mcp],
            permission_mode="dontAsk",  # anything not allowed above is refused, never prompted
            mcp_servers=mcp,
            strict_mcp_config=True,  # only our tools: no user/project MCP servers or connectors
            setting_sources=[],  # don't load ~/.claude or project settings, hooks, CLAUDE.md
            skills=[],
            hooks={"PreToolUse": [HookMatcher(hooks=[self._guard(name, writable)])]},
            output_format={"type": "json_schema", "schema": schema} if schema else None,
            max_turns=max_turns,
            max_buffer_size=64 * 1024 * 1024,  # tool results with images (figures, screenshots) exceed the 1 MB default
            # restricted: file tools confined to the workspace, no shell. Our own logs replace Claude Code's
            # session transcripts, which would otherwise pile up in ~/.claude/projects (one per agent).
            extra_args={"restricted": None, "no-session-persistence": None},
            stderr=lambda line: self._note(log, f"stderr: {line}"),
        )
        started, result, texts = time.time(), None, []
        self.say(name, f"started ({options.model}, effort {options.effort})")
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                self._check_auth(message.data)
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        texts.append(block.text)
                        self._note(log, block.text)
                    elif isinstance(block, ToolUseBlock):
                        self.say(name, self._describe(block))
                        self._note(log, f"**{block.name}** `{json.dumps(block.input)[:600]}`")
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        self._note(log, ("ERROR " if block.is_error else "") + "> " + _brief(block.content))
            elif isinstance(message, RateLimitEvent):
                self._rate_limit(message.rate_limit_info)
            elif isinstance(message, ResultMessage):
                result = message

        if result is None:
            raise RuntimeError("the session ended without a result")
        if result.is_error and result.subtype != "error_max_turns":
            info = self.rate
            rejected = info is not None and getattr(info, "status", "") == "rejected"
            if (result.api_error_status == 429 and rejected) or _looks_like_usage_limit(f"{result.result} {result.errors}"):
                resets = getattr(info, "resets_at", None) if info else None
                when = time.strftime("%H:%M", time.localtime(resets)) if resets else "later"
                raise UsageLimit(f"usage limit reached (resets around {when}). Re-run the same command to resume.")
            raise RuntimeError(f"{result.subtype}: {result.errors or (result.result or '')[:300]}")
        data = result.structured_output if schema else None
        if schema and data is None:
            data = _parse_json(result.result or "")
        if schema and need_data and data is None:
            raise RuntimeError(f"no structured answer ({result.subtype})")
        if result.subtype == "error_max_turns":
            self.say(name, f"stopped at the {max_turns}-turn limit")
        run = Run(name, data, "\n\n".join(texts), result.total_cost_usd or 0.0, result.num_turns, time.time() - started)
        usage = result.usage or {}
        row = {"agent": name, "model": options.model, "seconds": round(run.seconds), "turns": run.turns,
               "cost_usd": round(run.cost_usd, 4), "input_tokens": usage.get("input_tokens", 0),
               "output_tokens": usage.get("output_tokens", 0),
               "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
               "cache_write_tokens": usage.get("cache_creation_input_tokens", 0)}
        self.ledger.append(row)
        with (self.logs / "usage.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        self._note(log, f"\n## Result\n\n{result.subtype}, {run.turns} turns, {run.seconds:.0f}s, ${run.cost_usd:.2f}"
                        + (f"\n\n```json\n{json.dumps(data, indent=2)}\n```" if data else ""))
        m, s = divmod(int(run.seconds), 60)
        self.say(name, f"finished in {m}m{s:02d}s, {run.turns} turns, ${run.cost_usd:.2f}")
        return run

    def _guard(self, name: str, writable):
        """PreToolUse hook: an agent may only write the files it owns, and doesn't read the pipeline's logs."""
        allowed = [Path(p).resolve() for p in writable]
        ws, logs = self.ws, self.logs

        def deny(reason: str) -> dict:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}

        async def hook(event, tool_use_id, context):
            tool, args = event.get("tool_name"), event.get("tool_input") or {}
            target = Path(args.get("file_path") or args.get("notebook_path") or args.get("path") or ws)
            target = (target if target.is_absolute() else ws / target).resolve()
            if tool in ("Read", "Glob", "Grep") and (target == logs or logs in target.parents):
                return deny("logs/ holds the pipeline's own records; it is not material for the page.")
            if tool in EDIT_TOOLS and not any(target == a or a in target.parents for a in allowed):
                return deny(f"{name} may only edit: " + (", ".join(str(a.relative_to(ws)) for a in allowed) or "nothing"))
            return {}
        return hook

    def _check_auth(self, init: dict) -> None:
        if self.auth_seen:
            return
        self.auth_seen = True
        source = init.get("apiKeySource", "unknown")
        if self.auth == "openrouter":
            self.say("auth", f"using OpenRouter ({os.environ.get('ANTHROPIC_BASE_URL')})")
        elif self.auth == "subscription" and source != "none":
            self.say("auth", f"WARNING: expected your Claude subscription but Claude Code is using {source}")
        else:
            self.say("auth", "using your Claude subscription (claude.ai login)" if source == "none" else f"using {source}")

    def _rate_limit(self, info) -> None:
        self.rate = info
        if info.status != "allowed" and not self.limit_warned:
            self.limit_warned = True
            used = f"{info.utilization:.0%} of " if info.utilization is not None else ""
            self.say("usage", f"{info.status}: {used}your {info.rate_limit_type or ''} usage window".replace("  ", " "))

    @staticmethod
    def _note(log: Path, text: str) -> None:
        with log.open("a") as f:
            f.write(text.rstrip() + "\n\n")

    def _describe(self, block: ToolUseBlock) -> str:
        args = block.input or {}

        def rel(p: str) -> str:
            try:
                return str(Path(p).resolve().relative_to(self.ws))
            except ValueError:
                return p
        tool = block.name.split("__")[-1]
        if tool == "Write":
            return f"write {rel(args.get('file_path', ''))} ({len(args.get('content', '')) / 1000:.1f} KB)"
        if tool in ("Edit", "MultiEdit"):
            return f"edit {rel(args.get('file_path', ''))}"
        if tool == "Read":
            return f"read {rel(args.get('file_path', ''))}"
        if tool in ("Glob", "Grep"):
            return f"{tool.lower()} {args.get('pattern', '')}"
        if tool == "check_section":
            return f"check_section {args.get('section_id', '')}"
        if tool == "crop_figure":
            return f"crop_figure page {args.get('page')} -> figures/{args.get('name')}.png"
        if tool == "extract_images":
            return f"extract_images page {args.get('page')}"
        if tool == "StructuredOutput":
            return "answer submitted"
        return tool


def _looks_like_usage_limit(text: str) -> bool:
    text = text.lower()
    return "usage limit" in text or "limit reached" in text or "out of extra usage" in text


def _brief(content) -> str:
    if isinstance(content, list):
        content = " ".join(c.get("text", f"[{c.get('type')}]") for c in content if isinstance(c, dict))
    return re.sub(r"\s+", " ", str(content or ""))[:400]


def _parse_json(text: str) -> dict | None:
    """Fallback when the structured answer is missing: the first JSON object in the final text."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
