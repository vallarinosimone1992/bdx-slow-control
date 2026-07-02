# Split-Host Service Examples

These service files run one Channel Access server process per subsystem.

Use them when:

- subsystems are distributed across different hosts; or
- explicit, non-conflicting Channel Access search-port routing has been designed.

Do not enable all of these examples on the prototype host without defining the Channel Access port and client discovery strategy. Two Channel Access servers must never expose the same PV names. The default and validated single-host development deployment is the `prototype` profile.
