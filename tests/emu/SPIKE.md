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
  `/sdcard` and `/storage` exist but are unmounted; the harness mounts an ext4 image at `/nvme`.
* **No `SynthContentLocations`.** A never-used MPC has not written that list to MPC.settings. Without it the Manager loads but MPC
  shows the generic parameter page instead of the skin, and the Manager reports "No install location". The harness adds the entry.
* **First-run screens.** Gen2 shows a three-page welcome and three dialogs before the main screen; Gen1 shows one dialog. They are
  dismissed by reading the screen (OCR), not by fixed sequences, because they differ per firmware.
* **QEMU's serial port** takes one client and wedges if written to before the guest reads it. The harness holds a single connection,
  sends nothing before the login prompt, and uses it only to start sshd.
* **Network.** The guest is cut off from everything except a fixture server (`restrict=on` plus a `guestfwd`), so tests never read
  the live catalog. The fixture is plain http: TLS and the CA bundle are not exercised.

## Found during the spike

* A press held longer than about a quarter second toggles a button twice (queue then unqueue, or two pages forward). MPC sends the
  button's value on touch-down and again on touch-up, and both the wrapper and the Manager tell a repeat from a new press by time
  alone (250 ms). A quick tap is one event; a 0.3 s or 1.5 s press is two.

## What the emulator cannot show

* MPC OS 3.10.1 (the `VST2 Plugins` folder and "External Plugin Support"): QEngine does not boot it and no image was at hand. The
  Manager's 3.10.1 logic can still be driven (it keys off a line in MPC.settings), but MPC itself will not load from that folder.
* Real drives: SD cards, USB sticks, the SATA bay, Akai's automounter options, a card pulled mid-install. Drives here are loop images.
* Device identities other than the two built (Force, X, One, Key, Live II and so on) unless an instance is built for each.
* Timing. TCG on x86 is several times slower than the hardware and has different I/O behaviour; nothing here measures performance.
* Audio, MIDI and the control surface beyond what QEngine stubs.
