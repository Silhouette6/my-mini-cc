"""Plugin-style SkillLoader — manifest.json discovery, progressive disclosure,
and subprocess-based tool execution."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import config


class SkillLoader:
    """Manages skills under a skills directory."""

    def __init__(self, skills_dir: Path | None = None):
        self._dir = skills_dir or config.settings.skills_path
        self.registry: dict[str, dict] = {}
        self.loaded: dict[str, dict] = {}
        self._scan()

    def _scan(self) -> None:
        """Read all manifest.json files, storing only name + description."""
        if not self._dir.exists():
            return
        for manifest_path in sorted(self._dir.rglob("manifest.json")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            name = data.get("name", manifest_path.parent.name)
            self.registry[name] = {
                "manifest": data,
                "dir": manifest_path.parent,
            }

    def summaries(self) -> str:
        """One-line summaries for all registered skills (injected into system prompt)."""
        if not self.registry:
            return "(no skills available)"
        lines = []
        for name, entry in self.registry.items():
            desc = entry["manifest"].get("description", "-")
            lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """Load a skill: return full SKILL.md + tool descriptions."""
        entry = self.registry.get(name)
        if entry is None:
            available = ", ".join(self.registry.keys()) or "none"
            return f"Error: Unknown skill '{name}'. Available: {available}"

        skill_dir: Path = entry["dir"]
        manifest: dict = entry["manifest"]

        skill_md_path = skill_dir / "SKILL.md"
        body = ""
        if skill_md_path.exists():
            body = skill_md_path.read_text(encoding="utf-8")

        tools_desc = self._format_tool_descriptions(manifest.get("tools", []))

        self.loaded[name] = {
            "manifest": manifest,
            "dir": skill_dir,
        }

        sections = [f"# Skill: {name}"]
        if body:
            sections.append(body)
        if tools_desc:
            sections.append(f"## Available tools\n\n{tools_desc}")
            sections.append(
                "Call these tools via: run_skill_tool(skill_name, tool_name, args_json)"
            )
        return "\n\n".join(sections)

    @staticmethod
    def _format_tool_descriptions(tools: list[dict]) -> str:
        if not tools:
            return ""
        lines: list[str] = []
        for t in tools:
            args_parts: list[str] = []
            for arg_name, arg_spec in t.get("args", {}).items():
                req = " (required)" if arg_spec.get("required") else ""
                args_parts.append(
                    f"    - {arg_name}: {arg_spec.get('type', 'string')}"
                    f" - {arg_spec.get('description', '')}{req}"
                )
            args_str = "\n".join(args_parts) if args_parts else "    (no arguments)"
            lines.append(f"- **{t['name']}**: {t.get('description', '')}\n{args_str}")
        return "\n".join(lines)

    def run_tool(self, skill_name: str, tool_name: str, args: dict) -> str:
        """Execute a tool script from a loaded skill via subprocess."""
        entry = self.loaded.get(skill_name)
        if entry is None:
            if skill_name in self.registry:
                return (
                    f"Error: Skill '{skill_name}' is not loaded yet. "
                    f"Call load_skill('{skill_name}') first."
                )
            return f"Error: Unknown skill '{skill_name}'"

        manifest = entry["manifest"]
        skill_dir: Path = entry["dir"]

        tool_spec = None
        for t in manifest.get("tools", []):
            if t["name"] == tool_name:
                tool_spec = t
                break
        if tool_spec is None:
            available = ", ".join(t["name"] for t in manifest.get("tools", []))
            return (
                f"Error: Tool '{tool_name}' not found in skill '{skill_name}'. "
                f"Available: {available}"
            )

        script_rel = tool_spec.get("script", "")
        script_path = skill_dir / script_rel
        if not script_path.exists():
            return f"Error: Script not found at {script_path}"

        return self._execute_script(script_path, args)

    @staticmethod
    def _execute_script(script_path: Path, args: dict) -> str:
        """Run a Python or shell script, passing args as JSON."""
        args_json = json.dumps(args, ensure_ascii=False)
        suffix = script_path.suffix.lower()

        if suffix == ".py":
            cmd = [sys.executable, str(script_path), "--args-json", args_json]
        elif suffix in (".sh", ".bash"):
            cmd = ["bash", str(script_path), args_json]
        elif suffix in (".ps1",):
            cmd = ["powershell", "-File", str(script_path), args_json]
        else:
            cmd = [str(script_path), args_json]

        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=config.settings.command_timeout,
                cwd=str(config.settings.workdir),
            )
            output = ((r.stdout or "") + (r.stderr or "")).strip()
            return output[:50000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: Script timed out ({config.settings.command_timeout}s)"
        except Exception as e:
            return f"Error executing script: {e}"
