#!/usr/bin/python

# BDX Archiver Appliance policies.
#
# This file follows the official Archiver Appliance policy interface:
# getPolicyList(), getFieldsArchivedAsPartOfStream(), and
# determinePolicy(pvInfoDict). The storage plugin URLs intentionally use
# ARCHAPPL_* environment variables so that retention and paths remain deployment
# configuration, not repository constants.


def getPolicyList():
    pvPoliciesDict = {}
    pvPoliciesDict["BDX_Physical_5s"] = "BDX physical readbacks updated around every 5 seconds"
    pvPoliciesDict["BDX_State_Change"] = "BDX state and boolean transition PVs"
    pvPoliciesDict["BDX_Diagnostic_Change"] = "BDX string and diagnostic transition PVs"
    pvPoliciesDict["BDX_Heartbeat_Slow"] = "BDX heartbeat counters archived slowly when explicitly requested"
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
    "&hold=${BDX_ARCHIVER_MEDIUM_TERM_HOLD_DAYS}&gather=1"
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
        if userPolicyOverride == "BDX_Physical_5s":
            return _policy("BDX_Physical_5s", 5.0, "MONITOR")
        if userPolicyOverride == "BDX_State_Change":
            return _policy("BDX_State_Change", 1.0, "MONITOR")
        if userPolicyOverride == "BDX_Diagnostic_Change":
            return _policy("BDX_Diagnostic_Change", 5.0, "MONITOR")
        if userPolicyOverride == "BDX_Heartbeat_Slow":
            return _policy("BDX_Heartbeat_Slow", 60.0, "SCAN")

    if (
        pvName.endswith(":COMM_OK")
        or pvName.endswith(":OUTPUT_RBV")
        or pvName.endswith(":RUN_RBV")
        or pvName.endswith(":FAULT")
        or pvName.endswith(":DEVIATION_WARNING")
        or pvName.endswith(":DEVIATION_ALARM")
        or pvName.endswith(":STATUS_OK")
    ):
        return _policy("BDX_State_Change", 1.0, "MONITOR")

    if (
        pvName.endswith(":COMM_STATUS")
        or pvName.endswith(":ERROR_MESSAGE")
        or pvName.endswith(":OUTPUT_STATE")
        or pvName.endswith(":RUN_STATE")
        or pvName.endswith(":DEVIATION_STATUS")
        or pvName.endswith(":STATUS")
    ):
        return _policy("BDX_Diagnostic_Change", 5.0, "MONITOR")

    if pvName.endswith(":HEARTBEAT"):
        return _policy("BDX_Heartbeat_Slow", 60.0, "SCAN")

    return _policy("BDX_Physical_5s", 5.0, "MONITOR")
