# Phoebus Settings

`scripts/launch_phoebus.sh` generates the effective settings file at runtime. The generated file configures:

- Channel Access as the default PV protocol;
- explicit Channel Access address lists;
- CA server and repeater ports;
- the Display Builder update throttle.

`settings.ini.template` documents the generated keys.
