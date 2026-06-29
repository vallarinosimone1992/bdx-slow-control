# Changelog

## 0.2.0

- renamed the repository and archives to `bdx-slow-control`;
- added generated Phoebus `.bob` displays for all simulation PVs;
- added complete read and write coverage through `all_pvs.bob`;
- added subsystem overview and live trend displays;
- added a valid Phoebus PV Table file;
- added portable Phoebus launch scripts and machine-local environment configuration;
- added dynamic IOC update periods from 2 to 3600 seconds;
- limited simulation and default display update rates to below 1 Hz;
- added simulated interlock trigger and reset commands;
- propagated global all-off and interlock actions to simulated PSU and HV outputs;
- added display generation and coverage tests.
