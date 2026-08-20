"""
analytics.py — PostHog Analytics Stub

Stubbed-out analytics matching the macOS ClickyAnalytics.swift interface.
All methods are no-ops that can be wired to a real PostHog SDK later.
"""

import logging

logger = logging.getLogger(__name__)


class ClickyAnalytics:
    """PostHog analytics stub. All methods log the event name but send nothing."""

    _initialized = False

    @classmethod
    def initialize(cls):
        """Initialize analytics (no-op stub)."""
        if cls._initialized:
            return
        cls._initialized = True
        logger.debug("Analytics: initialized (stub — no data is sent)")

    @classmethod
    def _track(cls, event_name: str, properties: dict | None = None):
        """Internal tracking method. Logs the event for debugging."""
        props_str = f" {properties}" if properties else ""
        logger.debug(f"Analytics: {event_name}{props_str}")

    # ─── Push-to-Talk ─────────────────────────────────────────────────

    @classmethod
    def track_push_to_talk_started(cls):
        cls._track("push_to_talk_started")

    @classmethod
    def track_push_to_talk_released(cls):
        cls._track("push_to_talk_released")

    # ─── AI Response ──────────────────────────────────────────────────

    @classmethod
    def track_user_message_sent(cls, transcript: str):
        cls._track("user_message_sent", {"transcript_length": len(transcript)})

    @classmethod
    def track_ai_response_received(cls, response: str):
        cls._track("ai_response_received", {"response_length": len(response)})

    @classmethod
    def track_response_error(cls, error: str):
        cls._track("response_error", {"error": error})

    # ─── TTS ──────────────────────────────────────────────────────────

    @classmethod
    def track_tts_error(cls, error: str):
        cls._track("tts_error", {"error": error})

    # ─── Element Pointing ─────────────────────────────────────────────

    @classmethod
    def track_element_pointed(cls, element_label: str | None):
        cls._track("element_pointed", {"label": element_label or "unknown"})

    # ─── Permissions ──────────────────────────────────────────────────

    @classmethod
    def track_permission_granted(cls, permission: str):
        cls._track("permission_granted", {"permission": permission})

    @classmethod
    def track_all_permissions_granted(cls):
        cls._track("all_permissions_granted")

    # ─── Onboarding ──────────────────────────────────────────────────

    @classmethod
    def track_onboarding_started(cls):
        cls._track("onboarding_started")

    @classmethod
    def track_onboarding_video_completed(cls):
        cls._track("onboarding_video_completed")

    @classmethod
    def track_onboarding_demo_triggered(cls):
        cls._track("onboarding_demo_triggered")

    @classmethod
    def track_onboarding_replayed(cls):
        cls._track("onboarding_replayed")
