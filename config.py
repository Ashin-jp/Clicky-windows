"""
config.py — Windows Clicky Configuration

All configurable constants live here. API keys are loaded from
environment variables. Copy .env.example to .env and fill in your keys.
"""

import os
from pathlib import Path

# ─── Load .env file if present (before any os.getenv calls) ─────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; use system env vars instead

# ─── Cloudflare Worker Proxy ──────────────────────────────────────────
# Replace with your deployed Worker URL after running `npx wrangler deploy`
WORKER_BASE_URL = os.getenv("WORKER_BASE_URL", "https://your-worker-name.your-subdomain.workers.dev")

CHAT_ENDPOINT = f"{WORKER_BASE_URL}/chat"
TTS_ENDPOINT = f"{WORKER_BASE_URL}/tts"
TRANSCRIBE_TOKEN_ENDPOINT = f"{WORKER_BASE_URL}/transcribe-token"

# ─── AI Provider Selection ────────────────────────────────────────────
# Set to "groq", "gemini", or "claude".
# Groq and Gemini work directly (no Worker needed). Claude requires Worker.
AI_PROVIDER = "groq"

# ─── Groq ─────────────────────────────────────────────────────────────
# Get your API key from https://console.groq.com/keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEFAULT_GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_MAX_TOKENS = 1024

# ─── Gemini ───────────────────────────────────────────────────────────
# Get your API key from https://aistudio.google.com/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_PRO_MODEL = "gemini-2.5-pro"
GEMINI_MAX_TOKENS = 1024

# ─── Claude ───────────────────────────────────────────────────────────
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
OPUS_CLAUDE_MODEL = "claude-opus-4-6"
CLAUDE_MAX_TOKENS = 1024
CLAUDE_TIMEOUT_SECONDS = 120

# ─── TTS Provider Selection ───────────────────────────────────────────
# Set to "edge" (free, no key) or "elevenlabs" (requires Worker proxy)
TTS_PROVIDER = "edge"

# ─── Edge TTS (Free — Microsoft Edge voices, no API key) ─────────────
EDGE_TTS_VOICE = "en-US-AriaNeural"  # Try: GuyNeural, JennyNeural, BrandonNeural

# ─── ElevenLabs TTS (requires Worker proxy) ──────────────────────────
TTS_MODEL_ID = "eleven_flash_v2_5"
TTS_STABILITY = 0.5
TTS_SIMILARITY_BOOST = 0.75

# ─── STT Provider Selection ──────────────────────────────────────────
# Set to "google_free" (free, no key) or "assemblyai" (requires Worker proxy)
STT_PROVIDER = "google_free"

# ─── AssemblyAI (requires Worker proxy) ──────────────────────────────
ASSEMBLYAI_WEBSOCKET_URL = "wss://streaming.assemblyai.com/v3/ws"
ASSEMBLYAI_SAMPLE_RATE = 16000
ASSEMBLYAI_SPEECH_MODEL = "u3-rt-pro"

# ─── Audio Capture ────────────────────────────────────────────────────
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_BLOCK_SIZE = 1024
AUDIO_DTYPE = "int16"

# ─── Screen Capture ──────────────────────────────────────────────────
SCREENSHOT_MAX_DIMENSION = 1280
SCREENSHOT_JPEG_QUALITY = 80

# ─── Push-to-Talk & Dictation ─────────────────────────────────────────
# Virtual key codes for Ctrl+Alt (AI) and Shift+Alt (Dictation)
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4  # Left Alt
VK_RMENU = 0xA5  # Right Alt
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1

PTT_DISPLAY_TEXT = "Ctrl + Alt"
DICTATION_DISPLAY_TEXT = "Shift + Alt"
# ─── Overlay ──────────────────────────────────────────────────────────
CURSOR_FOLLOW_INTERVAL_MS = 16  # ~60fps
CURSOR_OFFSET_X = 35
CURSOR_OFFSET_Y = 25
CURSOR_TRIANGLE_SIZE = 16

# ─── Conversation ────────────────────────────────────────────────────
MAX_CONVERSATION_HISTORY = 10

# ─── Onboarding ──────────────────────────────────────────────────────
ONBOARDING_VIDEO_URL = "https://stream.mux.com/e5jB8UuSrtFABVnTHCR7k3sIsmcUHCyhtLu1tzqLlfs.m3u8"
ONBOARDING_DEMO_TRIGGER_SECONDS = 40

# ─── App Metadata ────────────────────────────────────────────────────
APP_NAME = "Clicky"
APP_VERSION = "3.0.0"
APP_DESCRIPTION = "An AI companion that lives next to your cursor — with superpowers"

# ─── Storage (v2) ────────────────────────────────────────────────────
_default_data_dir = str(Path.home() / "Clicky")
CLICKY_DATA_DIR = os.getenv("CLICKY_DATA_DIR", _default_data_dir)
CLICKY_WORKSPACE_DIR = os.path.join(CLICKY_DATA_DIR, "workspace")
CLICKY_MACROS_DIR = os.path.join(CLICKY_DATA_DIR, "macros")
CLICKY_LOGS_DIR = os.path.join(CLICKY_DATA_DIR, "logs")

# ─── File Access Limits (v2) ─────────────────────────────────────────
MAX_FILE_READ_SIZE = 50_000        # Max chars to read into AI context
MAX_CMD_OUTPUT = 10_000            # Max chars from command output
MAX_CMD_TIMEOUT = 30               # Command execution timeout (seconds)

# ─── Web Fetch Limits (v2) ───────────────────────────────────────────
MAX_FETCH_CHARS = 15_000           # Max chars from fetched web pages
WEB_REQUEST_TIMEOUT = 15.0         # HTTP request timeout (seconds)

# ─── RAM Thresholds (v3) ─────────────────────────────────────────────
RAM_TOTAL_MB = 10_240              # 10GB total
RAM_AVAILABLE_TARGET_MB = 4_500    # Target available for Clicky
RAM_WARNING_PERCENT = 85.0         # Warn when RAM usage exceeds this
RAM_CRITICAL_PERCENT = 92.0        # Unload optional models when exceeded

# ─── Model Router Constants (v3) ─────────────────────────────────────
GROQ_RATE_LIMIT_BACKOFF_BASE = 60  # Base backoff in seconds
GROQ_RATE_LIMIT_BACKOFF_MAX = 300  # Max backoff in seconds
GROQ_MAX_FALLBACK_CHAIN = 4       # Number of models in fallback chain

# ─── Ambient Context (v3) ────────────────────────────────────────────
AMBIENT_POLL_INTERVAL = 30.0       # Seconds between context snapshots
AMBIENT_BUFFER_SIZE = 240          # Max entries in ring buffer (2 hours)

# ─── Focus Mode (v3) ─────────────────────────────────────────────────
DEFAULT_FOCUS_MINUTES = 25
FOCUS_NUDGE_DELAY = 10.0           # Seconds on distraction before nudge
FOCUS_NUDGE_COOLDOWN = 60.0        # Seconds between repeated nudges

# ─── Health Monitor (v3) ─────────────────────────────────────────────
HEALTH_POLL_INTERVAL = 10.0
CPU_WARN_THRESHOLD = 90.0
CPU_WARN_DURATION = 30             # Seconds of sustained high CPU
DISK_WARN_THRESHOLD = 95.0

# ─── Hotkey Display Text (v3) ────────────────────────────────────────
FOCUS_HOTKEY_TEXT = "Ctrl + Shift + F"
SCREEN_READ_HOTKEY_TEXT = "Ctrl + Shift + S"
HEALTH_HOTKEY_TEXT = "Ctrl + Shift + H"
MACRO_RECORD_HOTKEY_TEXT = "Ctrl + Shift + R"
WORKSPACE_HOTKEY_TEXT = "Ctrl + Shift + W"

