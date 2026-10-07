# /*
#  * Copyright © 2026 VMware, Inc.
#  * SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-only
#  */
"""Unit tests for the DHCPv4 client identifier that photon_installer/networkmanager.py writes."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "photon_installer"))

from networkmanager import NetworkManager  # noqa: E402


def written(tmp_path, config):
    nm = NetworkManager(config, root_dir=str(tmp_path))
    nm.prepare_filesystem()
    nm.write_interfaces()
    netdir = tmp_path / "etc" / "systemd" / "network"
    return {p.name: p.read_text() for p in sorted(netdir.iterdir())}


def test_dhcp4_uses_the_mac_as_client_identifier_by_default(tmp_path):
    files = written(tmp_path, {"version": "2", "ethernets": {"dhcp-en": {"match": {"name": "e*"}, "dhcp4": True}}})
    assert "[DHCPv4]\nClientIdentifier=mac\n" in files["50-dhcp-en.network"]


def test_dhcp_identifier_duid_keeps_the_systemd_default(tmp_path):
    files = written(tmp_path, {"version": "2", "ethernets": {
        "dhcp-en": {"match": {"name": "e*"}, "dhcp4": True, "dhcp-identifier": "duid"}}})
    assert "[DHCPv4]\nClientIdentifier=duid\n" in files["50-dhcp-en.network"]


def test_an_unknown_dhcp_identifier_is_refused(tmp_path):
    with pytest.raises(Exception, match="dhcp-identifier of dhcp-en must be 'mac' or 'duid'"):
        written(tmp_path, {"version": "2", "ethernets": {
            "dhcp-en": {"match": {"name": "e*"}, "dhcp4": True, "dhcp-identifier": "uuid"}}})


def test_static_and_dhcp6_only_interfaces_get_no_dhcpv4_section(tmp_path):
    files = written(tmp_path, {"version": "2", "ethernets": {
        "static": {"match": {"name": "eth0"}, "dhcp4": False, "addresses": ["192.168.2.58/24"]},
        "v6": {"match": {"name": "eth1"}, "dhcp6": True}}})
    assert "[DHCPv4]" not in files["50-static.network"]
    assert "[DHCPv4]" not in files["50-v6.network"]


def test_legacy_dhcp_and_vlan_dhcp_get_the_mac_identifier(tmp_path):
    files = written(tmp_path, {"type": "dhcp"})
    assert "[DHCPv4]\nClientIdentifier=mac\n" in files["50-dhcp-en.network"]
    files = written(tmp_path / "vlan", {"type": "vlan", "vlan_id": "100"})
    vlan = [text for name, text in files.items() if name.endswith(".network") and "vlan" in name]
    assert vlan and all("[DHCPv4]\nClientIdentifier=mac\n" in text for text in vlan)
