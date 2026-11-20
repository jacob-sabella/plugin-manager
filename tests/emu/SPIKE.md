# Emulator spike: does the Plugin Manager run inside real MPC firmware under QEngine?

Yes, on all three firmware images tried. MPC loads the Manager as a VST, draws its skin, and the Manager takes touches sent over VNC.
Nothing here needed a physical MPC.

Host: x86_64 Linux, Docker, QEMU 10 in a container, TCG only (no KVM for ARM guests on x86). QEngine at commit 83a03e5, unmodified.

| | MPC 3.9.1 Gen1 | MPC 3.9.1 Gen2 | MPC 3.10.0 Gen2 |
|---|---|---|---|
| QEngine instance (identity) | `mpc-g1-391` (ACVA) | `mpc-g2-391` (ACVG) | `mpc-g2-3100` (ACVG) |
| CPU | armv7 | aarch64 | aarch64 |
| Boot to ssh | 72 s | 24 s | 24 s |
| MPC running after | about 90 s | 34 s | 34 s |
| MPC lists the Manager under VST | yes | yes | yes |
| Manager `.so` loads, writes device.txt | yes | yes | yes |
| Skin drawn (full Manager screen) | yes | yes | yes |
| Taps over VNC reach the Manager | yes | yes | yes |
| Web API on :8790 inside the guest | yes | yes | yes |
| APPLY: MPC stopped, installer run, MPC restarted | yes | yes | yes |
| Whole unattended setup to a snapshot | 240 s | 155 s | 160 s |
| Resume that snapshot in a fresh QEMU | 3 s | 3 s | 3 s |

## What had to be worked around

* **Nowhere to install.** On the Gen1 instance `/sdcard` is a folder on the read-only root, so the stock installer's default target
  fails; `/media/az01-internal` (the data partition) is used instead. On Gen2, QEngine does not emulate the NVMe drive, so `/nvme`,
  `/sdcard` and `/storage` exist but are unmounted; the harness adds a disk of its own to the VM and mounts it at `/nvme`.
* **No internal drive as far as MPC OS 3.10.1 is concerned.** A unit says in its device tree which internal drive is fitted
  (`chosen/inmusic,nvme-fitted` on Gen2, `inmusic,internal-sd-fitted` on a Gen1 with the internal card), and 3.10.1 asks. QEngine
  serves the product code and serial only (its BUILD_MPC.md says so), and without the flag MPC 3.10.1 never looks in
  `<internal drive>/VST2 Plugins` when it starts: the plugin list stays at Akai's own plugins with both Enhancements switches on,
  Splice says "Download directory could not be found", and yet the Developer panel's External Plugins page finds the plugin. The
  harness binds a copy of `chosen` with the flag over the real one before MPC starts. On Gen1, 3.10.1 looks on `/sdcard` and not
  on the data partition, so there the disk is mounted at `/sdcard`. (Found by access times: which `VST2 Plugins` folders MPC reads
  when it starts.) A 1.2 GB drive drew "Low on temporary space" from MPC; the disk is 4 GB.
* **No `SynthContentLocations`.** A never-used MPC has not written that list to MPC.settings. Without it the Manager loads but MPC
  shows the generic parameter page instead of the skin, and the Manager reports "No install location". The harness adds the entry.
* **First-run screens.** Gen2 shows a three-page welcome and three dialogs before the main screen; Gen1 shows one dialog. They are
  dismissed by reading the screen (OCR), not by fixed sequences, because they differ per firmware.
* **QEMU's serial port** takes one client and wedges if written to before the guest reads it. The harness holds a single connection,
  sends nothing before the login prompt, and uses it only to start sshd.
* **Network.** The guest is cut off from everything except a fixture server (`restrict=on` plus a `guestfwd`), so tests never read
  the live catalog. The fixture is plain http: TLS and the CA bundle are not exercised.

## Found during the spike

* A press held longer than about a quarter second toggled a button twice (queue then unqueue, or two pages forward). The skin
  binds every trigger to its parameter twice, a "Mouse Down" action and a Button component; MPC runs the first when the finger lands
  and the second when it lifts, and the 250 ms double-press window only hides that for a quick tap. Fixed in `vst/skin_taps.py`.

* The same firmware images boot under other identities. `mpc-x-391` (ACV5, MPC X) and `mpc-oneg2-3100` (ACVA3, MPC One Gen 2) were
  built and run: the Manager loads, draws and installs on both. The One Gen 2 shows its first-run screens in another colour, so
  those are recognised by their text, not by a pixel.
* QEngine sets its own `LD_PRELOAD` in a drop-in named `override.conf`. On the Gen2 image that file is on a read-only root, the
  addin installer therefore writes its own drop-in (`90-mpc-addins.conf`), and `override.conf` sorts after it and wins: the install
  reports success and MPC starts without the addin. The harness renames the emulator's drop-in so it sorts first. A device with a
  read-only LD_PRELOAD drop-in that sorts after `90-` would behave the same; none is known.
* One copy of an instance runs at a time (QEngine's own rule), so two builds are tested on the same instance one after the other.

## What the emulator cannot show

* Real drives: SD cards, USB sticks, the SATA bay, Akai's automounter options, a card pulled mid-install. Drives here are loop images.
* Device identities other than the ones built unless an instance is built for each, and whatever differs between models outside
  the firmware image: which drive is mounted where (a One Gen 2 keeps its content on `/storage`, a Live III on `/nvme`), how full it
  is, what mods are installed. A Force has its own firmware, which was not at hand.
* Timing. TCG on x86 is several times slower than the hardware and has different I/O behaviour; nothing here measures performance.
* Audio, MIDI and the control surface beyond what QEngine stubs.

## MPC OS 3.10.1 (added 2026-11-20)

Instances `mpc-g2-3101` (ACVG) and `mpc-g1-3101` (ACVA), built from the 3.10.1.12 update images with the same command. Both boot,
and with the stand-ins above MPC lists the Manager under **External Plugins**, loads it from `<drive>/VST2 Plugins` and draws its
skin. What the harness does there, decided by what the MPC binary supports (it has the switch's name) and not by a version number:

* switches on Enable Enhancements and External Plugin Support (`Enhancements.Enabled`, `Enhancements.VST2Support`) in MPC.settings
  with MPC stopped; MPC's own Preferences page shows both ticked afterwards. A user does this by hand in Preferences.
* runs the package's `install.sh` without `-t`: the installer itself picks `<drive>/VST2 Plugins`.
* `t.synths` in a scenario is then that folder, and the scenarios that differ by design say so (`t.external`): there is nothing
  to register for a card, and a noexec drive's `VST2 Plugins` is still offered as a place to install to.

Not covered on 3.10.1: an attached drive as MPC's automounter (edisksd) would mount it. The extra drives are still loop images
mounted by the test, which the Manager sees but MPC does not scan, so "MPC loads a plugin from a card" is not shown.

