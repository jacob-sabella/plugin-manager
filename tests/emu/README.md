# Emulator tests

The Plugin Manager running inside real MPC firmware, in QEMU, driven the way a person drives it: taps on the screen, then a look at
what is on the device. [QEngine](https://github.com/Peyton-C/QEngine) boots the unmodified firmware; this folder adds the harness.

    tests/emu/build.sh          # the Manager for armv7 and aarch64, pointed at the fixture catalog, plus the fixture packages
    tests/emu/run.sh            # every scenario on a Gen1 and a Gen2 machine, side by side

`run.sh -k self_update` runs one scenario, `-i mpc-g2-391` picks an instance, `--list` names the scenarios, `--label` picks a build.
Results: `results/<instance>/<build>/summary.json`, and per scenario the screen captures and the device's own logs.

## What it needs

* Docker, with arm and arm64 user emulation registered (binfmt) for the cross builds. No root, no KVM.
* A QEngine checkout with instances built from Akai's update images (not in this repository, see below):
  `QENGINE=<path>`, default `../QEngine` beside this repository.
* An mpc-vst-plugins checkout for the wrapper and release tools: `MPC_VST=<path>`. The fixture packages are built with its
  installer and uninstaller, so a fix in those scripts is tested by pointing `MPC_VST` at the checkout that has it.
* `docker build -t pm-emu-host tests/emu` once: QEMU, a VNC client, ssh and tesseract in one image.

Building the instances (once; about ten minutes each, needs the firmware images in `FIRMWARE_DIR`):

    tests/emu/qe scripts/build_scripts/new_instance.sh --name mpc-g1-391  --product-code ACVA --firmware $FIRMWARE_DIR/MPC-3.9.1-Gen1-update.img
    tests/emu/qe scripts/build_scripts/new_instance.sh --name mpc-g2-3100 --product-code ACVG --firmware $FIRMWARE_DIR/MPC-3.10.0-Gen2-update.img

## How it works

* `qe` runs a command in the `pm-emu-host` container; `in-container` gives each machine its own container, so every VM uses the same
  ports and several run at once.
* `shim/qemu-system-aarch64` sits in front of QEMU: each instance disk gets a qcow2 overlay in `state/<instance>/<build>/`, so the
  instance is never written, and the guest's network is restricted to one address, the fixture server.
* `emu.py` is the machine: boot, ssh, the QEMU monitor (snapshots, power cut), the screen and touches over VNC, OCR of the screen,
  the Manager's web API from inside the guest, and `Fixture`, the catalog and zips the guest downloads from.
* `prepare.py` takes a booted instance to "MPC running, the Manager on a track, its screen open" and snapshots it. That is done once
  per instance and build (three to four minutes); every scenario then resumes the snapshot in about three seconds.
* `scenarios.py` holds the scenarios; `run.py` runs them and writes the results.

A scenario queues changes with a tap or through the Manager's web API (the same code path as the phone page), runs APPLY, and then
asserts on the device: `/tmp/openmpc-pm/{device.txt,apply.log,result}`, the plugin folders, `MPC.settings`, the running MPC's
environment, and what the screen shows.

## The scenarios

| Scenario | What it does |
|---|---|
| `fresh_install` | the Manager as first installed: listed, skin drawn, catalog read, install location found |
| `plugin_install_update_remove` | a plugin installed with a tap, updated, removed |
| `addin_install_remove` | an addin installed and removed; MPC really preloads it in between |
| `self_update` | the Manager updates itself |
| `theme_survives_update` | the dark theme is still there after the Manager's own update |
| `long_press` | slow presses on a button count once each |
| `power_cut_mid_apply` | QEMU is killed while an update keeps its safety copy; after the reboot the update goes through |
| `full_disk` | the install drive fills up: the message says so, a removal still works, MPC.settings stays whole |
| `drive_name_with_space` | an exFAT card called "EMU CARD": registered, installed to, updated, removed |
| `exfat_noexec_drive` | a noexec drive is refused with its reason, installs go to the internal drive |
| `readonly_addins` | `/data/mpc-addins` read-only: an addin is refused before MPC stops, a plugin still installs |
| `catalog_retry` | the catalog cannot be fetched at first; the Manager tries again on its own |
| `vst2_plugin_json` | the MPC OS 3.10.1 install path (VST2 Support switched on in MPC.settings): `VST2 Plugins` and a correct plugin.json |

Drives are loop images mounted under `/media/<label>` in the guest, with the mount options a device was seen to use. The power cut
is QEMU's `quit` after a guest `sync`: what was written is on the disk, nothing after it is.

## Testing a fix

`build.sh --ref <commit> --label before` builds the Manager as it was at a commit; `run.sh --label before -k <scenario>` runs a
scenario against it. A scenario written for a bug fails there and passes on the working tree.

## Firmware

The update images are Akai's and are not redistributable: they are never committed, and neither are the instances built from them.
See `SPIKE.md` for what was tried on which firmware and what the emulator cannot show, and `CI.md` for running this in CI.

## By hand

    tests/emu/vm up mpc-g2-3100 work ready     # resume the ready snapshot and keep it running
    tests/emu/vm sh 'cat /tmp/openmpc-pm/device.txt'
    tests/emu/vm tap /tmp/s.png 640,400 s2      # tap, wait two seconds, capture
    tests/emu/vm down
