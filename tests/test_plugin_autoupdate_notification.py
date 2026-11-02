"""`usePluginAutoupdateNotification`: the REPL's notice after a plugin autoupdate."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import app.utils.plugins.plugin_autoupdate as autoupdate
from app.hooks.notifs.use_plugin_autoupdate_notification import (
    plugin_autoupdate_notification_text,
    plugin_display_name,
    use_plugin_autoupdate_notification,
)


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    autoupdate._reset_for_tests()
    yield
    autoupdate._reset_for_tests()


def test_names_are_the_id_before_the_at() -> None:
    assert plugin_display_name("lint@team") == "lint"
    assert plugin_display_name("bare") == "bare"
    assert plugin_display_name("@scoped") == "@scoped"


def test_the_notice_names_one_or_two_plugins_and_counts_more() -> None:
    assert plugin_autoupdate_notification_text(["a@m"]) == "Plugin updated: a · Run /reload-plugins to apply"
    assert plugin_autoupdate_notification_text(["a@m", "b@n"]) == (
        "Plugins updated: a and b · Run /reload-plugins to apply")
    assert plugin_autoupdate_notification_text(["a@m", "b@n", "c@o"]) == (
        "Plugins updated: 3 plugins · Run /reload-plugins to apply")


def test_each_batch_is_written_while_subscribed() -> None:
    written: list[str] = []
    unsubscribe = use_plugin_autoupdate_notification(written.append)
    assert autoupdate._plugin_update_callback is not None
    autoupdate._plugin_update_callback(["a@m"])
    autoupdate._plugin_update_callback([])
    assert written == ["Plugin updated: a · Run /reload-plugins to apply"]
    unsubscribe()
    assert autoupdate._plugin_update_callback is None


def test_an_update_finished_before_the_repl_subscribed_is_shown_at_once() -> None:
    autoupdate._pending_notification = ["x@m", "y@m"]
    written: list[str] = []
    use_plugin_autoupdate_notification(written.append)
    assert written == ["Plugins updated: x and y · Run /reload-plugins to apply"]
