#!/usr/bin/python

# BDX Archiver Appliance policies.
#
# This file follows the official Archiver Appliance policy interface:
# getPolicyList(), getFieldsArchivedAsPartOfStream(), and
# determinePolicy(pvInfoDict). The storage plugin URLs intentionally use
# ARCHAPPL_* environment variables so that retention and paths remain deployment
# configuration, not repository constants.

import os


PHYSICAL_POLICY = "BDX_Physical_5s"
STATE_POLICY = "BDX_State_Change"
DIAGNOSTIC_POLICY = "BDX_Diagnostic_Change"


STATE_SUFFIXES = (
    ":ALL_OUTPUTS_OFF",
    ":COMM_OK",
    ":COMM_STATUS",
    ":DEVIATION_ALARM",
    ":DEVIATION_STATUS",
    ":DEVIATION_WARNING",
    ":EXTERNAL_TEMPERATURE_VALID",
    ":FAULT",
    ":IOC_STATE",
    ":OUTPUT_RBV",
    ":OUTPUT_STATE",
    ":PRESSURE_VALID",
    ":RUN_RBV",
    ":RUN_STATE",
    ":STATUS",
    ":STATUS_OK",
)

DIAGNOSTIC_SUFFIXES = (
    ":COOLING_MODE",
    ":DEVICE_STATUS",
    ":ERROR_CODE",
    ":ERROR_MESSAGE",
    ":FAULT_DIAGNOSIS",
    ":LAST_TEMPERATURE_UPDATE",
    ":LAST_UPDATE",
    ":PUMP_STAGE",
)


def _positive_int_env(name, default):
    value = os.environ.get(name, default)
    try:
        parsed = int(value)
    except ValueError:
        parsed = int(default)
    if parsed <= 0:
        parsed = int(default)
    return parsed


def getPolicyList():
    pvPoliciesDict = {}
    pvPoliciesDict[PHYSICAL_POLICY] = "BDX physical readbacks, MONITOR at nominal 5 seconds"
    pvPoliciesDict[STATE_POLICY] = "BDX boolean, connection, output, warning, alarm, and fault states, MONITOR at nominal 1 second"
    pvPoliciesDict[DIAGNOSTIC_POLICY] = "BDX string and integer diagnostics, MONITOR at nominal 5 seconds"
    return pvPoliciesDict


def getFieldsArchivedAsPartOfStream():
    return []


shorttermstore_plugin_url = (
    "pb://localhost?name=STS&rootFolder=${ARCHAPPL_SHORT_TERM_FOLDER}"
    "&partitionGranularity=PARTITION_HOUR&consolidateOnShutdown=true"
)
mediumtermstore_plugin_url = (
    "pb://localhost?name=MTS&rootFolder=${ARCHAPPL_MEDIUM_TERM_FOLDER}"
    "&partitionGranularity=PARTITION_DAY"
    "&hold=%s&gather=1" % _positive_int_env("BDX_ARCHIVER_MEDIUM_TERM_HOLD_DAYS", "60")
)
longtermstore_plugin_url = (
    "pb://localhost?name=LTS&rootFolder=${ARCHAPPL_LONG_TERM_FOLDER}"
    "&partitionGranularity=PARTITION_YEAR"
)


def _policy(name, period, method):
    pvPolicyDict = {}
    pvPolicyDict["samplingPeriod"] = period
    pvPolicyDict["samplingMethod"] = method
    pvPolicyDict["dataStores"] = [
        shorttermstore_plugin_url,
        mediumtermstore_plugin_url,
        longtermstore_plugin_url,
    ]
    pvPolicyDict["policyName"] = name
    pvPolicyDict["archiveFields"] = []
    return pvPolicyDict


def determinePolicy(pvInfoDict):
    pvName = pvInfoDict.get("pvName", "")
    userPolicyOverride = pvInfoDict.get("policyName", "")

    if userPolicyOverride:
        if userPolicyOverride == PHYSICAL_POLICY:
            return _policy(PHYSICAL_POLICY, 5.0, "MONITOR")
        if userPolicyOverride == STATE_POLICY:
            return _policy(STATE_POLICY, 1.0, "MONITOR")
        if userPolicyOverride == DIAGNOSTIC_POLICY:
            return _policy(DIAGNOSTIC_POLICY, 5.0, "MONITOR")

    if pvName.endswith(STATE_SUFFIXES):
        return _policy(STATE_POLICY, 1.0, "MONITOR")

    if pvName.endswith(DIAGNOSTIC_SUFFIXES):
        return _policy(DIAGNOSTIC_POLICY, 5.0, "MONITOR")

    return _policy(PHYSICAL_POLICY, 5.0, "MONITOR")
