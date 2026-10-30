"""Explicit reasoning controls; no arbitrary provider request extensions."""
from dataclasses import dataclass

from common.sanity_clustering import check


EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ReasoningSettings:
    mode: str = "default"
    protocol: str = "openai"
    effort: str = "default"

    def __post_init__(self):
        check(self.mode in ("default", "on", "off")
              and self.protocol in ("openai", "chat_template")
              and self.effort in ("default", *EFFORTS), "AI_REASONING_CONFIG")
        check((self.mode == "on" and self.protocol == "openai" and self.effort in EFFORTS)
              or (self.effort == "default" and (self.mode != "on" or self.protocol == "chat_template")),
              "AI_REASONING_CONFIG")

    @classmethod
    def from_config(cls, value):
        check(isinstance(value, dict) and set(value) == {"mode", "protocol", "effort"}, "AI_REASONING_CONFIG")
        return cls(**value)

    def public(self):
        return {"mode": self.mode, "protocol": self.protocol, "effort": self.effort}

    def validate_api(self, api):
        check(self.protocol != "chat_template" or api == "chat_completions", "AI_REASONING_CONFIG")

    def body(self, api):
        self.validate_api(api)
        if self.mode == "default":
            return {}
        if self.protocol == "chat_template":
            return {"chat_template_kwargs": {"enable_thinking": self.mode == "on"}}
        effort = "none" if self.mode == "off" else self.effort
        return {"reasoning_effort": effort} if api == "chat_completions" else {"reasoning": {"effort": effort}}


def validate_wire_reasoning(body, api):
    """Validate only supported wire keys; callers enforce their API allowlist."""
    keys = {"reasoning_effort", "reasoning", "chat_template_kwargs"} & set(body)
    check(len(keys) <= 1, "AI_VWP_SCHEMA")
    if "reasoning_effort" in body:
        check(api == "chat_completions" and body["reasoning_effort"] in ("none", *EFFORTS), "AI_VWP_SCHEMA")
    if "reasoning" in body:
        value = body["reasoning"]
        check(api == "responses" and isinstance(value, dict) and set(value) == {"effort"}
              and value["effort"] in ("none", *EFFORTS), "AI_VWP_SCHEMA")
    if "chat_template_kwargs" in body:
        value = body["chat_template_kwargs"]
        check(api == "chat_completions" and isinstance(value, dict) and set(value) == {"enable_thinking"}
              and type(value["enable_thinking"]) is bool, "AI_VWP_SCHEMA")
