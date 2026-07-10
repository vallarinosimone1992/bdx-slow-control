# BDX Slow Control manuals

This directory contains the English LaTeX sources for two maintained manuals:

- `quickstart_guide.tex`: routine operator startup, use, shutdown, and basic troubleshooting;
- `developer_manual.tex`: architecture, configuration, extension, testing, deployment, and release workflow.

Shared formatting is defined in `common/preamble.tex`.

## Build

A standard TeX Live installation with `latexmk` is sufficient:

```bash
make -C documentation
```

The final PDFs are copied to the repository root:

```text
BDX_Slow_Control_Quickstart.pdf
BDX_Slow_Control_Developer_Manual.pdf
```

Build only one manual with:

```bash
make -C documentation quickstart
make -C documentation developer
```

Remove intermediate files with:

```bash
make -C documentation clean
```

## Maintenance rule

Update the quickstart guide whenever an operator-visible command, display, safety workflow, address, or troubleshooting procedure changes. Update the developer manual whenever the architecture, configuration schema, PV contract, extension procedure, testing policy, deployment model, or release process changes.

The focused documents under `docs/` and `deploy/` remain authoritative for subsystem-specific deployment details. The manuals should summarize and link to those documents rather than duplicating them verbatim.
