# /*
#  * Copyright © 2026 VMware, Inc.
#  * SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-only
#  */
"""Unit tests for the installer initrd's console keymap: clean_up() removes
/usr/share/keymaps, so vconsole.conf must not name a keymap that loadkeys
would look for there. Runs on a temporary tree: no root, no tdnf needed."""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "photon_installer"))

from commandutils import CommandUtils  # noqa: E402
from generate_initrd import IsoInitrd  # noqa: E402

LOGGER = logging.getLogger("test_initrd_vconsole")


def initrd(tmp_path):
    iso_initrd = IsoInitrd.__new__(IsoInitrd)
    iso_initrd.logger = LOGGER
    iso_initrd.cmd_util = CommandUtils(LOGGER)
    iso_initrd.initrd_path = str(tmp_path)
    for directory in ["etc", "usr/sbin", "usr/share/keymaps/i386/qwerty", "usr/share/terminfo"]:
        os.makedirs(tmp_path / directory)
    (tmp_path / "usr/share/keymaps/i386/qwerty/us.map.gz").write_bytes(b"")
    return iso_initrd


def vconsole_settings(tmp_path):
    settings = {}
    for line in (tmp_path / "etc/vconsole.conf").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip()
    return settings


def test_vconsole_conf_keeps_the_kernel_keymap(tmp_path):
    initrd(tmp_path).write_vconsole_conf()
    assert vconsole_settings(tmp_path) == {"KEYMAP": "@kernel"}


def test_vconsole_conf_replaces_a_packaged_one(tmp_path):
    iso_initrd = initrd(tmp_path)
    (tmp_path / "etc/vconsole.conf").write_text("KEYMAP=de\nFONT=lat9w-16\n", encoding="utf-8")
    iso_initrd.write_vconsole_conf()
    assert vconsole_settings(tmp_path) == {"KEYMAP": "@kernel"}


def test_clean_up_removes_the_keymaps_vconsole_conf_does_not_need(tmp_path):
    iso_initrd = initrd(tmp_path)
    iso_initrd.write_vconsole_conf()
    iso_initrd.clean_up()
    assert not (tmp_path / "usr/share/keymaps").exists()
    assert (tmp_path / "usr/share/terminfo").is_dir()
    assert vconsole_settings(tmp_path) == {"KEYMAP": "@kernel"}
