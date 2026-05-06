"""
Query tools for [QUERY:...] tags.

Executes read-only information queries and returns formatted results
for LLM consumption. The pipeline detects these tags, runs the query,
and feeds results back so the pony can actually USE the information.

Supported tags:
  [QUERY:FILE_TREE:path]       — directory tree with numbered entries
  [QUERY:FILE_TREE:path:depth] — same with custom max depth (default 3)
  [QUERY:CLIPBOARD_HISTORY]    — Windows 10 clipboard history (Win+V items)
  [QUERY:READ_NOTEPAD]         — text content of open Notepad window(s)
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_MAX_DEPTH = 3
_MAX_TREE_ITEMS = 150


# ── Public entry point ────────────────────────────────────────────────────────

def execute_query(query_tag: str) -> str:
    """
    Execute a raw query tag string like '[QUERY:FILE_TREE:C:/Users]'.
    Returns a human/LLM-readable result string.
    """
    inner = query_tag.strip("[]").strip()
    if inner.upper().startswith("QUERY:"):
        inner = inner[6:]

    # Split on first colon only — path may contain colons (C:\...)
    # But FILE_TREE:C:\foo needs special handling
    first_colon = inner.find(":")
    if first_colon == -1:
        qtype = inner.upper()
        rest = ""
    else:
        qtype = inner[:first_colon].upper()
        rest = inner[first_colon + 1:]

    if qtype == "FILE_TREE":
        return _file_tree(rest)
    elif qtype == "CLIPBOARD_HISTORY":
        return _clipboard_history()
    elif qtype == "READ_NOTEPAD":
        return _read_notepad()
    else:
        return f"[Unknown query type: {qtype}]"


# ── File tree ─────────────────────────────────────────────────────────────────

def _file_tree(arg: str) -> str:
    """Return a numbered hierarchical tree of a directory."""
    # arg may be "C:/Users" or "C:/Users:2" (path:depth)
    # Split off trailing :N depth if present
    max_depth = _DEFAULT_MAX_DEPTH
    path_str = arg.strip().strip("\"'")

    # If the last component looks like a digit, treat as depth
    if path_str:
        last_colon = path_str.rfind(":")
        # Check that it's not a Windows drive letter (single char before colon)
        if last_colon > 1:
            candidate = path_str[last_colon + 1:]
            if candidate.isdigit():
                max_depth = max(1, min(6, int(candidate)))
                path_str = path_str[:last_colon]

    if not path_str:
        path_str = os.path.expanduser("~")

    p = Path(path_str)
    if not p.exists():
        return f"Path not found: {path_str}"
    if not p.is_dir():
        try:
            size = p.stat().st_size
            return f"File: {p.name} ({_fmt_size(size)})\nFull path: {p.resolve()}"
        except OSError:
            return f"File: {p.name}\nFull path: {p.resolve()}"

    lines: List[str] = [f"ROOT: {p.resolve()}"]
    counter = [0]
    item_count = [0]

    def recurse(directory: Path, depth: int, parent_num: str) -> None:
        if depth > max_depth or item_count[0] >= _MAX_TREE_ITEMS:
            return
        try:
            raw_entries = list(directory.iterdir())
        except PermissionError:
            indent = "  " * depth
            lines.append(f"{indent}[permission denied]")
            return

        # Folders first, then files, both sorted alphabetically
        entries = sorted(raw_entries, key=lambda e: (not e.is_dir(), e.name.lower()))

        sibling = 0
        for entry in entries:
            if item_count[0] >= _MAX_TREE_ITEMS:
                indent = "  " * depth
                lines.append(f"{indent}... (truncated — use a narrower path or smaller depth)")
                return
            sibling += 1
            item_count[0] += 1

            # Build the dotted number: "1", "1.2", "1.2.3", etc.
            num = f"{parent_num}.{sibling}" if parent_num else str(sibling)

            # Depth arrows: "" at root, ">" one level in, ">>" two levels, etc.
            arrows = ">" * depth
            indent = "  " * depth

            if entry.is_dir():
                lines.append(f"{indent}[{arrows}{num}] {entry.name}/")
                recurse(entry, depth + 1, num)
            else:
                try:
                    size = entry.stat().st_size
                    lines.append(f"{indent}[{arrows}{num}] {entry.name} ({_fmt_size(size)})")
                except OSError:
                    lines.append(f"{indent}[{arrows}{num}] {entry.name}")

    recurse(p, 0, "")

    if item_count[0] >= _MAX_TREE_ITEMS:
        lines.append(f"\n(Showing first {_MAX_TREE_ITEMS} items. Narrow the path or reduce depth.)")

    lines.append(f"\n(Depth shown: {max_depth}. To go deeper: [QUERY:FILE_TREE:{path_str}:{max_depth + 1}])")
    return "\n".join(lines)


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / (1024 ** 2):.1f} MB"
    else:
        return f"{b / (1024 ** 3):.1f} GB"


# ── Clipboard history ─────────────────────────────────────────────────────────

def _clipboard_history() -> str:
    """Read Windows 10 clipboard history via WinRT through PowerShell."""
    ps = r"""
try {
    $null = [Windows.ApplicationModel.DataTransfer.Clipboard, Windows.ApplicationModel.DataTransfer, ContentType=WindowsRuntime]
    $task = [Windows.ApplicationModel.DataTransfer.Clipboard]::GetHistoryItemsAsync()
    $result = $task.GetAwaiter().GetResult()
    if ($result.Status -ne 0) {
        "Clipboard history unavailable (status $($result.Status)). Enable it in Settings > System > Clipboard."
        return
    }
    $items = $result.Items
    if ($items.Count -eq 0) {
        "Clipboard history is empty."
        return
    }
    $out = @()
    $i = 1
    foreach ($item in $items) {
        if ($i -gt 25) { break }
        $content = $item.Content
        if ($content.Contains('Text')) {
            try {
                $text = $content.GetTextAsync().GetAwaiter().GetResult()
                $text = $text.Trim()
                if ($text.Length -gt 600) { $text = $text.Substring(0, 600) + "..." }
                if ($text.Length -gt 0) {
                    $out += "[$i] $text"
                    $i++
                }
            } catch {}
        }
    }
    if ($out.Count -eq 0) { "No text items in clipboard history." }
    else { $out -join "`n---`n" }
} catch {
    "Error reading clipboard history: $_"
}
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=12,
        )
        out = proc.stdout.strip()
        if out:
            return out
        err = proc.stderr.strip()
        if err:
            return _clipboard_fallback(f"PowerShell error: {err[:150]}")
        return _clipboard_fallback("No output from PowerShell")
    except subprocess.TimeoutExpired:
        return _clipboard_fallback("clipboard history query timed out")
    except FileNotFoundError:
        return _clipboard_fallback("PowerShell not found")
    except Exception as exc:
        return _clipboard_fallback(str(exc))


def _clipboard_fallback(reason: str) -> str:
    """Fall back to reading just the current clipboard item."""
    try:
        import win32clipboard  # type: ignore
        win32clipboard.OpenClipboard()
        try:
            text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            preview = text[:1000] if len(text) > 1000 else text
            return (
                f"[Full history unavailable: {reason}]\n"
                f"Current clipboard:\n{preview}"
            )
        except Exception:
            return f"[Clipboard unreadable: {reason}]"
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        return f"[Cannot read clipboard: {reason} / {e}]"


# ── Read Notepad ──────────────────────────────────────────────────────────────

def _read_notepad() -> str:
    """Read text from any open Notepad window(s)."""
    results: List[Tuple[str, str]] = []

    # ── Strategy 1: classic Notepad (Windows 10, Edit control) ──────────────
    try:
        import win32gui  # type: ignore
        import win32con  # type: ignore
        import win32api  # type: ignore

        def _enum(hwnd: int, _: object) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            classname = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if classname == "Notepad":
                edit = win32gui.FindWindowEx(hwnd, 0, "Edit", None)
                if edit:
                    try:
                        length = win32api.SendMessage(edit, win32con.WM_GETTEXTLENGTH, 0, 0)
                        if length > 0:
                            buf = ctypes.create_unicode_buffer(length + 1)
                            win32api.SendMessage(edit, win32con.WM_GETTEXT, length + 1, buf)
                            content = buf.value
                            if content.strip():
                                results.append((title or "Notepad", content))
                    except Exception as exc:
                        logger.debug("Notepad Edit read failed: %s", exc)
            return True

        win32gui.EnumWindows(_enum, None)
    except Exception as exc:
        logger.debug("Classic Notepad scan failed: %s", exc)

    # ── Strategy 2: Windows 11 Notepad via UI Automation (PowerShell) ───────
    if not results:
        results.extend(_read_notepad_uia())

    if not results:
        return "No Notepad window found. Open Notepad and try again."

    parts = []
    for title, content in results[:3]:
        if len(content) > 5000:
            content = content[:5000] + "\n... (truncated at 5000 chars)"
        parts.append(f"=== {title} ===\n{content}")

    return "\n\n".join(parts)


def _read_notepad_uia() -> List[Tuple[str, str]]:
    """Read Notepad via UI Automation — works on Windows 11 new Notepad."""
    ps = r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$scope = [System.Windows.Automation.TreeScope]::Children
$desc = [System.Windows.Automation.TreeScope]::Descendants
$found = @()

# Find windows whose process name is "notepad"
$procs = Get-Process -Name "notepad" -ErrorAction SilentlyContinue
foreach ($proc in $procs) {
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
    $win = $root.FindFirst($scope, $cond)
    if ($null -eq $win) { continue }

    $title = $win.Current.Name

    # Look for a Document or Edit control type
    $docCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Document)
    $editCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Edit)

    foreach ($ctype in @($docCond, $editCond)) {
        $el = $win.FindFirst($desc, $ctype)
        if ($null -eq $el) { continue }
        try {
            $tp = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
            $text = $tp.DocumentRange.GetText(-1)
        } catch {
            try {
                $vp = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                $text = $vp.Current.Value
            } catch { $text = "" }
        }
        if ($text.Trim().Length -gt 0) {
            if ($text.Length -gt 5000) { $text = $text.Substring(0, 5000) + "..." }
            $found += "===TITLE===$title===CONTENT===$text===END==="
            break
        }
    }
}

if ($found.Count -eq 0) { "" } else { $found -join "`n|||`n" }
"""
    results: List[Tuple[str, str]] = []
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=12,
        )
        out = proc.stdout.strip()
        if not out:
            return results
        for chunk in out.split("\n|||\n"):
            chunk = chunk.strip()
            if "===TITLE===" in chunk and "===CONTENT===" in chunk:
                try:
                    title = chunk.split("===TITLE===")[1].split("===CONTENT===")[0]
                    content = chunk.split("===CONTENT===")[1].split("===END===")[0]
                    if content.strip():
                        results.append((title.strip(), content.strip()))
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("UIA Notepad read failed: %s", exc)
    return results
