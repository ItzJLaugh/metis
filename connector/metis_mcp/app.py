"""Metis desktop app: choose the folders Metis can see and connect Metis to Claude Desktop.

Run from the `connector` folder:  python -m metis_mcp.app
"""

import os
import sys
from pathlib import Path

import webview

from metis_mcp import core

CONNECTOR = Path(__file__).resolve().parent.parent  # the folder that holds metis_mcp


def claude_config():
    """Claude Desktop's config file. Newer Windows versions keep it in %LOCALAPPDATA%\\Claude-Data\\claude_*."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    newer = list((Path(os.environ["LOCALAPPDATA"]) / "Claude-Data").glob("claude_*"))
    base = newer[0] if newer else Path(os.environ["APPDATA"]) / "Claude"
    return base / "claude_desktop_config.json"


class Api:
    """Each method can be called from app.html as `window.pywebview.api.<name>()`."""

    def state(self):
        s = core.settings()
        connected = "metis" in core.load(claude_config(), {}).get("mcpServers", {})
        return {**s, "folders": [{"path": f, "name": " › ".join(Path(f).parts[-2:])} for f in s["folders"]],
                "connected": connected, "shared": core.shared_today()}

    def toggle(self):
        s = core.settings()
        s["on"] = not s["on"]
        core.save(core.SETTINGS, s)
        return self.state()

    def add_folder(self):
        picked = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        if picked:
            s = core.settings()
            s["folders"] = sorted(set(s["folders"]) | {picked[0]})
            core.save(core.SETTINGS, s)
            core.refresh()  # index the new folder now so the first search is quick
        return self.state()

    def remove_folder(self, folder):
        s = core.settings()
        s["folders"].remove(folder)
        core.save(core.SETTINGS, s)
        core.refresh()  # forget the removed folder's files
        return self.state()

    def connect(self):
        config = core.load(claude_config(), {})
        config.setdefault("mcpServers", {})["metis"] = {
            "command": str(Path(sys.executable).with_name("python.exe")),  # not pythonw.exe, which the shortcut uses
            "args": ["-m", "metis_mcp.server"],
            "env": {"PYTHONPATH": str(CONNECTOR)},
        }
        core.save(claude_config(), config)
        return self.state()

    def disconnect(self):
        config = core.load(claude_config(), {})
        config.get("mcpServers", {}).pop("metis", None)
        core.save(claude_config(), config)
        return self.state()

    def test(self, question):
        return core.retrieve(question)


if __name__ == "__main__":
    core.start_idle_unloader()  # the app embeds new files and runs Test; it must not hold the models forever
    webview.create_window("Metis", str(Path(__file__).with_name("app.html")), js_api=Api(), width=1100, height=720)
    webview.start()
