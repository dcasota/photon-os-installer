# /*
#  * Copyright © 2026 VMware, Inc.
#  * SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-only
#  */
"""Unit tests for photon_installer/dhcprelease.py, with networkctl and
systemctl replaced by fakes: no network, no root, no systemd needed."""

import ast
import json
import logging
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "photon_installer"))

import dhcprelease  # noqa: E402

LOGGER = logging.getLogger("test_dhcprelease")


def link(name, sources=(), network_file=None):
    entry = {"Name": name, "Addresses": [{"ConfigSource": s} for s in sources]}
    if network_file:
        entry["NetworkFile"] = network_file
    return entry


class FakeSystem:
    """Answers systemctl and networkctl like networkd would, one list per poll."""

    def __init__(self, networkd_active=True, polls=None, reload_fails=False):
        self.networkd_active = networkd_active
        self.polls = list(polls or [])
        self.reload_fails = reload_fails
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "systemctl":
            return subprocess.CompletedProcess(cmd, 0 if self.networkd_active else 3, "", "")
        if cmd[1:] == ["reload"]:
            if self.reload_fails:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        interfaces = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"Interfaces": interfaces}), "")


@pytest.fixture
def system(monkeypatch, tmp_path):
    def install(fake):
        monkeypatch.setattr(dhcprelease.subprocess, "run", fake.run)
        monkeypatch.setattr(dhcprelease, "NETWORK_DIR", str(tmp_path))
        monkeypatch.setattr(dhcprelease.time, "sleep", lambda s: None)
        monkeypatch.setattr(dhcprelease.shutil, "which", lambda name: f"/usr/bin/{name}")
        return fake
    return install


def override(tmp_path, name):
    return os.path.join(str(tmp_path), f"{dhcprelease.NETWORK_PREFIX}{name}.network")


def test_inactive_networkd_is_left_alone(system):
    fake = system(FakeSystem(networkd_active=False))
    record = dhcprelease.release_leases(LOGGER)
    assert record["links"] == {} and record["error"] is None
    assert [c[0] for c in fake.calls] == ["systemctl"]


def test_links_without_dhcp_addresses_are_not_touched(system, tmp_path):
    fake = system(FakeSystem(polls=[[link("lo", ["foreign"]), link("eth0", ["static"])]]))
    record = dhcprelease.release_leases(LOGGER)
    assert record["links"] == {}
    assert ["networkctl", "reload"] not in fake.calls
    assert os.listdir(tmp_path) == []


def test_lease_released_once_override_applied_and_address_gone(system, tmp_path):
    path = override(tmp_path, "eth0")
    fake = system(FakeSystem(polls=[
        [link("eth0", ["DHCPv4"])],
        [link("eth0", ["DHCPv4"], "/run/systemd/network/99-dhcp-en.network")],
        [link("eth0", ["DHCPv4"], path)],
        [link("eth0", [], path)],
    ]))
    record = dhcprelease.release_leases(LOGGER)
    assert record["links"] == {"eth0": "released"} and record["error"] is None
    assert fake.calls.count(["networkctl", "reload"]) == 1
    with open(path) as f:
        content = f.read()
    assert "Name=eth0\n" in content and "DHCP=no\n" in content and content.count("SendRelease=yes") == 2
    assert oct(os.stat(path).st_mode & 0o777) == "0o644"


def test_every_link_shares_one_reload_and_one_deadline(system, tmp_path, monkeypatch):
    clock = iter(range(0, 1000))
    monkeypatch.setattr(dhcprelease.time, "monotonic", lambda: next(clock))
    path0, path1 = override(tmp_path, "eth0"), override(tmp_path, "eth1")
    fake = system(FakeSystem(polls=[
        [link("eth0", ["DHCPv4"]), link("eth1", ["DHCPv6"])],
        [link("eth0", [], path0), link("eth1", ["DHCPv6"], path1)],
    ]))
    record = dhcprelease.release_leases(LOGGER)
    assert record["links"] == {"eth0": "released", "eth1": "timeout"}
    assert fake.calls.count(["networkctl", "reload"]) == 1
    polls = [c for c in fake.calls if c[1:] == ["--json=short", "list"]]
    assert len(polls) <= dhcprelease.RELEASE_TIMEOUT_SEC + 1


def test_vanished_link_counts_as_released(system, tmp_path):
    system(FakeSystem(polls=[[link("eth0", ["DHCPv4"])], []]))
    assert dhcprelease.release_leases(LOGGER)["links"] == {"eth0": "released"}


def test_glob_names_are_skipped(system, tmp_path):
    fake = system(FakeSystem(polls=[[link("eth*0", ["DHCPv4"])]]))
    record = dhcprelease.release_leases(LOGGER)
    assert record["links"] == {"eth*0": "skipped: name is not a literal match"}
    assert ["networkctl", "reload"] not in fake.calls


@pytest.mark.parametrize("fake", [
    FakeSystem(polls=[[link("eth0", ["DHCPv4"])]], reload_fails=True),
])
def test_command_failure_is_recorded_not_raised(system, fake):
    system(fake)
    record = dhcprelease.release_leases(LOGGER)
    assert record["error"] and "CalledProcessError" in record["error"]


def test_unexpected_json_is_recorded_not_raised(system, monkeypatch):
    fake = system(FakeSystem(polls=[[]]))
    original = fake.run

    def bad_json(cmd, **kwargs):
        if cmd[0] == "networkctl":
            return subprocess.CompletedProcess(cmd, 0, "[1, 2]", "")
        return original(cmd, **kwargs)

    monkeypatch.setattr(dhcprelease.subprocess, "run", bad_json)
    record = dhcprelease.release_leases(LOGGER)
    assert record["error"] and record["links"] == {}


def test_every_command_carries_a_timeout(system, tmp_path, monkeypatch):
    seen = []
    fake = FakeSystem(polls=[[link("eth0", ["DHCPv4"])], [link("eth0", [], override(tmp_path, "eth0"))]])

    def checked(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        return fake.run(cmd, **kwargs)

    system(fake)
    monkeypatch.setattr(dhcprelease.subprocess, "run", checked)
    dhcprelease.release_leases(LOGGER)
    assert seen and all(t is not None and 0 < t <= dhcprelease.COMMAND_TIMEOUT_SEC for t in seen)


def test_missing_networkctl_is_reported_without_touching_anything(system, monkeypatch, tmp_path):
    fake = system(FakeSystem(polls=[[link("eth0", ["DHCPv4"])]]))
    monkeypatch.setattr(dhcprelease.shutil, "which", lambda name: None)
    record = dhcprelease.release_leases(LOGGER)
    assert record["error"] == "networkctl is not installed"
    assert record["links"] == {}
    assert [c[0] for c in fake.calls] == ["systemctl"]
    assert os.listdir(tmp_path) == []


def test_installer_initrd_keeps_networkctl():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "photon_installer", "generate_initrd.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    clean_up = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "clean_up")
    removed = {n.value for n in ast.walk(clean_up) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "/usr/bin/networkctl" not in removed
