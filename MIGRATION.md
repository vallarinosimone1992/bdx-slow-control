# Migration from `test_ioc.py`

This repository is not a compatibility layer.

The migration policy is:

- preserve useful hardware communication code only;
- move hardware communication into dedicated drivers;
- replace command-line environment coupling with JSON configuration;
- replace one monolithic IOC with independent subsystem processes;
- preserve or deliberately revise PV names through an explicit PV contract;
- do not retain legacy implementation details solely for backward compatibility.

Recommended migration steps:

1. copy the existing TTI communication code into a new hardware driver;
2. implement the methods required by `PowerSupplyDriver`;
3. add the hardware driver to `drivers/factory.py`;
4. select it with `"mode": "hardware"`;
5. compare the generated PV list with the approved BDX naming document;
6. validate all command, readback, timeout, and reconnect cases.
