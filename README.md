# 🖱️ Clicky for Windows

**An AI companion that lives in your system tray — voice-first, screen-aware, with full desktop automation superpowers.**

Talk to Clicky with push-to-talk, and it sees your screen, speaks back, points at UI elements with an animated cursor, automates tasks, browses the web, and controls your computer — all through natural conversation.

[![Tests](https://github.com/Ashin-jp/Clicky-windows/actions/workflows/tests.yml/badge.svg)](https://github.com/Ashin-jp/Clicky-windows/actions/workflows/tests.yml)

---

## ✨ Features

### Voice Interaction
- **Push-to-Talk** (`Ctrl+Alt`) — speak naturally, get spoken responses
- **Dictation Mode** (`Shift+Alt`) — voice typing into any application with smart corrections
- **Silent/Chat Mode** (`Ctrl+Shift+Alt`) — text chat overlay when you can't speak

### Screen Vision
- Captures all monitors and sends screenshots with every AI interaction
- AI references specific UI elements, buttons, and text it sees on your screen
- **Visual Finder** (`Ctrl+Shift+V`) — locates any UI element using two-pass LLM zoom + pulsing highlight overlay

### Animated Cursor Companion
- A small blue triangle cursor follows your real cursor
- When the AI points at something, the cursor **flies via Bézier curve** to the target element
- Displays speech bubbles, waveform animations during recording, and processing spinners

### 60+ Desktop Actions
| Category | Actions |
|---|---|
| **Screen** | Click, scroll, drag, right-click, screenshot regions |
| **Files** | Create, read, write, search files, run shell commands |
| **Web** | Fetch URLs, download files, summarize pages |
| **Browser** | Full Playwright automation — search, navigate, click, type, read pages, fill forms |
| **Apps** | Close, switch, restart apps, control per-app volume |
| **Knowledge** | Explain, translate, generate code, quiz, step guides |
| **Automation** | Record/replay macros, watch folders, schedule tasks |
| **Workspace** | Save and restore entire window layouts |

### Safety & Trust Engine
- **4-tier trust model**: Safe actions run silently → medium-risk confirm once → high-risk confirm every time → dangerous actions blocked
- File access classification (public/project/sensitive/system/forbidden)
- Dangerous commands (`format`, `rm -rf`, `shutdown`, etc.) automatically refused

### Multi-Provider AI
- **Groq** (default, free) — LLaMA 4 Scout with automatic model fallback cascade
- **Google Gemini** — Gemini 2.5 Flash/Pro
- **Anthropic Claude** — via Cloudflare Worker proxy

### Ambient Intelligence
- Focus mode with distraction nudges (Pomodoro-style)
- System health monitoring (CPU, RAM, disk, battery)
- Proactive suggestions based on usage patterns
- Clipboard monitoring and context-aware responses

---

## 🏗️ Architecture

```
┌────────────────────────────────────────────────────────┐
│                   main.py (Entry Point)                 │
│  QApplication + asyncio loop + signal wiring            │
└───────────┬──────────────┬──────────────┬──────────────┘
            │              │              │
  ┌─────────▼───┐  ┌──────▼─────┐  ┌─────▼──────────┐
  │ SystemTray   │  │FloatingPanel│  │CompanionManager │
  │ (tray icon)  │  │(settings)  │  │(central brain)  │
  └──────────────┘  └────────────┘  └────────┬────────┘
                                             │
     ┌────────┬─────────┬─────────┬──────────┼──────────┐
     │        │         │         │          │          │
 GlobalHotkey Audio  Screen   GroqRouter  Overlay   Actions
 (Win32 hook) Capture Capture  (LLM)     Window   (executors)
```

### Key Modules

| Module | Purpose |
|---|---|
| `companion_manager.py` | Central state machine — orchestrates voice lifecycle, AI calls, action dispatch |
| `groq_router.py` | Multi-model AI router with task classification and automatic fallback cascade |
| `screen_capture.py` | Multi-monitor capture via mss, scales and encodes for vision API |
| `overlay_window.py` | Transparent, click-through overlay with animated cursor and speech bubbles |
| `global_hotkey.py` | Low-level Win32 keyboard hook for system-wide hotkeys |
| `actions.py` + `executors/` | Parses AI action tags and routes through modular executor registry |
| `trust_engine.py` | 4-tier safety classification for every action |
| `browser_controller.py` | Full Playwright-based web automation with persistent login |
| `intent_router.py` | Local intent classifier (zero API calls) with keyword trie + regex |
| `visual_finder.py` | Two-pass LLM vision locator with quadrant zoom and highlight overlay |
| `storage.py` | SQLite persistence for macros, schedules, trust approvals, analytics |
| `chat_overlay.py` | Silent mode text chat interface |

---

## 🚀 Setup & Installation

### Prerequisites
- **Python 3.12+**
- **Windows 10/11**
- At least one AI provider API key (Groq is free and recommended)

### 1. Clone the repository
```bash
git clone https://github.com/Ashin-jp/Clicky-windows.git
cd Clicky-windows
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

For browser automation (optional):
```bash
pip install playwright
python -m playwright install chromium
```

### 3. Configure API keys

Copy the example environment file and add your keys:
```bash
cp .env.example .env
```

Edit `.env` with your API keys:
```env
# Required (at least one):
GROQ_API_KEY=gsk_your_key_here        # https://console.groq.com/keys
GEMINI_API_KEY=your_key_here           # https://aistudio.google.com/apikey

# Optional:
WORKER_BASE_URL=https://your-worker.workers.dev  # For Claude + ElevenLabs
```

### 4. Run
```bash
python main.py
```

Clicky appears in your system tray. Hold `Ctrl+Alt` to talk.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12+ |
| UI Framework | PySide6 (Qt6) |
| AI Providers | Groq, Google Gemini, Anthropic Claude |
| Vision | Multimodal LLM (LLaMA 4 Scout) |
| STT | Google Free / AssemblyAI |
| TTS | Microsoft Edge TTS / ElevenLabs |
| Screen Capture | mss (GDI/BitBlt) |
| Browser Automation | Playwright (Chromium) |
| Database | SQLite (WAL mode) |
| Audio | sounddevice (WASAPI) + pygame |
| Global Hotkeys | Win32 low-level keyboard hook |

---

## 🧪 Testing

Run the test suite:
```bash
python -m pytest tests/ -v
```

The test suite includes 99 unit tests covering:
- Groq router model cascading and fallback
- Intent classification and command parsing
- STT correction engine
- Trust engine safety levels
- File access classification
- Visual finder coordinate mapping

All tests run with mocked PySide6/Win32 dependencies (no GUI needed).

---

## 🙏 Credits & Attribution

This project is a **Windows port** of the original macOS **Clicky** built by [Farza](https://github.com/farzaa/clicky).

### What this port adds/changes:
- Complete rewrite from Swift → Python for Windows
- PySide6 (Qt6) UI instead of SwiftUI
- Win32 low-level keyboard hooks for global hotkeys
- Multi-monitor support via mss (GDI/BitBlt)
- Modular action executor registry (60+ action types)
- 4-tier trust engine with file access classification
- Full Playwright browser automation
- Visual Finder with two-pass LLM zoom + highlight overlay
- Silent/chat mode overlay
- Focus mode, health monitoring, ambient intelligence
- Macro recording, workspace saving, task scheduling
- SQLite persistence layer
- 99-test unit test suite with CI

---

## 📄 License

[MIT License](LICENSE) — see [LICENSE](LICENSE) for details.

Original macOS version © 2026 [Farza](https://github.com/farzaa/clicky)
Windows port © 2026 [Ashin-jp](https://github.com/Ashin-jp)
