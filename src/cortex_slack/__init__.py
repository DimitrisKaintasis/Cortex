"""Slack reference connector built only on the public Cortex SDK."""

from cortex_slack.connector import SlackConnector, SlackSyncRejected
from cortex_slack.mapper import SlackConnectorConfig, SlackMapper, SlackMessagePointer
from cortex_slack.models import (
    SlackDeletion,
    SlackEventPage,
    SlackMessage,
    event_pages_from_mapping,
)

__all__ = [
    "SlackConnector",
    "SlackConnectorConfig",
    "SlackDeletion",
    "SlackEventPage",
    "SlackMapper",
    "SlackMessage",
    "SlackMessagePointer",
    "SlackSyncRejected",
    "event_pages_from_mapping",
]
