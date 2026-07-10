# BDX Slow Control manuals

This directory contains the English LaTeX sources for two maintained documents:

- `quickstart_guide.tex`: a concise, command-oriented reference for routine operators;
- `developer_manual.tex`: the detailed user manual plus the architecture, configuration, extension, testing, deployment, and release guide for maintainers.

Shared formatting is defined in `common/preamble.tex`.

## Build

A standard TeX Live installation with `latexmk` is sufficient:

```bash
make -C documentation
```

The final PDFs are copied to the repository root:

```text
BDX_Slow_Control_Quickstart.pdf
BDX_Slow_Control_User_and_Developer_Manual.pdf
```

Build only one document with:

```bash
make -C documentation quickstart
make -C documentation developer
```

Remove intermediate files with:

```bash
make -C documentation clean
```

## Content rule

The quickstart must remain command-first and usable by an operator who does not know the internal EPICS architecture. Each command should state what it does, when to use it, and what result to expect.

Detailed explanations of devices, Phoebus fields, PV semantics, network configuration, Archiver behavior, operating procedures, and troubleshooting belong in Part I of the user and developer manual. Part II contains architecture, configuration, extension, testing, deployment, and release information for maintainers.

The focused documents under `docs/` and `deploy/` remain authoritative for subsystem-specific deployment details. The manuals should summarize and link to those documents rather than duplicating them verbatim.
