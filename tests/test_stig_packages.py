#!/usr/bin/env python3
# Copyright 2026 VMware, Inc.
# SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-only

"""
The STIG package set, and the places that restate it.

`stigenable.KS_STIG_PACKAGES` is the declaration of what "Apply STIG
hardening" installs. Two other places have to agree with it:

  examples/ova/packages_stig.json   the OVA package list
  isoBuilder.downloadPkgs()         what the media must be able to satisfy

Both have drifted from it before. The media once offered the STIG option
without carrying the packages, which failed the install with
"Error(1011) : No matching packages"; and the OVA list kept three packages
after they were dropped from KS_STIG_PACKAGES. Neither drift is visible to the
image tests, which only assert that an image file appeared.

So these are the checks that a list restated in two places actually says the
same thing in both. They need no docker, no repo and no network, and they run
in milliseconds - the point is that nothing about them can be skipped when the
slow tests are.
"""

import json
import os
import sys

import pytest

POI_TEST_PATH = os.path.dirname(os.path.abspath(__file__))
POI_PATH = os.path.dirname(POI_TEST_PATH)
sys.path.insert(0, os.path.join(POI_PATH, "photon_installer"))

OVA_PKG_LIST = os.path.join(POI_PATH, "examples", "ova", "packages_stig.json")
OVA_STIG_KS = os.path.join(POI_PATH, "examples", "ova", "minimal_stig_ks.yaml")

# Dropped from KS_STIG_PACKAGES as redundant: libselinux-utils is already a
# dependency of selinux-policy, libgcrypt is a workaround for an unversioned
# Requires in aide.spec, and ntp is installed but never configured - the
# stig-hardening role notifies none of its three time-sync handlers and none of
# its PHTN-50-xxxxxx controls covers time sync. They must stay dropped in both
# places, or the OVA quietly reintroduces what the installer removed.
DROPPED = ["libselinux-utils", "ntp", "libgcrypt"]


def ova_packages():
    with open(OVA_PKG_LIST) as f:
        return json.load(f)["packages"]


def test_stigenable_imports_without_a_terminal():
    """
    isoBuilder imports stigenable at module level, and stigenable pulls in the
    curses UI modules. An ISO builder runs headless, so that import has to work
    with no terminal attached - which is exactly the condition this test runs
    in.
    """
    import stigenable

    assert isinstance(stigenable.KS_STIG_PACKAGES, list)
    assert stigenable.KS_STIG_PACKAGES, "the STIG package set must not be empty"


def test_the_iso_builder_uses_the_installers_own_declaration():
    """
    Not a copy of it. Restating the names in a package list file is what let
    the media and the installer drift apart in the first place.
    """
    import isoBuilder
    import stigenable

    assert isoBuilder.stigenable.KS_STIG_PACKAGES is stigenable.KS_STIG_PACKAGES


def test_the_ova_list_covers_every_package_the_stig_option_requests():
    """
    The invariant that broke. An OVA built from packages_stig.json is meant to
    come out equivalent to an install with STIG selected, so it cannot be
    missing anything KS_STIG_PACKAGES asks for.
    """
    import stigenable

    missing = sorted(set(stigenable.KS_STIG_PACKAGES) - set(ova_packages()))
    assert not missing, (
        f"examples/ova/packages_stig.json is missing {missing}, which "
        "stigenable.KS_STIG_PACKAGES requests"
    )


@pytest.mark.parametrize("pkg", DROPPED)
def test_a_dropped_package_stays_dropped_in_both_places(pkg):
    import stigenable

    assert pkg not in stigenable.KS_STIG_PACKAGES
    assert pkg not in ova_packages(), (
        f"{pkg} was dropped from KS_STIG_PACKAGES but is still explicit in "
        "examples/ova/packages_stig.json"
    )


def test_the_ova_package_list_is_well_formed():
    """
    It is hand-edited JSON with several entries per line, which is easy to
    break with a stray comma. A malformed list fails the OVA build minutes in;
    here it fails immediately.
    """
    with open(OVA_PKG_LIST) as f:
        data = json.load(f)

    assert isinstance(data["packages"], list)
    assert all(isinstance(p, str) and p for p in data["packages"])
    assert len(data["packages"]) == len(set(data["packages"])), "duplicate entries"


def test_the_stig_ova_kickstart_uses_the_stig_package_list():
    """
    Guards the wiring rather than the content: an example that silently points
    at packages_minimal.json would make every STIG assertion above vacuous for
    the image that is actually built.
    """
    with open(OVA_STIG_KS) as f:
        assert "packagelist_file: packages_stig.json" in f.read()


class _Config(dict):
    """Stands in for install_config, which is a plain dict."""


class _Stub:
    """
    Enough of StigEnable to call the method under test.

    StigEnable.__init__ builds curses windows, so it cannot be constructed in a
    test. set_stig_enabled touches nothing but install_config, so calling it
    unbound against a stub exercises the real code with none of the UI.
    """

    def __init__(self):
        self.install_config = _Config()


def test_selecting_stig_requests_exactly_the_declared_packages():
    """
    The only runtime consumer of KS_STIG_PACKAGES is the curses menu, so
    nothing else in the suite executes this path.
    """
    import stigenable

    s = _Stub()
    stigenable.StigEnable.set_stig_enabled(s, True)

    assert s.install_config["additional_packages"] == stigenable.KS_STIG_PACKAGES
    assert s.install_config["ansible"] == stigenable.KS_STIG_ANSIBLE


def test_answering_no_leaves_no_trace_of_a_previous_yes():
    """
    The menu can be answered twice. If "no" only skipped the assignment, a
    yes-then-no would still install and harden.
    """
    import stigenable

    s = _Stub()
    stigenable.StigEnable.set_stig_enabled(s, True)
    stigenable.StigEnable.set_stig_enabled(s, False)

    assert "additional_packages" not in s.install_config
    assert "ansible" not in s.install_config


def test_answering_no_first_is_not_an_error():
    """del on a missing key would raise; the guard has to be there."""
    import stigenable

    s = _Stub()
    stigenable.StigEnable.set_stig_enabled(s, False)
    assert s.install_config == {}
