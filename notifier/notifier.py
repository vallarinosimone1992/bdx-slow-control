#!/usr/bin/env python3
"""Configurable EPICS-to-Telegram alarm notifier for BDX."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from html import escape
import json
import logging
import math
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time
from typing import Any, Literal

from caproto.threading.client import Context
from dotenv import load_dotenv
import requests


LOG = logging.getLogger("bdx-notifier")
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = BASE_DIR / "config.env"
DEFAULT_CONFIG_FILE = BASE_DIR / "alarms.json"
LEVEL_RANK = {"MINOR": 1, "MAJOR": 2, "INTERLOCK": 3}


class ConfigurationError(ValueError):
    """Raised when alarms.json is invalid."""


class TelegramDeliveryError(RuntimeError):
    """Raised without exposing the bot token in an exception message."""


@dataclass(frozen=True)
class NumericPolicy:
    minor_percent: float = 5.0
    minor_seconds: float = 5.0
    major_percent: float = 10.0
    major_seconds: float = 0.0
    major_sustained_percent: float = 5.0
    major_sustained_seconds: float = 20.0
    recovery_seconds: float = 5.0
    interlock_percent: float | None = None
    interlock_seconds: float | None = None


@dataclass(frozen=True)
class AlarmStage:
    level: Literal["MINOR", "MAJOR", "INTERLOCK"]
    after_seconds: float


@dataclass(frozen=True)
class Condition:
    pv: str
    operator: Literal["eq", "ne", "truthy", "falsy"]
    value: Any = None

    @property
    def required_pvs(self) -> set[str]:
        return {self.pv}


@dataclass(frozen=True)
class Person:
    user_id: int
    name: str


@dataclass(frozen=True)
class TelegramPolicy:
    people: dict[int, Person]
    major_people: tuple[int, ...]
    interlock_people: Literal["all"] | tuple[int, ...]
    verify_membership: bool = True
    membership_cache_seconds: float = 300.0


@dataclass(frozen=True)
class NumericRule:
    rule_id: str
    label: str
    pv: str
    mode: Literal["deviation", "above", "below", "ratio"]
    reference_pv: str | None
    reference_value: float | None
    policy: NumericPolicy
    conditions: tuple[Condition, ...] = ()
    group: str | None = None
    optional: bool = False

    @property
    def required_pvs(self) -> set[str]:
        result = {self.pv}
        if self.reference_pv:
            result.add(self.reference_pv)
        for condition in self.conditions:
            result.update(condition.required_pvs)
        return result


@dataclass(frozen=True)
class StateRule:
    rule_id: str
    label: str
    pv: str
    alarm_when: bool
    level: Literal["MAJOR", "INTERLOCK"]
    activation_seconds: float
    recovery_seconds: float
    limit_description: str
    stages: tuple[AlarmStage, ...] = ()
    operator: Literal["eq", "ne", "truthy", "falsy"] = "eq"
    conditions: tuple[Condition, ...] = ()
    group: str | None = None
    optional: bool = False

    @property
    def required_pvs(self) -> set[str]:
        result = {self.pv}
        for condition in self.conditions:
            result.update(condition.required_pvs)
        return result

    @property
    def effective_stages(self) -> tuple[AlarmStage, ...]:
        return self.stages or (AlarmStage(self.level, self.activation_seconds),)


@dataclass(frozen=True)
class StaleRule:
    rule_id: str
    label: str
    pv: str
    stale_after_seconds: float
    timestamp_mode: Literal["change", "update"]
    stages: tuple[AlarmStage, ...]
    recovery_seconds: float
    limit_description: str
    conditions: tuple[Condition, ...] = ()
    group: str | None = None
    optional: bool = False

    @property
    def required_pvs(self) -> set[str]:
        result = {self.pv}
        for condition in self.conditions:
            result.update(condition.required_pvs)
        return result


@dataclass(frozen=True)
class ComparisonRule:
    rule_id: str
    label: str
    pv: str
    reference_pv: str | None
    reference_value: Any
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    stages: tuple[AlarmStage, ...]
    recovery_seconds: float
    limit_description: str
    conditions: tuple[Condition, ...] = ()
    group: str | None = None
    optional: bool = False

    @property
    def required_pvs(self) -> set[str]:
        result = {self.pv}
        if self.reference_pv:
            result.add(self.reference_pv)
        for condition in self.conditions:
            result.update(condition.required_pvs)
        return result


@dataclass(frozen=True)
class RangeRule:
    rule_id: str
    label: str
    pv: str
    minimum: float | None
    maximum: float | None
    stages: tuple[AlarmStage, ...]
    recovery_seconds: float
    conditions: tuple[Condition, ...] = ()
    group: str | None = None
    optional: bool = False

    @property
    def required_pvs(self) -> set[str]:
        result = {self.pv}
        for condition in self.conditions:
            result.update(condition.required_pvs)
        return result


Rule = NumericRule | StateRule | StaleRule | ComparisonRule | RangeRule


@dataclass(frozen=True)
class NotifierConfig:
    defaults: NumericPolicy
    telegram: TelegramPolicy
    rules: tuple[Rule, ...]

    @property
    def required_pvs(self) -> tuple[str, ...]:
        names: set[str] = set()
        for rule in self.rules:
            names.update(rule.required_pvs)
        return tuple(sorted(names))

    @property
    def mandatory_pvs(self) -> tuple[str, ...]:
        names: set[str] = set()
        for rule in self.rules:
            if not rule.optional:
                names.update(rule.required_pvs)
        return tuple(sorted(names))


@dataclass
class RuleState:
    level: str | None = None
    notified: bool = False
    breach_started_at: float | None = None
    direct_major_started_at: float | None = None
    interlock_started_at: float | None = None
    activation_started_at: float | None = None
    recovery_started_at: float | None = None
    last_value: Any = None
    last_reference: Any = None
    last_deviation_percent: float | None = None
    last_limit: str = ""


@dataclass(frozen=True)
class AlarmEvent:
    rule_id: str
    label: str
    pv: str
    level: str
    resolved: bool
    value: Any
    limit: str
    deviation_percent: float | None = None
    group: str | None = None


def _number(value: Any, field_name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ConfigurationError(f"{field_name} must be finite and >= {minimum:g}")
    return result


def _policy_from_mapping(raw: dict[str, Any], base: NumericPolicy | None = None) -> NumericPolicy:
    base = base or NumericPolicy()
    values = {}
    for name in NumericPolicy.__dataclass_fields__:
        value = raw.get(name, getattr(base, name))
        if name in {"interlock_percent", "interlock_seconds"} and value is None:
            values[name] = None
        else:
            values[name] = _number(value, name)
    policy = NumericPolicy(**values)
    if policy.major_percent < policy.minor_percent:
        raise ConfigurationError("major_percent must be >= minor_percent")
    if policy.major_sustained_percent < policy.minor_percent:
        raise ConfigurationError("major_sustained_percent must be >= minor_percent")
    if (policy.interlock_percent is None) != (policy.interlock_seconds is None):
        raise ConfigurationError(
            "interlock_percent and interlock_seconds must be configured together"
        )
    return policy


def _parse_conditions(item: dict[str, Any], rule_id: str) -> tuple[Condition, ...]:
    raw_conditions = item.get("conditions", [])
    if not isinstance(raw_conditions, list):
        raise ConfigurationError(f"{rule_id}.conditions must be a list")
    conditions: list[Condition] = []
    for raw in raw_conditions:
        if not isinstance(raw, dict):
            raise ConfigurationError(f"{rule_id}.conditions entries must be objects")
        operator = raw.get("operator", "eq")
        if operator not in {"eq", "ne", "truthy", "falsy"}:
            raise ConfigurationError(f"Invalid condition operator for {rule_id}: {operator}")
        if operator in {"eq", "ne"} and "value" not in raw:
            raise ConfigurationError(f"{rule_id} condition {operator} requires value")
        conditions.append(
            Condition(
                pv=_required_text(raw, "pv"),
                operator=operator,
                value=raw.get("value"),
            )
        )
    return tuple(conditions)


def _parse_stages(
    item: dict[str, Any],
    rule_id: str,
    *,
    default: tuple[AlarmStage, ...],
) -> tuple[AlarmStage, ...]:
    raw_stages = item.get("stages")
    if raw_stages is None:
        return default
    if not isinstance(raw_stages, list) or not raw_stages:
        raise ConfigurationError(f"{rule_id}.stages must be a non-empty list")
    stages: list[AlarmStage] = []
    previous_rank = 0
    previous_time = -1.0
    for raw in raw_stages:
        if not isinstance(raw, dict):
            raise ConfigurationError(f"{rule_id}.stages entries must be objects")
        level = raw.get("level")
        if level not in LEVEL_RANK:
            raise ConfigurationError(f"Invalid alarm stage level for {rule_id}: {level}")
        after = _number(raw.get("after_seconds", 0), f"{rule_id}.stage.after_seconds")
        if LEVEL_RANK[level] <= previous_rank or after < previous_time:
            raise ConfigurationError(
                f"{rule_id}.stages must increase in severity and time"
            )
        stages.append(AlarmStage(level, after))
        previous_rank = LEVEL_RANK[level]
        previous_time = after
    return tuple(stages)


def _common_rule_fields(item: dict[str, Any], rule_id: str) -> dict[str, Any]:
    group = item.get("group")
    if group is not None and (not isinstance(group, str) or not group.strip()):
        raise ConfigurationError(f"{rule_id}.group must be a non-empty string")
    optional = item.get("optional", False)
    if not isinstance(optional, bool):
        raise ConfigurationError(f"{rule_id}.optional must be boolean")
    return {
        "conditions": _parse_conditions(item, rule_id),
        "group": group.strip() if isinstance(group, str) else None,
        "optional": optional,
    }


def _required_text(raw: dict[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be a non-empty string")
    return value.strip()


def _parse_people(raw: dict[str, Any]) -> TelegramPolicy:
    people: dict[int, Person] = {}
    for item in raw.get("people", []):
        if not isinstance(item, dict):
            raise ConfigurationError("telegram.people entries must be objects")
        user_id = item.get("user_id")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ConfigurationError("telegram people user_id must be a positive integer")
        people[user_id] = Person(user_id=user_id, name=_required_text(item, "name"))

    def recipient_list(name: str) -> tuple[int, ...]:
        value = raw.get(name, [])
        if not isinstance(value, list) or any(
            isinstance(item, bool) or not isinstance(item, int) for item in value
        ):
            raise ConfigurationError(f"telegram.{name} must be a list of numeric user IDs")
        unknown = sorted(set(value).difference(people))
        if unknown:
            raise ConfigurationError(f"telegram.{name} contains unknown user IDs: {unknown}")
        return tuple(dict.fromkeys(value))

    interlock_raw = raw.get("interlock_people", "all")
    if interlock_raw == "all":
        interlock_people: Literal["all"] | tuple[int, ...] = "all"
    else:
        interlock_people = recipient_list("interlock_people")
    return TelegramPolicy(
        people=people,
        major_people=recipient_list("major_people"),
        interlock_people=interlock_people,
        verify_membership=bool(raw.get("verify_membership", True)),
        membership_cache_seconds=_number(
            raw.get("membership_cache_seconds", 300),
            "telegram.membership_cache_seconds",
        ),
    )


def telegram_policy_from_environment(policy: TelegramPolicy) -> TelegramPolicy:
    """Apply private Telegram recipient overrides from config.env."""
    people_value = os.getenv("TELEGRAM_PEOPLE", "").strip()
    major_value = os.getenv("TELEGRAM_MAJOR_PEOPLE", "").strip()
    if not people_value and not major_value:
        return policy

    people = dict(policy.people)
    if people_value:
        people = {}
        for entry in people_value.split(","):
            raw_id, separator, raw_name = entry.strip().partition(":")
            if not separator or not raw_id.strip() or not raw_name.strip():
                raise ConfigurationError(
                    "TELEGRAM_PEOPLE must contain comma-separated user_id:name entries"
                )
            try:
                user_id = int(raw_id)
            except ValueError as exc:
                raise ConfigurationError(
                    "TELEGRAM_PEOPLE user IDs must be positive integers"
                ) from exc
            if user_id <= 0:
                raise ConfigurationError(
                    "TELEGRAM_PEOPLE user IDs must be positive integers"
                )
            people[user_id] = Person(user_id=user_id, name=raw_name.strip())

    major_people = policy.major_people
    if major_value:
        try:
            major_people = tuple(
                dict.fromkeys(int(item.strip()) for item in major_value.split(","))
            )
        except ValueError as exc:
            raise ConfigurationError(
                "TELEGRAM_MAJOR_PEOPLE must contain comma-separated numeric user IDs"
            ) from exc
    unknown = sorted(set(major_people).difference(people))
    if unknown:
        raise ConfigurationError(
            f"TELEGRAM_MAJOR_PEOPLE contains unknown user IDs: {unknown}"
        )
    return replace(policy, people=people, major_people=major_people)


def _render_template(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("{") and value.endswith("}") and value.count("{") == 1:
            key = value[1:-1]
            if key in variables:
                return variables[key]
        return value.format_map(variables)
    if isinstance(value, list):
        return [_render_template(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _render_template(item, variables) for key, item in value.items()}
    return value


def _expanded_alarm_items(raw: dict[str, Any]) -> list[Any]:
    alarm_items = raw.get("alarms", [])
    templates = raw.get("alarm_templates", [])
    if not isinstance(alarm_items, list) or not isinstance(templates, list):
        raise ConfigurationError("alarms and alarm_templates must be lists")
    result = list(alarm_items)
    for template in templates:
        if not isinstance(template, dict):
            raise ConfigurationError("alarm_templates entries must be objects")
        instances = template.get("for_each")
        rules = template.get("alarms")
        if not isinstance(instances, list) or not isinstance(rules, list):
            raise ConfigurationError(
                "alarm template for_each and alarms fields must be lists"
            )
        for variables in instances:
            if not isinstance(variables, dict):
                raise ConfigurationError("alarm template for_each entries must be objects")
            for rule in rules:
                result.append(_render_template(rule, variables))
    return result


def load_config(path: Path) -> NotifierConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Alarm configuration not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ConfigurationError("alarms.json must be an object with version 1")

    defaults_raw = raw.get("defaults", {})
    telegram_raw = raw.get("telegram", {})
    alarm_items = _expanded_alarm_items(raw)
    if not isinstance(defaults_raw, dict) or not isinstance(telegram_raw, dict):
        raise ConfigurationError("defaults and telegram must be objects")
    defaults = _policy_from_mapping(defaults_raw)
    rules: list[Rule] = []
    seen_ids: set[str] = set()

    for item in alarm_items:
        if not isinstance(item, dict):
            raise ConfigurationError("Each alarm entry must be an object")
        if not bool(item.get("enabled", True)):
            continue
        rule_id = _required_text(item, "id")
        if rule_id in seen_ids:
            raise ConfigurationError(f"Duplicate alarm id: {rule_id}")
        seen_ids.add(rule_id)
        label = _required_text(item, "label")
        pv = _required_text(item, "pv")
        kind = item.get("kind")

        common = _common_rule_fields(item, rule_id)

        if kind == "numeric":
            mode = item.get("mode", "deviation")
            if mode not in {"deviation", "above", "below", "ratio"}:
                raise ConfigurationError(f"Invalid numeric mode for {rule_id}: {mode}")
            reference = item.get("reference")
            if not isinstance(reference, dict):
                raise ConfigurationError(f"Numeric alarm {rule_id} requires reference")
            reference_pv = reference.get("pv")
            reference_value = reference.get("value")
            if (reference_pv is None) == (reference_value is None):
                raise ConfigurationError(
                    f"Numeric alarm {rule_id} reference needs exactly one of pv or value"
                )
            if reference_pv is not None:
                if not isinstance(reference_pv, str) or not reference_pv.strip():
                    raise ConfigurationError(f"Invalid reference PV for {rule_id}")
                reference_pv = reference_pv.strip()
            else:
                reference_value = _number(reference_value, f"{rule_id}.reference.value")
            overrides = item.get("overrides", {})
            if not isinstance(overrides, dict):
                raise ConfigurationError(f"{rule_id}.overrides must be an object")
            rules.append(
                NumericRule(
                    rule_id=rule_id,
                    label=label,
                    pv=pv,
                    mode=mode,
                    reference_pv=reference_pv,
                    reference_value=reference_value,
                    policy=_policy_from_mapping(overrides, defaults),
                    **common,
                )
            )
        elif kind in {"state", "interlock"}:
            level = "INTERLOCK" if kind == "interlock" else item.get("level", "MAJOR")
            if level not in {"MAJOR", "INTERLOCK"}:
                raise ConfigurationError(f"Invalid state level for {rule_id}: {level}")
            alarm_when = item.get("alarm_when", True)
            if not isinstance(alarm_when, bool):
                raise ConfigurationError(f"{rule_id}.alarm_when must be boolean")
            state_operator = item.get("operator", "eq")
            if state_operator not in {"eq", "ne", "truthy", "falsy"}:
                raise ConfigurationError(
                    f"Invalid state operator for {rule_id}: {state_operator}"
                )
            activation_seconds = _number(
                item.get("activation_seconds", 0),
                f"{rule_id}.activation_seconds",
            )
            default_stage = (AlarmStage(level, activation_seconds),)
            rules.append(
                StateRule(
                    rule_id=rule_id,
                    label=label,
                    pv=pv,
                    alarm_when=alarm_when,
                    level=level,
                    activation_seconds=activation_seconds,
                    recovery_seconds=_number(
                        item.get("recovery_seconds", defaults.recovery_seconds),
                        f"{rule_id}.recovery_seconds",
                    ),
                    limit_description=_required_text(item, "limit_description"),
                    stages=_parse_stages(item, rule_id, default=default_stage),
                    operator=state_operator,
                    **common,
                )
            )
        elif kind == "stale":
            timestamp_mode = item.get("timestamp_mode", "change")
            if timestamp_mode not in {"change", "update"}:
                raise ConfigurationError(
                    f"Invalid stale timestamp_mode for {rule_id}: {timestamp_mode}"
                )
            rules.append(
                StaleRule(
                    rule_id=rule_id,
                    label=label,
                    pv=pv,
                    stale_after_seconds=_number(
                        item.get("stale_after_seconds"),
                        f"{rule_id}.stale_after_seconds",
                    ),
                    timestamp_mode=timestamp_mode,
                    stages=_parse_stages(
                        item,
                        rule_id,
                        default=(AlarmStage("MINOR", 0), AlarmStage("MAJOR", 15)),
                    ),
                    recovery_seconds=_number(
                        item.get("recovery_seconds", defaults.recovery_seconds),
                        f"{rule_id}.recovery_seconds",
                    ),
                    limit_description=_required_text(item, "limit_description"),
                    **common,
                )
            )
        elif kind == "comparison":
            operator = item.get("operator", "eq")
            if operator not in {"eq", "ne", "gt", "gte", "lt", "lte"}:
                raise ConfigurationError(
                    f"Invalid comparison operator for {rule_id}: {operator}"
                )
            reference = item.get("reference")
            if not isinstance(reference, dict):
                raise ConfigurationError(f"Comparison {rule_id} requires reference")
            reference_pv = reference.get("pv")
            has_value = "value" in reference
            if (reference_pv is None) == (not has_value):
                raise ConfigurationError(
                    f"Comparison {rule_id} reference needs exactly one of pv or value"
                )
            rules.append(
                ComparisonRule(
                    rule_id=rule_id,
                    label=label,
                    pv=pv,
                    reference_pv=(
                        _required_text(reference, "pv") if reference_pv is not None else None
                    ),
                    reference_value=reference.get("value"),
                    operator=operator,
                    stages=_parse_stages(
                        item,
                        rule_id,
                        default=(AlarmStage("MINOR", 5), AlarmStage("MAJOR", 20)),
                    ),
                    recovery_seconds=_number(
                        item.get("recovery_seconds", defaults.recovery_seconds),
                        f"{rule_id}.recovery_seconds",
                    ),
                    limit_description=_required_text(item, "limit_description"),
                    **common,
                )
            )
        elif kind == "range":
            minimum = item.get("minimum")
            maximum = item.get("maximum")
            if minimum is None and maximum is None:
                raise ConfigurationError(f"Range {rule_id} needs minimum or maximum")
            minimum_value = float(minimum) if minimum is not None else None
            maximum_value = float(maximum) if maximum is not None else None
            if (
                minimum_value is not None
                and maximum_value is not None
                and minimum_value >= maximum_value
            ):
                raise ConfigurationError(f"Range {rule_id} minimum must be below maximum")
            rules.append(
                RangeRule(
                    rule_id=rule_id,
                    label=label,
                    pv=pv,
                    minimum=minimum_value,
                    maximum=maximum_value,
                    stages=_parse_stages(
                        item,
                        rule_id,
                        default=(AlarmStage("MAJOR", 0),),
                    ),
                    recovery_seconds=_number(
                        item.get("recovery_seconds", defaults.recovery_seconds),
                        f"{rule_id}.recovery_seconds",
                    ),
                    **common,
                )
            )
        else:
            raise ConfigurationError(f"Invalid alarm kind for {rule_id}: {kind}")

    if not rules:
        raise ConfigurationError("At least one enabled alarm is required")
    return NotifierConfig(
        defaults=defaults,
        telegram=_parse_people(telegram_raw),
        rules=tuple(rules),
    )


def value_as_bool(value: Any) -> bool:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "on", "true", "yes"}:
            return True
        if normalized in {"0", "off", "false", "no"}:
            return False
        raise ValueError(f"Cannot interpret {value!r} as boolean")
    return bool(value)


def scalar_from_response(response: Any) -> Any:
    if len(response.data) != 1:
        raise ValueError(f"Expected one scalar value, received {len(response.data)}")
    value = response.data[0]
    value = value.item() if hasattr(value, "item") else value
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _elapsed(start: float | None, now: float) -> float:
    return 0.0 if start is None else max(0.0, now - start)


class AlarmEngine:
    """Evaluate configured alarm rules using monotonic time."""

    def __init__(self, config: NotifierConfig) -> None:
        self.config = config
        self.samples: dict[str, Any] = {}
        self.sample_update_times: dict[str, float] = {}
        self.sample_change_times: dict[str, float] = {}
        self.states = {rule.rule_id: RuleState() for rule in config.rules}
        self.primed = False
        self.primed_rules: set[str] = set()

    def set_sample(self, pv: str, value: Any, now: float | None = None) -> None:
        timestamp = time.monotonic() if now is None else now
        if pv not in self.samples or self.samples[pv] != value:
            self.sample_change_times[pv] = timestamp
        self.samples[pv] = value
        self.sample_update_times[pv] = timestamp

    @property
    def baseline_complete(self) -> bool:
        return set(self.config.mandatory_pvs).issubset(self.samples)

    def _rule_ready(self, rule: Rule) -> bool:
        return rule.required_pvs.issubset(self.samples)

    def _conditions_met(self, rule: Rule) -> bool:
        for condition in rule.conditions:
            value = self.samples[condition.pv]
            if condition.operator == "truthy":
                matches = value_as_bool(value)
            elif condition.operator == "falsy":
                matches = not value_as_bool(value)
            elif condition.operator == "eq":
                matches = value == condition.value
            else:
                matches = value != condition.value
            if not matches:
                return False
        return True

    def _numeric_measurement(self, rule: NumericRule) -> tuple[float, float, float, str]:
        value = float(self.samples[rule.pv])
        reference = (
            float(self.samples[rule.reference_pv])
            if rule.reference_pv is not None
            else float(rule.reference_value)
        )
        denominator = abs(reference)
        if denominator == 0:
            raise ValueError(f"Reference for {rule.rule_id} is zero")
        if rule.mode == "deviation":
            deviation = abs(value - reference) / denominator * 100.0
            limit = f"deviation from {reference:g} must remain below configured percentages"
        elif rule.mode == "above":
            deviation = max(0.0, value - reference) / denominator * 100.0
            limit = f"upper limit {reference:g}"
        elif rule.mode == "below":
            deviation = max(0.0, reference - value) / denominator * 100.0
            limit = f"lower limit {reference:g}"
        else:
            deviation = value / denominator * 100.0
            limit = f"ratio to {reference:g}"
        if rule.reference_pv:
            limit += f" ({rule.reference_pv})"
        return value, reference, deviation, limit

    def _event(
        self,
        rule: Rule,
        state: RuleState,
        level: str,
        resolved: bool,
    ) -> AlarmEvent:
        value = state.last_value
        if value is None:
            raise RuntimeError(f"Rule {rule.rule_id} has no value")
        return AlarmEvent(
            rule_id=rule.rule_id,
            label=rule.label,
            pv=rule.pv,
            level=level,
            resolved=resolved,
            value=value,
            limit=state.last_limit,
            deviation_percent=state.last_deviation_percent,
            group=rule.group,
        )

    def prime(self, now: float, *, notify_initial: bool) -> list[AlarmEvent]:
        if not self.baseline_complete:
            return []
        events: list[AlarmEvent] = []
        self.primed = True
        for rule in self.config.rules:
            if not self._rule_ready(rule):
                continue
            events.extend(self._prime_rule(rule, now, notify_initial=notify_initial))
        return events

    def _prime_rule(
        self,
        rule: Rule,
        now: float,
        *,
        notify_initial: bool,
    ) -> list[AlarmEvent]:
        self.primed_rules.add(rule.rule_id)
        state = self.states[rule.rule_id]
        if isinstance(rule, NumericRule):
            if self._conditions_met(rule):
                value, reference, deviation, limit = self._numeric_measurement(rule)
            else:
                value = float(self.samples[rule.pv])
                reference = (
                    self.samples[rule.reference_pv]
                    if rule.reference_pv is not None
                    else rule.reference_value
                )
                deviation = 0.0
                limit = "rule conditions are inactive"
            state.last_value = value
            state.last_reference = reference
            state.last_deviation_percent = deviation
            state.last_limit = limit
            if self._conditions_met(rule) and deviation >= rule.policy.minor_percent:
                state.breach_started_at = now
            if self._conditions_met(rule) and deviation > rule.policy.major_percent:
                state.direct_major_started_at = now
                if rule.policy.major_seconds == 0:
                    state.level = "MAJOR"
        else:
            active, value, limit, started_at = self._non_numeric_status(rule, now)
            state.last_value = value
            state.last_limit = limit
            if active:
                state.activation_started_at = started_at
                stages = self._stages(rule)
                due = [stage for stage in stages if _elapsed(started_at, now) >= stage.after_seconds]
                if due:
                    state.level = due[-1].level
        if state.level and notify_initial:
            state.notified = True
            return [self._event(rule, state, state.level, False)]
        return []

    def evaluate(self, now: float) -> list[AlarmEvent]:
        if not self.primed:
            return []
        events: list[AlarmEvent] = []
        for rule in self.config.rules:
            if rule.rule_id not in self.primed_rules:
                if self._rule_ready(rule):
                    self._prime_rule(rule, now, notify_initial=False)
                continue
            try:
                if isinstance(rule, NumericRule):
                    event = self._evaluate_numeric(rule, now)
                else:
                    event = self._evaluate_staged(rule, now)
            except (KeyError, TypeError, ValueError) as exc:
                LOG.error("Cannot evaluate alarm %s: %s", rule.rule_id, exc)
                continue
            if event is not None:
                events.append(event)
        return events

    @staticmethod
    def _stages(rule: StateRule | StaleRule | ComparisonRule | RangeRule) -> tuple[AlarmStage, ...]:
        return rule.effective_stages if isinstance(rule, StateRule) else rule.stages

    @staticmethod
    def _compare(value: Any, reference: Any, operator: str) -> bool:
        if operator == "eq":
            return value == reference
        if operator == "ne":
            return value != reference
        if operator == "gt":
            return float(value) > float(reference)
        if operator == "gte":
            return float(value) >= float(reference)
        if operator == "lt":
            return float(value) < float(reference)
        if operator == "lte":
            return float(value) <= float(reference)
        if operator == "truthy":
            return value_as_bool(value)
        if operator == "falsy":
            return not value_as_bool(value)
        raise ValueError(f"Unsupported comparison operator: {operator}")

    def _non_numeric_status(
        self,
        rule: StateRule | StaleRule | ComparisonRule | RangeRule,
        now: float,
    ) -> tuple[bool, Any, str, float]:
        if not self._conditions_met(rule):
            return False, self.samples[rule.pv], "rule conditions are inactive", now
        if isinstance(rule, StateRule):
            raw_value = self.samples[rule.pv]
            value = value_as_bool(raw_value) if isinstance(rule.alarm_when, bool) else raw_value
            active = self._compare(value, rule.alarm_when, rule.operator)
            return active, value, rule.limit_description, now
        if isinstance(rule, StaleRule):
            timestamps = (
                self.sample_change_times
                if rule.timestamp_mode == "change"
                else self.sample_update_times
            )
            last_seen = timestamps[rule.pv]
            stale_started = last_seen + rule.stale_after_seconds
            age = _elapsed(last_seen, now)
            return (
                now >= stale_started,
                f"{age:.1f} s since last {rule.timestamp_mode}",
                rule.limit_description,
                stale_started,
            )
        if isinstance(rule, ComparisonRule):
            value = self.samples[rule.pv]
            reference = (
                self.samples[rule.reference_pv]
                if rule.reference_pv is not None
                else rule.reference_value
            )
            matches = self._compare(value, reference, rule.operator)
            return not matches, value, rule.limit_description, now
        value = float(self.samples[rule.pv])
        active = (rule.minimum is not None and value < rule.minimum) or (
            rule.maximum is not None and value > rule.maximum
        )
        bounds = f"range [{rule.minimum}, {rule.maximum}]"
        return active, value, bounds, now

    def _evaluate_numeric(self, rule: NumericRule, now: float) -> AlarmEvent | None:
        state = self.states[rule.rule_id]
        enabled = self._conditions_met(rule)
        if enabled:
            value, reference, deviation, limit = self._numeric_measurement(rule)
        else:
            value = float(self.samples[rule.pv])
            reference = (
                self.samples[rule.reference_pv]
                if rule.reference_pv is not None
                else rule.reference_value
            )
            deviation = 0.0
            limit = "rule conditions are inactive"
        state.last_value = value
        state.last_reference = reference
        state.last_deviation_percent = deviation
        state.last_limit = limit
        policy = rule.policy

        minor_breach = enabled and deviation >= policy.minor_percent
        direct_major = enabled and (
            deviation >= policy.major_percent
            if rule.mode == "ratio"
            else deviation > policy.major_percent
        )
        sustained_major = enabled and deviation >= policy.major_sustained_percent
        interlock_breach = (
            enabled
            and policy.interlock_percent is not None
            and deviation >= policy.interlock_percent
        )

        if minor_breach:
            state.recovery_started_at = None
            if state.breach_started_at is None:
                state.breach_started_at = now
        else:
            state.breach_started_at = None

        if direct_major:
            if state.direct_major_started_at is None:
                state.direct_major_started_at = now
        else:
            state.direct_major_started_at = None

        if interlock_breach:
            if state.interlock_started_at is None:
                state.interlock_started_at = now
        else:
            state.interlock_started_at = None

        major_due = direct_major and _elapsed(state.direct_major_started_at, now) >= policy.major_seconds
        major_due = major_due or (
            sustained_major
            and _elapsed(state.breach_started_at, now) >= policy.major_sustained_seconds
        )
        minor_due = minor_breach and _elapsed(state.breach_started_at, now) >= policy.minor_seconds
        interlock_due = (
            interlock_breach
            and _elapsed(state.interlock_started_at, now) >= float(policy.interlock_seconds)
        )

        if state.level is None:
            if interlock_due:
                state.level = "INTERLOCK"
                state.notified = True
                return self._event(rule, state, "INTERLOCK", False)
            if major_due:
                state.level = "MAJOR"
                state.notified = True
                return self._event(rule, state, "MAJOR", False)
            if minor_due:
                state.level = "MINOR"
                state.notified = True
                return self._event(rule, state, "MINOR", False)
            return None

        if state.level != "INTERLOCK" and interlock_due:
            state.level = "INTERLOCK"
            state.notified = True
            return self._event(rule, state, "INTERLOCK", False)

        if state.level == "MINOR" and major_due:
            state.level = "MAJOR"
            state.notified = True
            return self._event(rule, state, "MAJOR", False)

        if minor_breach:
            state.recovery_started_at = None
            return None
        if state.recovery_started_at is None:
            state.recovery_started_at = now
            return None
        if _elapsed(state.recovery_started_at, now) < policy.recovery_seconds:
            return None

        old_level = state.level
        should_notify = state.notified
        self.states[rule.rule_id] = replace(
            state,
            level=None,
            notified=False,
            recovery_started_at=None,
            breach_started_at=None,
            direct_major_started_at=None,
            interlock_started_at=None,
        )
        return self._event(rule, state, old_level, True) if should_notify else None

    def _evaluate_staged(
        self,
        rule: StateRule | StaleRule | ComparisonRule | RangeRule,
        now: float,
    ) -> AlarmEvent | None:
        state = self.states[rule.rule_id]
        active, value, limit, started_at = self._non_numeric_status(rule, now)
        state.last_value = value
        state.last_limit = limit

        if active:
            state.recovery_started_at = None
            if state.activation_started_at is None:
                state.activation_started_at = started_at
            due = [
                stage
                for stage in self._stages(rule)
                if _elapsed(state.activation_started_at, now) >= stage.after_seconds
            ]
            if not due:
                return None
            target = due[-1].level
            if state.level is None or LEVEL_RANK[target] > LEVEL_RANK[state.level]:
                state.level = target
                state.notified = True
                return self._event(rule, state, target, False)
            return None

        state.activation_started_at = None
        if state.level is None:
            return None
        if state.recovery_started_at is None:
            state.recovery_started_at = now
            return None
        if _elapsed(state.recovery_started_at, now) < rule.recovery_seconds:
            return None
        old_level = state.level
        should_notify = state.notified
        self.states[rule.rule_id] = replace(
            state,
            level=None,
            notified=False,
            recovery_started_at=None,
            activation_started_at=None,
        )
        return self._event(rule, state, old_level, True) if should_notify else None


class AlarmGroupReducer:
    """Suppress duplicate notifications while any rule in a group remains active."""

    def __init__(self) -> None:
        self.active: dict[str, dict[str, AlarmEvent]] = {}
        self.announced: dict[str, AlarmEvent] = {}

    def process(self, event: AlarmEvent) -> tuple[AlarmEvent, ...]:
        group = event.group or event.rule_id
        active = self.active.setdefault(group, {})
        if not event.resolved:
            active[event.rule_id] = event
            announced = self.announced.get(group)
            if announced is None or LEVEL_RANK[event.level] > LEVEL_RANK[announced.level]:
                self.announced[group] = event
                return (event,)
            return ()

        active.pop(event.rule_id, None)
        if active:
            return ()
        self.active.pop(group, None)
        announced = self.announced.pop(group, None)
        if announced is None:
            return ()
        return (
            replace(
                announced,
                resolved=True,
                value=event.value,
                deviation_percent=event.deviation_percent,
            ),
        )


class TelegramSender:
    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        policy: TelegramPolicy,
        *,
        dry_run: bool,
    ) -> None:
        self.dry_run = dry_run
        self.policy = policy
        self._session = requests.Session()
        self._membership_cache: dict[int, tuple[float, bool]] = {}
        if dry_run:
            self._base_url = None
            self._chat_id = chat_id or "dry-run-chat"
        else:
            if not token or not chat_id:
                raise ConfigurationError(
                    "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required unless --dry-run is used"
                )
            self._base_url = f"https://api.telegram.org/bot{token}"
            self._chat_id = chat_id

    def _person_is_member(self, person: Person) -> bool:
        if self.dry_run or not self.policy.verify_membership:
            return True
        now = time.monotonic()
        cached = self._membership_cache.get(person.user_id)
        if cached and now - cached[0] < self.policy.membership_cache_seconds:
            return cached[1]
        try:
            response = self._session.post(
                f"{self._base_url}/getChatMember",
                data={"chat_id": self._chat_id, "user_id": person.user_id},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            status = payload.get("result", {}).get("status") if payload.get("ok") else None
            present = status not in {None, "left", "kicked"}
        except requests.RequestException:
            LOG.warning("Could not verify Telegram membership for user ID %s", person.user_id)
            present = False
        self._membership_cache[person.user_id] = (now, present)
        return present

    def _mentions(self, level: str) -> str:
        if level == "MINOR":
            return ""
        recipients = (
            self.policy.major_people
            if level == "MAJOR"
            else (
                tuple(self.policy.people)
                if self.policy.interlock_people == "all"
                else self.policy.interlock_people
            )
        )
        mentions = []
        for user_id in recipients:
            person = self.policy.people[user_id]
            if self._person_is_member(person):
                mentions.append(
                    f'<a href="tg://user?id={person.user_id}">{escape(person.name)}</a>'
                )
        return " ".join(mentions)

    def send_event(self, event: AlarmEvent) -> None:
        icon = "✅" if event.resolved else "⚠️"
        heading = f"RESOLVED {event.level}" if event.resolved else event.level
        lines = [
            f"<b>{icon} [BDX] {heading}</b>",
            f"<b>PV:</b> <code>{escape(event.pv)}</code>",
            f"<b>Limit:</b> {escape(event.limit)}",
            f"<b>Value:</b> {escape(str(event.value))}",
        ]
        if event.deviation_percent is not None:
            lines.append(f"<b>Deviation:</b> {event.deviation_percent:.2f}%")
        lines.append(f"<b>Condition:</b> {escape(event.label)}")
        mentions = self._mentions(event.level)
        if mentions:
            lines.extend(["", mentions])
        self._send("\n".join(lines))

    def send_connection_event(self, *, resolved: bool, detail: str) -> None:
        event = AlarmEvent(
            rule_id="epics-connection",
            label=detail,
            pv="EPICS Channel Access",
            level="MAJOR",
            resolved=resolved,
            value=resolved,
            limit="all configured PVs must remain connected",
        )
        self.send_event(event)

    def send_test_message(self) -> None:
        """Send one harmless delivery test without connecting to EPICS."""
        message = (
            "<b>\u2705 [BDX] TELEGRAM TEST</b>\n"
            "Notifier configuration and Telegram delivery are working.\n"
            "No EPICS PV was read or written."
        )
        mentions = self._mentions("MAJOR")
        if mentions:
            message = f"{message}\n\n{mentions}"
        self._send(message)

    def _send(self, message: str) -> None:
        if self.dry_run:
            LOG.info("DRY RUN Telegram message:\n%s", message)
            return
        try:
            response = self._session.post(
                f"{self._base_url}/sendMessage",
                data={"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise TelegramDeliveryError(
                f"Telegram connection failed ({type(exc).__name__})"
            ) from exc
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise TelegramDeliveryError(
                f"Telegram sendMessage returned invalid JSON (HTTP {response.status_code})"
            ) from exc
        if not response.ok or not payload.get("ok", False):
            description = str(payload.get("description", "request rejected"))
            raise TelegramDeliveryError(
                f"Telegram sendMessage failed (HTTP {response.status_code}: {description})"
            )

    def close(self) -> None:
        self._session.close()


class BdxNotifier:
    def __init__(
        self,
        config: NotifierConfig,
        sender: TelegramSender,
        *,
        notify_initial: bool,
        connection_timeout: float,
        retry_seconds: float,
    ) -> None:
        self.config = config
        self.sender = sender
        self.notify_initial = notify_initial
        self.connection_timeout = connection_timeout
        self.retry_seconds = retry_seconds
        self.engine = AlarmEngine(config)
        self.events: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.context: Context | None = None
        self.connected_pvs: set[str] = set()
        self.connection_baseline_ready = False
        self.connection_alarm_active = False
        self._subscriptions = []
        self.group_reducer = AlarmGroupReducer()

    def _connection_callback(self, pv, state: str) -> None:
        self.events.put(("connection", pv.name, state))

    def _value_callback(self, subscription, response) -> None:
        self.events.put(("value", subscription.pv.name, scalar_from_response(response)))

    def _connect_once(self) -> None:
        self.context = Context(timeout=self.connection_timeout, client_name="bdx-notifier")
        pvs = self.context.get_pvs(
            *self.config.required_pvs,
            connection_state_callback=self._connection_callback,
        )
        mandatory = set(self.config.mandatory_pvs)
        failures = []
        for pv in pvs:
            try:
                timeout = self.connection_timeout if pv.name in mandatory else 0.25
                pv.wait_for_connection(timeout=timeout)
            except Exception as exc:
                if pv.name in mandatory:
                    failures.append(f"{pv.name}: {exc}")
                    continue
                else:
                    LOG.info("Optional EPICS PV is unavailable: %s", pv.name)
            subscription = pv.subscribe()
            subscription.add_callback(self._value_callback)
            self._subscriptions.append(subscription)
        if failures:
            raise RuntimeError("Unable to connect to monitored PVs: " + "; ".join(failures))
        LOG.info("Connected to %d BDX PVs", len(pvs))

    def connect(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._connect_once()
                return
            except Exception as exc:
                LOG.warning("EPICS startup connection not ready: %s", exc)
                if self.context is not None:
                    self.context.disconnect(wait=True)
                    self.context = None
                self._subscriptions.clear()
                self.stop_event.wait(self.retry_seconds)

    def _deliver(self, event: AlarmEvent) -> None:
        for reduced_event in self.group_reducer.process(event):
            try:
                self.sender.send_event(reduced_event)
            except TelegramDeliveryError as exc:
                LOG.error("%s; event %s was not delivered", exc, reduced_event.rule_id)

    def _process_value(self, pv_name: str, value: Any, now: float) -> None:
        self.engine.set_sample(pv_name, value, now)
        if not self.engine.primed and self.engine.baseline_complete:
            LOG.info("Initial EPICS alarm baseline complete")
            for event in self.engine.prime(now, notify_initial=self.notify_initial):
                self._deliver(event)
        for event in self.engine.evaluate(now):
            LOG.info(
                "Alarm transition: %s level=%s resolved=%s",
                event.rule_id,
                event.level,
                event.resolved,
            )
            self._deliver(event)

    def _process_connection(self, pv_name: str, state: Any) -> None:
        connected = str(state).lower() == "connected"
        if connected:
            self.connected_pvs.add(pv_name)
            if set(self.config.mandatory_pvs).issubset(self.connected_pvs):
                if self.connection_baseline_ready and self.connection_alarm_active:
                    self.sender.send_connection_event(
                        resolved=True,
                        detail="All monitored EPICS PVs reconnected",
                    )
                self.connection_baseline_ready = True
                self.connection_alarm_active = False
            return
        self.connected_pvs.discard(pv_name)
        if (
            pv_name in self.config.mandatory_pvs
            and self.connection_baseline_ready
            and not self.connection_alarm_active
        ):
            self.connection_alarm_active = True
            self.sender.send_connection_event(
                resolved=False,
                detail=f"First unavailable PV: {pv_name}",
            )

    def run(self) -> None:
        self.connect()
        if self.stop_event.is_set():
            return
        LOG.info("Notifier running; press Ctrl-C to stop")
        while not self.stop_event.is_set():
            try:
                event_type, pv_name, value = self.events.get(timeout=0.25)
            except queue.Empty:
                event_type = "tick"
                pv_name = ""
                value = None
            now = time.monotonic()
            try:
                if event_type == "value":
                    self._process_value(pv_name, value, now)
                elif event_type == "connection":
                    self._process_connection(pv_name, value)
                for event in self.engine.evaluate(now):
                    LOG.info(
                        "Alarm transition: %s level=%s resolved=%s",
                        event.rule_id,
                        event.level,
                        event.resolved,
                    )
                    self._deliver(event)
            except TelegramDeliveryError as exc:
                LOG.error("%s", exc)
            except Exception:
                LOG.exception("Failed to process notifier event for %s", pv_name)

    def stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        if self.context is not None:
            self.context.disconnect(wait=True)
        self.sender.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service-instance",
        default="bdx-notifier",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_FILE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="send one Telegram test message and exit without connecting to EPICS",
    )
    parser.add_argument("--notify-initial", action="store_true")
    parser.add_argument("--connection-timeout", type=float, default=5.0)
    parser.add_argument("--retry-seconds", type=float, default=5.0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.connection_timeout <= 0 or args.retry_seconds <= 0:
        LOG.error("Connection timeout and retry interval must be positive")
        return 2
    load_dotenv(args.env_file)
    try:
        config = load_config(args.config)
        config = replace(
            config,
            telegram=telegram_policy_from_environment(config.telegram),
        )
        sender = TelegramSender(
            os.getenv("TELEGRAM_BOT_TOKEN"),
            os.getenv("TELEGRAM_CHAT_ID"),
            config.telegram,
            dry_run=args.dry_run,
        )
    except ConfigurationError as exc:
        LOG.error("%s", exc)
        return 2

    if args.test_telegram:
        try:
            sender.send_test_message()
        except TelegramDeliveryError as exc:
            LOG.error("%s", exc)
            return 1
        finally:
            sender.close()
        LOG.info("Telegram test message delivered")
        return 0

    notifier = BdxNotifier(
        config,
        sender,
        notify_initial=args.notify_initial,
        connection_timeout=args.connection_timeout,
        retry_seconds=args.retry_seconds,
    )

    def request_stop(signum, frame) -> None:
        LOG.info("Stopping after signal %s", signum)
        notifier.stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        notifier.run()
    finally:
        notifier.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
