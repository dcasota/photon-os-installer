# /*
#  * Copyright © 2026 VMware, Inc.
#  * SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-only
#  */
"""Release the DHCP leases of the installer environment before it reboots."""

import json
import os
import shutil
import subprocess
import time

NETWORK_DIR = "/run/systemd/network"
NETWORK_PREFIX = "00-00-photon-installer-release-"
DHCP_CONFIG_SOURCES = ("DHCPv4", "DHCPv6")
COMMAND_TIMEOUT_SEC = 10
RELEASE_TIMEOUT_SEC = 10
POLL_INTERVAL_SEC = 0.2

NETWORK_TEMPLATE = """\
[Match]
Name={name}

[Network]
DHCP=no

[DHCPv4]
SendRelease=yes

[DHCPv6]
SendRelease=yes
"""

EXPECTED_ERRORS = (OSError, ValueError, LookupError, TypeError, subprocess.SubprocessError)


def _run(cmd, timeout=COMMAND_TIMEOUT_SEC):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout).stdout


def _links(timeout=COMMAND_TIMEOUT_SEC):
    description = json.loads(_run(["networkctl", "--json=short", "list"], timeout))
    if not isinstance(description, dict):
        raise ValueError("networkctl list: no JSON object")
    return {link["Name"]: link for link in description.get("Interfaces", [])}


def _has_dhcp_address(link):
    return any(address.get("ConfigSource") in DHCP_CONFIG_SOURCES for address in link.get("Addresses", []))


def _is_released(link, network_file):
    return link is None or (link.get("NetworkFile") == network_file and not _has_dhcp_address(link))


def release_leases(logger):
    """
    Stop the DHCP client of every link that holds a DHCP address, so that
    networkd sends DHCPRELEASE, and wait until it reports the addresses gone.
    Returns the outcome per link for the install manifest; never raises.
    """
    record = {"links": {}, "error": None}
    started = time.monotonic()
    try:
        if subprocess.run(["systemctl", "--quiet", "is-active", "systemd-networkd.service"],
                          check=False, timeout=COMMAND_TIMEOUT_SEC).returncode != 0:
            return record
        if shutil.which("networkctl") is None:
            record["error"] = "networkctl is not installed"
            logger.warning("DHCP: leases not released: networkctl is not installed")
            return record

        files = {}
        for name, link in _links().items():
            if not _has_dhcp_address(link):
                continue
            if any(c in name for c in "*?["):
                record["links"][name] = "skipped: name is not a literal match"
                continue
            files[name] = os.path.join(NETWORK_DIR, f"{NETWORK_PREFIX}{name}.network")
        if not files:
            return record

        os.makedirs(NETWORK_DIR, exist_ok=True)
        for name, path in files.items():
            with open(path, "w") as f:
                f.write(NETWORK_TEMPLATE.format(name=name))
            os.chmod(path, 0o644)
        _run(["networkctl", "reload"])

        pending = dict(files)
        deadline = time.monotonic() + RELEASE_TIMEOUT_SEC
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            links = _links(min(COMMAND_TIMEOUT_SEC, remaining))
            for name, path in list(pending.items()):
                if _is_released(links.get(name), path):
                    record["links"][name] = "released"
                    logger.info(f"DHCP: {name}: lease released")
                    del pending[name]
            if pending:
                time.sleep(min(POLL_INTERVAL_SEC, max(0.0, deadline - time.monotonic())))
        for name in pending:
            record["links"][name] = "timeout"
            logger.warning(f"DHCP: {name}: lease not released within {RELEASE_TIMEOUT_SEC}s")
    except EXPECTED_ERRORS as err:
        record["error"] = repr(err)
        logger.warning(f"DHCP: leases not released: {err!r}")
    finally:
        record["duration_sec"] = round(time.monotonic() - started, 1)
    return record
