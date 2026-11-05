from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict

from screening.application.ports import SerializedMessage

ROLES = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}


def to_serialized(messages: list[BaseMessage]) -> list[SerializedMessage]:
    return [SerializedMessage(ROLES[item["type"]], item) for item in messages_to_dict(messages)]


def from_serialized(items: list[SerializedMessage]) -> list[BaseMessage]:
    return messages_from_dict([item.content for item in items])
