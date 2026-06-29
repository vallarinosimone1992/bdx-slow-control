# Validation

The 0.2.0 archive was validated with a clean virtual environment.

Validation steps:

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
pytest
bdx-pv-list --config-dir config | wc -l
bdx-generate-displays --config-dir config --output-dir phoebus/displays
```

Results:

- 13 automated tests passed;
- 176 PVs were generated;
- all `.bob` files parsed as valid XML;
- all PV references in the displays matched the configured IOC database;
- all display navigation targets existed;
- every writable PV had a control widget in `all_pvs.bob`;
- runtime update-period writes and frequency readbacks were verified over Channel Access;
- simulated interlock reset and power-output shutdown were verified over Channel Access;
- Phoebus launcher argument and settings generation were tested with a mock launcher.

A full visual rendering test still requires an installed Phoebus product on the target machine.
