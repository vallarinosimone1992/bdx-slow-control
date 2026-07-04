# BDX EPICS Archiver Appliance Deployment

This directory contains BDX-owned deployment infrastructure for the EPICS
Archiver Appliance. It intentionally does not contain third-party source trees,
WAR files, JAR files, Tomcat runtimes, downloaded archives, logs, archive data,
databases, credentials, or secrets.

## Selected Release

The pinned stable release is:

```text
Archiver Appliance: 2.3.1
Artifact: archappl_v2.3.1.tar.gz
URL: https://github.com/archiver-appliance/epicsarchiverap/releases/download/2.3.1/archappl_v2.3.1.tar.gz
SHA-256: ce2eabe57915a99bc9be22d29d400f112f63931b5d5af9394e8504702d16722f
Java: JDK 21 or later
Servlet container: Apache Tomcat 11
```

Release `2.4.0` exists upstream as a prerelease and is not selected here.

The upstream license is the EPICS Archiver Appliance software license from the
official release. Keep the upstream `LICENSE`, `NOTICE`, and bundled license
files with any downloaded runtime artifact.

## Architecture

The Archiver Appliance is deployed as four official WAR components:

```text
mgmt       management UI and BPL API
engine     Channel Access sampling engine
etl        data movement between stores
retrieval  HTTP data retrieval API
```

The BDX scripts generate:

```text
appliances.xml  appliance topology and component URLs
policies.py     official policy interface with BDX policy names
pv-lists/*.txt  BDX PV registration lists
```

The persistent deployment is the primary model. A user-local layout is also
supported for evaluation.

## Storage Layout

Default persistent paths:

```text
/opt/bdx-archiver          application scripts and staged release metadata
/etc/bdx-archiver          site configuration
/var/lib/bdx-archiver      state, Tomcat bases, STS/MTS/LTS stores
/var/log/bdx-archiver      component logs
/var/cache/bdx-archiver    downloaded release archive cache
```

Runtime archive data must never be stored in the Git checkout.

The data stores are separated as:

```text
tmp          temporary files
sts          short-term store
mts          medium-term store
lts          long-term store
persistence  local persistence metadata if used
```

Final BDX retention is not yet defined. The default policy values are prototype
testing defaults and must be reviewed before production.

## Prerequisites

The scripts report missing prerequisites but never install OS packages.

Ubuntu 22.04 hints:

```bash
sudo apt install openjdk-21-jdk curl tar
```

Install Apache Tomcat 11 from the official Tomcat distribution if it is not
available from the OS repositories.

Rocky Linux 9 hints:

```bash
sudo dnf install java-21-openjdk java-21-openjdk-devel curl tar
```

Install Apache Tomcat 11 from the official Tomcat distribution if it is not
available from the OS repositories.

The management service should remain on a trusted network. These scripts do not
modify host firewall rules.

## Channel Access

Configure Channel Access in the private environment file:

```bash
EPICS_CA_ADDR_LIST="172.22.50.1 172.22.50.10"
EPICS_CA_AUTO_ADDR_LIST=NO
EPICS_CA_SERVER_PORT=5064
EPICS_CA_REPEATER_PORT=5065
```

The example includes a future main IOC server and the Raspberry environment IOC.
Do not hard-code prototype addresses in scripts or PV lists.

## Local Evaluation

Create a user-local environment file:

```bash
mkdir -p ~/.config/bdx-archiver
cp deploy/archiver-appliance/config/archappl.env.example \
  ~/.config/bdx-archiver/archappl.env
```

Edit `~/.config/bdx-archiver/archappl.env` and set:

```bash
BDX_ARCHIVER_EVALUATION_MODE=true
ARCHAPPL_PERSISTENCE_LAYER=
BDX_ARCHIVER_TOMCAT_TARBALL=/path/to/apache-tomcat-11.x.y.tar.gz
EPICS_CA_ADDR_LIST="172.22.50.1 172.22.50.10"
```

Then stage and configure:

```bash
deploy/archiver-appliance/scripts/install.sh \
  --env ~/.config/bdx-archiver/archappl.env \
  --user-local \
  --download

deploy/archiver-appliance/scripts/configure.sh \
  --env ~/.config/bdx-archiver/archappl.env \
  --user-local
```

Start and stop manually:

```bash
~/.local/share/bdx-archiver/app/scripts/start.sh \
  --env ~/.config/bdx-archiver/archappl.env \
  --user-local

~/.local/share/bdx-archiver/app/scripts/stop.sh \
  --env ~/.config/bdx-archiver/archappl.env \
  --user-local
```

## Provisional Ubuntu 22.04 Deployment

The final production host is not selected. A provisional persistent deployment
on an Ubuntu 22.04 server with sudo access is feasible if Java 21, Tomcat 11,
persistent metadata storage, and adequate filesystem storage are provided.

```bash
sudo install -d -m 0755 /etc/bdx-archiver
sudo cp deploy/archiver-appliance/config/archappl.env.example \
  /etc/bdx-archiver/archappl.env
sudo editor /etc/bdx-archiver/archappl.env
```

Set at least:

```bash
BDX_ARCHIVER_TOMCAT_HOME=/opt/bdx-archiver/tomcat
BDX_ARCHIVER_TOMCAT_TARBALL=/path/to/apache-tomcat-11.x.y.tar.gz
ARCHAPPL_PERSISTENCE_LAYER=<site-selected persistent layer>
EPICS_CA_ADDR_LIST="172.22.50.1 172.22.50.10"
```

Stage the BDX deployment files and optionally download the pinned release:

```bash
sudo deploy/archiver-appliance/scripts/install.sh \
  --env /etc/bdx-archiver/archappl.env \
  --download

sudo /opt/bdx-archiver/scripts/configure.sh \
  --env /etc/bdx-archiver/archappl.env
```

Install and start the system service after reviewing the generated configuration:

```bash
sudo install -m 0644 /opt/bdx-archiver/systemd/bdx-archiver.service \
  /etc/systemd/system/bdx-archiver.service
sudo systemctl daemon-reload
sudo systemctl enable bdx-archiver
sudo systemctl start bdx-archiver
sudo systemctl --no-pager --full status bdx-archiver
```

## PV Registration

Dry-run registration:

```bash
/opt/bdx-archiver/scripts/register-pvs.py \
  --dry-run \
  /opt/bdx-archiver/pv-lists/environment.txt \
  /opt/bdx-archiver/pv-lists/psu.txt \
  /opt/bdx-archiver/pv-lists/chiller.txt
```

Register PVs using the management BPL API:

```bash
/opt/bdx-archiver/scripts/register-pvs.py \
  --mgmt-url http://127.0.0.1:17665/mgmt/bpl \
  /opt/bdx-archiver/pv-lists/environment.txt \
  /opt/bdx-archiver/pv-lists/psu.txt \
  /opt/bdx-archiver/pv-lists/chiller.txt
```

The registration script reports a per-PV outcome and returns non-zero if any PV
cannot be registered.

## Health Checks

```bash
/opt/bdx-archiver/scripts/healthcheck.sh \
  --env /etc/bdx-archiver/archappl.env
```

With retrieval of a known BDX PV:

```bash
/opt/bdx-archiver/scripts/healthcheck.sh \
  --env /etc/bdx-archiver/archappl.env \
  --check-pv
```

## Retrieval Tests

```bash
/opt/bdx-archiver/scripts/verify-retrieval.py \
  --retrieval-url http://127.0.0.1:17668/retrieval \
  --pv BDX:ENV:TEMP:T00:VALUE
```

The script distinguishes endpoint failure, unknown PV, known PV without recent
samples, and successful retrieval.

## Phoebus Integration

The Phoebus launcher can combine live Channel Access samples with Archiver
Appliance history. Use the same retrieval endpoint configured for the
appliance:

```bash
BDX_ARCHIVER_ENABLED=true \
BDX_ARCHIVER_URL=http://<ARCHIVER_HOST>:17668/retrieval \
BDX_ARCHIVER_NAME="BDX Archiver" \
./scripts/launch_phoebus.sh overview
```

The launcher writes Phoebus Data Browser preferences with a `pbraw://` archive
URL. If the retrieval service is temporarily unavailable, Data Browser keeps the
live Channel Access traces unless `BDX_ARCHIVER_STRICT_CHECK=true` and the
optional preflight check fails.

## Backup And Restore

Back up configuration and metadata:

```bash
/opt/bdx-archiver/scripts/backup-config.sh \
  --env /etc/bdx-archiver/archappl.env
```

This does not create a consistent live backup of archive data under STS/MTS/LTS.
Use a storage-specific snapshot or backup method for archive data.

Restore procedure:

1. Install the same pinned Archiver Appliance release and BDX deployment scripts.
2. Restore `/etc/bdx-archiver`, topology, policies, PV lists, and persistence metadata.
3. Restore archive data using the storage-specific consistent backup.
4. Run `configure.sh`.
5. Start the service and run `healthcheck.sh`.

## Upgrade Procedure

1. Select an official stable release.
2. Update `VERSION`, `CHECKSUMS`, and `archappl.env.example`.
3. Validate Java and Tomcat compatibility.
4. Download the artifact outside the repository.
5. Verify SHA-256.
6. Stop the service.
7. Back up configuration and persistence metadata.
8. Deploy the new WAR files.
9. Run health and retrieval checks.

Do not commit downloaded release artifacts.

## Uninstall

Stop and remove staged application files while preserving state:

```bash
sudo /opt/bdx-archiver/scripts/uninstall.sh \
  --env /etc/bdx-archiver/archappl.env \
  --yes
```

Remove state and configuration only after a confirmed backup:

```bash
sudo /opt/bdx-archiver/scripts/uninstall.sh \
  --env /etc/bdx-archiver/archappl.env \
  --yes \
  --purge-state
```

## Future JLab Integration Boundary

No JLab central archiving or database integration is assumed here. This
repository owns BDX PV lists, policies, deployment scripts, checksums,
configuration templates, systemd examples, and health checks. Future JLab
integration should replace or wrap these templates at the deployment boundary
without committing site credentials, central database details, or private
network assumptions to this repository.
