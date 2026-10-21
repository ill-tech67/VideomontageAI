"""Сквозной тест: композиция → HyperFrames → MP4.

Запускается только при RUN_RENDER_TESTS=1 (нужен Node.js, ffmpeg и сеть).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.connectors.base import ToolContext
from app.connectors.hyperframes import HyperFramesConnector
from app.services.rendering import HyperFramesRunner

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RENDER_TESTS") != "1",
    reason="Требуется реальный рендер: поставьте RUN_RENDER_TESTS=1",
)

COMPOSITION = """<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <title>Тестовый ролик</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      body { margin: 0; background: #10131f; color: white; font-family: system-ui, sans-serif; }
      #root { position: relative; width: 100%; height: 100%; overflow: hidden; }
      .clip { position: absolute; inset: 0; display: grid; place-items: center; }
      h1 { margin: 0; font-size: 88px; text-align: center; padding: 0 60px; }
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0"
         data-width="1080" data-height="1920" data-duration="2" data-fps="30">
      <section id="title-card" class="clip" data-start="0" data-duration="2">
        <h1 id="title">Привет, VideoTG</h1>
      </section>
    </div>
    <script>
      const tl = gsap.timeline({ paused: true });
      tl.from("#title", { y: 60, opacity: 0, duration: 0.6, ease: "power3.out" }, 0.2);
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


async def test_write_composition_and_render(tmp_path: Path):
    connector = HyperFramesConnector()
    ctx = ToolContext(user_id=1, chat_id=1, project_dir=tmp_path, options={})

    write = await connector.execute(
        ctx, "hyperframes_write_composition", {"html": COMPOSITION, "filename": "index.html"}
    )
    assert not write.is_error, write.text
    assert (tmp_path / "index.html").exists()

    render = await connector.execute(
        ctx,
        "hyperframes_render",
        {"quality": "draft", "output": "out.mp4", "wait": True},
    )
    assert not render.is_error, render.text
    output = tmp_path / "out.mp4"
    assert output.exists()
    assert output.stat().st_size > 1000


async def test_runner_detects_modern_ffmpeg():
    runner = HyperFramesRunner()
    ffmpeg = runner.ffmpeg_binary()
    assert ffmpeg, "Современный ffmpeg не найден — укажите FFMPEG_BINARY"
    result = await runner._run(["doctor", "--json"], timeout_seconds=180)
    assert result.ok
