import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from notifier import (
    AlarmEvent,
    AlarmEngine,
    AlarmGroupReducer,
    AlarmStage,
    ComparisonRule,
    Condition,
    NotifierConfig,
    NumericPolicy,
    NumericRule,
    RangeRule,
    StateRule,
    StaleRule,
    Person,
    TelegramSender,
    TelegramPolicy,
    load_config,
    DEFAULT_CONFIG_FILE,
    main,
)


def config_for(*rules):
    return NotifierConfig(
        defaults=NumericPolicy(),
        telegram=TelegramPolicy(
            people={},
            major_people=(),
            interlock_people="all",
        ),
        rules=tuple(rules),
    )


class AlarmEngineTests(unittest.TestCase):
    def test_telegram_test_sends_once_without_starting_epics(self):
        sender = Mock()
        with (
            patch("notifier.load_dotenv"),
            patch("notifier.load_config", return_value=config_for()),
            patch("notifier.TelegramSender", return_value=sender),
            patch("notifier.BdxNotifier") as notifier_class,
        ):
            status = main(["--test-telegram"])

        self.assertEqual(status, 0)
        sender.send_test_message.assert_called_once_with()
        sender.close.assert_called_once_with()
        notifier_class.assert_not_called()

    def test_standard_minor_major_and_recovery_timing(self):
        rule = NumericRule(
            rule_id="temperature",
            label="Temperature deviation",
            pv="VALUE",
            mode="deviation",
            reference_pv="SETPOINT",
            reference_value=None,
            policy=NumericPolicy(),
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("VALUE", 100.0)
        engine.set_sample("SETPOINT", 100.0)
        self.assertEqual(engine.prime(0.0, notify_initial=False), [])

        engine.set_sample("VALUE", 106.0)
        self.assertEqual(engine.evaluate(1.0), [])
        minor = engine.evaluate(6.0)
        self.assertEqual([(event.level, event.resolved) for event in minor], [("MINOR", False)])

        major = engine.evaluate(21.0)
        self.assertEqual([(event.level, event.resolved) for event in major], [("MAJOR", False)])

        engine.set_sample("VALUE", 100.0)
        self.assertEqual(engine.evaluate(22.0), [])
        recovery = engine.evaluate(27.0)
        self.assertEqual(
            [(event.level, event.resolved) for event in recovery],
            [("MAJOR", True)],
        )

    def test_direct_major_above_ten_percent_is_immediate(self):
        rule = NumericRule(
            rule_id="temperature",
            label="Temperature deviation",
            pv="VALUE",
            mode="deviation",
            reference_pv=None,
            reference_value=100.0,
            policy=NumericPolicy(),
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("VALUE", 100.0)
        engine.prime(0.0, notify_initial=False)
        engine.set_sample("VALUE", 111.0)
        events = engine.evaluate(1.0)
        self.assertEqual([(event.level, event.resolved) for event in events], [("MAJOR", False)])

    def test_initial_minor_breach_still_observes_five_second_delay(self):
        rule = NumericRule(
            rule_id="temperature",
            label="Temperature deviation",
            pv="VALUE",
            mode="deviation",
            reference_pv=None,
            reference_value=100.0,
            policy=NumericPolicy(),
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("VALUE", 106.0)

        self.assertEqual(engine.prime(0.0, notify_initial=True), [])
        self.assertEqual(engine.evaluate(4.9), [])
        events = engine.evaluate(5.0)
        self.assertEqual([(event.level, event.resolved) for event in events], [("MINOR", False)])

    def test_interlock_and_resolution(self):
        rule = StateRule(
            rule_id="interlock",
            label="Interlock",
            pv="INTERLOCK",
            alarm_when=True,
            level="INTERLOCK",
            activation_seconds=0.0,
            recovery_seconds=1.0,
            limit_description="must remain Off",
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("INTERLOCK", False)
        engine.prime(0.0, notify_initial=False)
        engine.set_sample("INTERLOCK", True)
        alarm = engine.evaluate(1.0)
        self.assertEqual([(event.level, event.resolved) for event in alarm], [("INTERLOCK", False)])
        engine.set_sample("INTERLOCK", False)
        self.assertEqual(engine.evaluate(2.0), [])
        resolved = engine.evaluate(3.0)
        self.assertEqual(
            [(event.level, event.resolved) for event in resolved],
            [("INTERLOCK", True)],
        )

    def test_state_rule_escalates_from_major_to_interlock(self):
        rule = StateRule(
            rule_id="low-level",
            label="Low level",
            pv="LOW_LEVEL",
            alarm_when=True,
            level="MAJOR",
            activation_seconds=0.0,
            recovery_seconds=5.0,
            limit_description="must remain false",
            stages=(AlarmStage("MAJOR", 0.0), AlarmStage("INTERLOCK", 600.0)),
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("LOW_LEVEL", False, 0.0)
        engine.prime(0.0, notify_initial=False)
        engine.set_sample("LOW_LEVEL", True, 1.0)
        self.assertEqual([event.level for event in engine.evaluate(1.0)], ["MAJOR"])
        self.assertEqual([event.level for event in engine.evaluate(601.0)], ["INTERLOCK"])

    def test_stale_rule_escalates_on_unchanged_heartbeat(self):
        rule = StaleRule(
            rule_id="heartbeat",
            label="Heartbeat stale",
            pv="HEARTBEAT",
            stale_after_seconds=5.0,
            timestamp_mode="change",
            stages=(AlarmStage("MINOR", 0.0), AlarmStage("MAJOR", 15.0)),
            recovery_seconds=1.0,
            limit_description="must change",
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("HEARTBEAT", 1, 0.0)
        engine.prime(0.0, notify_initial=False)
        self.assertEqual([event.level for event in engine.evaluate(5.0)], ["MINOR"])
        self.assertEqual([event.level for event in engine.evaluate(20.0)], ["MAJOR"])
        engine.set_sample("HEARTBEAT", 2, 21.0)
        self.assertEqual(engine.evaluate(21.0), [])
        self.assertTrue(engine.evaluate(22.0)[0].resolved)

    def test_comparison_rule_is_gated_by_condition(self):
        rule = ComparisonRule(
            rule_id="output",
            label="Output mismatch",
            pv="READBACK",
            reference_pv="SETTING",
            reference_value=None,
            operator="eq",
            stages=(AlarmStage("MAJOR", 0.0),),
            recovery_seconds=1.0,
            limit_description="must match",
            conditions=(Condition("ACTIVE", "truthy"),),
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("READBACK", False, 0.0)
        engine.set_sample("SETTING", True, 0.0)
        engine.set_sample("ACTIVE", False, 0.0)
        engine.prime(0.0, notify_initial=False)
        self.assertEqual(engine.evaluate(1.0), [])
        engine.set_sample("ACTIVE", True, 2.0)
        self.assertEqual([event.level for event in engine.evaluate(2.0)], ["MAJOR"])

    def test_ratio_rule_reaches_interlock_after_its_own_timer(self):
        rule = NumericRule(
            rule_id="current",
            label="Current safety",
            pv="CURRENT",
            mode="ratio",
            reference_pv="LIMIT",
            reference_value=None,
            policy=NumericPolicy(
                minor_percent=90,
                minor_seconds=0,
                major_percent=100,
                major_seconds=0,
                major_sustained_percent=100,
                major_sustained_seconds=0,
                interlock_percent=100,
                interlock_seconds=30,
            ),
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("CURRENT", 0.0, 0.0)
        engine.set_sample("LIMIT", 1.0, 0.0)
        engine.prime(0.0, notify_initial=False)
        engine.set_sample("CURRENT", 1.0, 1.0)
        self.assertEqual([event.level for event in engine.evaluate(1.0)], ["MAJOR"])
        self.assertEqual(engine.evaluate(30.9), [])
        self.assertEqual([event.level for event in engine.evaluate(31.0)], ["INTERLOCK"])

    def test_range_rule_is_major_outside_bounds(self):
        rule = RangeRule(
            rule_id="temperature-range",
            label="Temperature range",
            pv="TEMP",
            minimum=10.0,
            maximum=35.0,
            stages=(AlarmStage("MAJOR", 0.0),),
            recovery_seconds=1.0,
        )
        engine = AlarmEngine(config_for(rule))
        engine.set_sample("TEMP", 20.0, 0.0)
        engine.prime(0.0, notify_initial=False)
        engine.set_sample("TEMP", 36.0, 1.0)
        self.assertEqual([event.level for event in engine.evaluate(1.0)], ["MAJOR"])

    def test_group_reducer_suppresses_duplicate_alarm_and_resolution(self):
        reducer = AlarmGroupReducer()
        first = AlarmEvent("one", "One", "PV1", "MINOR", False, 1, "limit", group="g")
        second = AlarmEvent("two", "Two", "PV2", "MINOR", False, 1, "limit", group="g")
        self.assertEqual(reducer.process(first), (first,))
        self.assertEqual(reducer.process(second), ())
        self.assertEqual(reducer.process(replace(first, resolved=True)), ())
        resolved = reducer.process(replace(second, resolved=True))
        self.assertEqual(len(resolved), 1)
        self.assertTrue(resolved[0].resolved)

    def test_repository_configuration_is_valid(self):
        config = load_config(DEFAULT_CONFIG_FILE)
        self.assertIn("BDX:GLOBAL:INTERLOCK_ACTIVE", config.required_pvs)
        self.assertTrue(any(rule.rule_id == "chiller-temperature-deviation" for rule in config.rules))

    def test_mentions_follow_level_policy(self):
        people = {
            101: Person(user_id=101, name="Major Operator"),
            202: Person(user_id=202, name="Second Operator"),
        }
        policy = TelegramPolicy(
            people=people,
            major_people=(101,),
            interlock_people="all",
        )
        sender = TelegramSender(None, None, policy, dry_run=True)
        try:
            self.assertEqual(sender._mentions("MINOR"), "")
            self.assertIn("Major Operator", sender._mentions("MAJOR"))
            interlock_mentions = sender._mentions("INTERLOCK")
            self.assertIn("Major Operator", interlock_mentions)
            self.assertIn("Second Operator", interlock_mentions)
        finally:
            sender.close()


if __name__ == "__main__":
    unittest.main()
