# Reliability pass, November 2026: what was fixed, what was not, what the emulator cannot cover

Branch `reliability-qengine` (local only). The emulator tests are in this folder; `SPIKE.md` has the per-firmware notes and `CI.md`
the CI proposal. Two fixes live in the installer scripts of mpc-vst-plugins, on a local branch there (`fix/pm-reliability`); the
Manager's embedded copy of `vst2_migrate.sh` was regenerated from it, and the emulator fixture was built with it.

## Fixed, each with a test that failed first

| What | Seen as | Test |
|---|---|---|
| A press held longer than a quarter second counted twice | INSTALL queued and unqueued, two pages turned, a letter typed twice | emulator `long_press`; `vst/skin_taps.py --selftest` |
| The dark theme was lost on the Manager's own update | "light theme after every update", plus a second restart prompt | emulator `theme_survives_update` |
| #46: a half-made safety copy blocked every later update | "an earlier update left a safety copy" on each try | `tests/run.sh` safety; emulator `power_cut_mid_apply` |
| #46: the safety copy was sized by what the drive reported | 932 MB copied on an exFAT card for 70 MB of files, minutes with a dark screen | `tests/run.sh` safety |
| #46: plugin.json rewritten after a failed item | the Manager unlisted after a failed update | `tests/run.sh` vst2 |
| #46: plugin.json naming a binary that is not in the folder | same | `tests/run.sh` vst2 (script fix in mpc-vst-plugins) |
| MPC OS 3.10.1: only internal drives tried when no Synths folder is usable | "No install location" with a usable SSD attached (the Force report) | `tests/devices/force-3101-external-only` |
| MPC OS 3.10.1: with the content drive full, plugins went to the data partition (`/media/az01-internal/VST2 Plugins`), which MPC does not scan on a unit with a content drive | a plugin installed and never listed (Key 37, 3.10.1.12) | emulator `full_disk` on Gen1 and Gen2 3.10.1; `tests/devices/key37-3101-storage-full` |
| A full install drive turned everything off, removals included | "None of MPC's 1 Synths folders can take a plugin", and no way to free space from the Manager | emulator `full_disk` (Manager, and `uninstall.sh` in mpc-vst-plugins) |
| An addin was offered with `/data/mpc-addins` read-only | a pointless restart, then a rollback | emulator `readonly_addins` |
| One failed catalog fetch left the Manager offline | offline after a boot until REFRESH is tapped | emulator `catalog_retry` |
| The offline tests read the live catalog | tests that changed result when the catalog did | `tests/run.sh` now runs on a network with no route out |
| CI never ran the `regress` section | - | workflow matrix |

## Reproduced, not fixed

* **Drum pad patch on an MPC One Gen 2.** On Gen2 the Manager installs the "MPC Add-Ins patch" with it, and that patch's script
  looks for `MPC Documents` in `/nvme`, `/sdcard` and `/data/UserData`. A One Gen 2 keeps its content on `/storage`
  (`tests/devices/one-gen2-storage.sh`, from a user's device). Run inside the 3.10.0 Gen2 firmware as a One Gen 2, with a drive
  holding `MPC Documents` at `/storage`, the script answers "cannot find content root"; with the same drive at `/nvme` it is accepted. The Manager does not run a
  patch whose companion cannot be installed (`pt_companions` in manager.c; the PATCHES page itself was not driven here). The drum pad patch itself reports the firmware as supported. Not fixed
  here: the list of drives is also inside the signed Lua device script, which cannot be changed without the signing key, and
  where a One Gen 2 keeps `MPC Documents` should be confirmed on a device first.
* **#46, the dark screen.** The copy is now made in memory for the reported case and takes seconds, and a copy on the drive logs
  its progress, but nothing is drawn on the screen while MPC is stopped, and the restart prompt does not warn about it. The
  prompt's wording is baked into the skin's artwork; changing it is a design change and a skin rebuild.

## Could not be reproduced

* **The Manager never appears on an MPC X.** An MPC X instance (ACV5, MPC 3.9.1) lists the Manager, draws its skin, installs,
  updates and removes a plugin. What does produce "it loads but there is no Manager screen" on any model: the Manager's folder in
  a Synths folder that is not in MPC's `SynthContentLocations`. MPC then shows the generic parameter page, and the Manager's own
  banner about it is never seen. The installer warns about this on the command line only.
* **#5** as reported (a Live 1, "has no install location", which drive and what was being installed unknown). The addin wording
  was already corrected and tested before this branch; the reporter's `device.txt` is still needed.

## Found on the way, left alone

* The addin installer's own drop-in (`90-mpc-addins.conf`) loses to a read-only LD_PRELOAD drop-in that sorts after it (QEngine's
  `override.conf` on a read-only root): the install reports success and MPC starts without the addin. No device is known to have
  such a file; the harness renames the emulator's.
* The package installer does not add its `-t` folder to `SynthContentLocations`; it warns that the plugin will have no skin.
* The installers leave one `MPC.settings.bak-*` per run beside MPC.settings; the Manager prunes them after a successful apply.

## What the emulator cannot cover

* MPC OS 3.10.1 loading a plugin from an attached drive. (3.10.1 itself is covered since 2026-11-20: instances `mpc-g1-3101` and
  `mpc-g2-3101` load the Manager from the internal drive's `VST2 Plugins`; see SPIKE.md. An earlier version of this note said
  QEngine does not boot 3.10.1: that had not been tried.)
* A Force (its firmware was not at hand), and anything that differs between models outside the firmware image: which drive is
  mounted where, how full it is, mods (MockbaMod, Hakai) and their launchers.
* Real media: SD cards, USB sticks, the SATA bay, Akai's automounter and its options, a card pulled during an install, slow or
  failing flash. Drives here are loop images; the power cut is QEMU exiting after a `sync`.
* Whether an installed plugin makes sound: no scenario loads an installed plugin onto a track other than the Manager itself.
* Wi-Fi, TLS and the CA bundle: the guest talks plain http to a fixture on the host.
* Timing and performance. TCG is several times slower than the hardware.
