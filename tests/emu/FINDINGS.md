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

## The harness on a slow host (GitHub-hosted runners)

A hosted runner runs the guest about 3.3 times slower than a desktop. `power_cut_mid_apply` failed there on `mpc-g2-3101`: after
the reboot the navigator ended on a drum track's sample editor and waited until it gave up. Reproduced on a desktop by limiting the
container to 0.4 CPU. The navigator acted on screens that were still being drawn: its "dialog: tap the left button" also matched
the project's loading dialog, whose CANCEL is at that place ("Operation cancelled"), and a plugin's loading dialog, where the tap
reaches TRACK - beneath; it also tracked what it had done in flags, so a tap that was not taken left it tapping the plugin field of
a drum track; and its double tap on the plugin field, spaced as two taps, came to a slow MPC as two single taps.

Now (emu.py `settle`, `press`; prepare.py `open_manager`): every step reads a screen that has stopped changing and waits after a
tap for the screen to change, so a step that did not take is seen and repeated; a progress dialog is waited out; a dialog's button
is read before it is tapped; the selected track is read from its panel; the double tap is tight; a screen nothing knows that stays
put for a minute restarts MPC and starts over (twice at most); the navigator's captures go to the results (`nav/`) when it fails.
The scenarios' own taps on the Manager wait for a still screen and read the state once it holds. `PM_EMU_TIMESCALE` multiplies every
timeout; `run.sh` sets 4 on GitHub Actions. Desktop runs take about as long as before.

## The MPC Sample (added 2026-11-20)

Instance `mpc-sample-130` (AC50, MPC Sample 1.3.0.12, from `MPC_SAMPLE_1.3.0_UPDATE.exe`, QEngine 83a03e5 unmodified): the instance
builds in about a minute, ssh answers after 28 s, MPC Sample runs after 29 s and shows its main screen after 36 s; the machine
snapshot takes 70 s. The Sample has no plugin browser, so the Manager's scenarios are not run on it; `sample_scenarios.py` tests what a
user does today instead (`sample.py` holds the machine): `sample_install`, `sample_plugin_loads`, `sample_reboot`, `sample_uninstall`,
all passing. A new fixture effect (`src/fxprobe.c`, a real VST2 effect that logs when MPC loads it) is installed with its release
zip's `install.sh -t /media/MPCSD/Synths`, put into the project's main output by the RACK addin's apply helper, and loaded by MPC once
the card's Synths folder is bind-mounted exec, as in mpc-vst-plugins' NOTES (2026-11-04/05).

What the instance needed (all in the harness, nothing in QEngine):

* **Network.** MPC Sample tells connman to switch wired networking off when it starts (its SSH runs over the USB gadget link), so
  the emulator's NIC went down and ssh never answered. The serial set-up now, only when the guest has no default route, tells
  connman to leave the NIC alone (a copy of its main.conf in /run) and gives it QEMU's usual address. Other machines have a route.
* **The SD card.** QEngine's udev rule makes edisksd ignore every virtio disk. A rule of the harness's lets its own disk through
  (exFAT, label MPCSD), and MPC Sample then mounts it itself at `/media/MPCSD` with the device's options (`noexec,nosuid,nodev,nosymfollow`).
* **The settings file.** A new MPC Sample writes no `MPC Sample.settings` at all, not even on stop; it writes one the first time it
  has something to keep, which is the card's Synths folder (added to SynthContentLocations as it starts). Every installer needs that
  file. The machine has the folder and the file.
* **A root that cannot be remounted.** QEngine's root can be remounted read-write, the device's cannot (the addin installer put its
  drop-in in /run there). With a remountable root the addin would survive a reboot in the emulator and not on the device, so the
  machine sets the ext4 `read-only` feature on its root.

Bugs found on the Sample (not fixed here):

* **The published RACK addin 1.0.0 zip's bundled installer does not install on a Sample.** The problem is the installer in the zip,
  not RACK: the zip was built against an mpc-vst-plugins from before e78fe440. Its `addin-lib.sh` picks `acvs` as MPC's service,
  writes `/run/systemd/system/acvs.service.d/90-mpc-addins.conf` and stops with "Failed to restart acvs.service: Unit acvs.service
  not found." (exit 5); MPC never preloads it. And its `install.sh` copies `sample-rack-apply` as 644, so RACK's APPLY fails
  ("Permission denied" from a shell, 203/EXEC from systemd-run). RACK itself works on a real Sample when installed with the current
  installer (ac50; executable by content). Fixed in RACK 1.0.1 (released 2026-11-20), built with the current installer: the harness
  serves its published zip as it is, and its own `install.sh -y` installs, preloads and applies (the four sample scenarios pass).
* **`install.sh` and `uninstall.sh` without `-t`.** Neither looks at the card. `install.sh` stops with "cannot create /sdcard/Synths"
  (nothing changed). `uninstall.sh` reports success, removes the settings entry (matched by uid) and leaves the plugin's folder on
  the card, unlisted. `mpc-store.sh` has the same `/sdcard/Synths` default (not run here).
* **Wording.** The noexec warning advises `-t /sdcard/Synths` or the Gen1 auto exec patch, neither of which applies to a Sample, and
  install.sh ends with "Add ... to a track from the plugin browser", which the Sample has not got.

The reboot (recorded as findings by `sample_reboot`, as on the device in NOTES 2026-11-05): after a clean power-off and on, MPC
mounts the card again (noexec), the plugin is still on the card, in the settings and in the project, but the exec bind mount is gone
and MPC logs only "Attempting to load VST" for it: nothing loads and nothing says so. The addin's /run drop-in is gone too, so MPC
starts without RACK. Binding the folder exec again and restarting MPC brings the plugin back. With the card noexec from the start the
same silent failure is seen (`sample_plugin_loads`). After an uninstall, MPC starts with a project that still names the plugin and
runs on without it.

Not covered: `mpc-store.sh` and catalog entries limited to `mpc-sample` (the fixture has no catalog.tsv); the RACK page itself and the
draw handler (the apply helper is driven directly, as RACK's APPLY runs it); a real SD card and edisksd's handling of one; audio.

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
