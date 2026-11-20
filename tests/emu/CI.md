# Running the emulator tests in CI: a proposal

Nothing here is wired up. The workflow is in `tests/emu/ci/emulator-tests.yml`, outside `.github/workflows/`, so GitHub does not
run it. This note says where it should run, how often, and how the firmware gets there.

## What a run costs

Measured on one machine (Ryzen 9 7950X3D, 32 threads, Linux, Docker; QEMU 10 with TCG, no KVM), `tests/emu/run.sh` with both
default instances side by side:

| | MPC 3.9.1 Gen1 (armv7) | MPC 3.10.0 Gen2 (aarch64) |
|---|---|---|
| Boot, install the Manager, open it, snapshot (once per build) | 275 s | 171 s |
| `fresh_install` | 20 s | 5 s |
| `plugin_install_update_remove` | 276 s | 165 s |
| `addin_install_remove` | 188 s | 93 s |
| `self_update` | 197 s | 100 s |
| `long_press` | 29 s | 16 s |
| `theme_survives_update` | 327 s | 192 s |
| `drive_name_with_space` | 507 s | 358 s |
| `exfat_noexec_drive` | 179 s | 119 s |
| `full_disk` | 360 s | 187 s |
| `readonly_addins` | 44 s | 16 s |
| `power_cut_mid_apply` | 315 s | 199 s |
| `catalog_retry` | 32 s | 25 s |
| `vst2_plugin_json` | 125 s | 86 s |
| **All 13 scenarios** | **2599 s** | **1561 s** |

Wall time for the whole run with the snapshots already made: 2607 s (43 minutes); the Gen1 machine sets it. A first run of a
new build adds the snapshot step: about 48 minutes.

Once per machine, before any run: the instances (two to three minutes each here, from the update images) and the Manager's skin
(`vst/build_themes.sh`, about 15 minutes; rebuilt only when the layout or artwork changes). `tests/emu/build.sh` then takes about
a minute.

Everything under TCG runs on one host thread per guest core, so the wall time is set by single-thread speed, not by core count.
The two machines run in parallel and need two guests' worth of cores (Gen1 four, Gen2 four) and about 6 GB of RAM together.

## Where it can run

| Runner | Acceleration | Firmware | Verdict |
|---|---|---|---|
| GitHub-hosted x86-64 (`ubuntu-24.04`, 4 vCPU) | TCG only: the guests are ARM | must be downloaded on every run | too slow for every PR; workable nightly, if the firmware question is solved |
| GitHub-hosted arm64 (`ubuntu-24.04-arm`) | TCG only as far as we know: no `/dev/kvm` on hosted arm64 runners (not verified here) | same | no gain over x86-64 without KVM |
| Self-hosted x86-64 box | TCG | on its disk | **recommended**: the numbers above are from one |
| Self-hosted arm64 box with KVM | Gen2 (aarch64) guests under KVM; Gen1 (armv7) only on cores that run AArch32 guests, e.g. a Raspberry Pi 4 (QEngine docs/BUILDING.md) | on its disk | fastest, but not tried here; QEngine's KVM path was not exercised by this harness |

The hosted-runner time is not measured. A 4-vCPU hosted runner has slower cores than the machine above and would run two
four-core guests on four threads; expect the run to take two to three times as long, and a failed run to be harder to tell from a
slow one (every wait in the harness is a timeout). Measure it once before relying on it:
`time tests/emu/run.sh -i mpc-g2-3100 -k fresh_install -k plugin_install_update_remove`.

## How often

* **Every PR:** the offline tests (`tests/run.sh`), which already run in CI and now need no network (with the `regress` section added to the matrix). Not the emulator.
* **Nightly, and on demand (`workflow_dispatch`):** the full emulator run on the self-hosted box.
* **Per PR, later, if wanted:** a short set on the self-hosted box (`-i mpc-g2-3100 -k fresh_install -k plugin_install_update_remove
  -k self_update`, about 7 minutes here including the snapshot), restricted to branches of this repository (see below).

## The firmware

Akai's update images are not redistributable. They are not in this repository and the workflow never uploads them or anything
built from them:

* The images live on the self-hosted runner, in `FIRMWARE_DIR`, put there by hand once. The instances QEngine builds from them
  stay in the QEngine checkout on that runner.
* No `actions/cache`, no artifact holds an image, an instance or a VM snapshot. The uploaded results are the Manager's own log
  files and screen captures.
* A pull request from a fork must never run on that runner: its code would run next to the images. The workflow's
  `pull_request` trigger is commented out; if it is enabled, keep the `head.repo.full_name == github.repository` condition and
  set the repository to require approval for outside contributors' workflows.

If a hosted runner is used instead, the images have to come from a private store on each run (a URL in a repository secret, the
sha256 checked after download), nothing is cached, and the job is limited to `schedule` and `workflow_dispatch`: secrets are not
given to fork PRs, and a cache in a public repository can be read by one.

Images used (sha256, to check a runner's copies):

    4c0f7797a006313fad206f9513eca8b3823e834c9e0941a6b5f088386cb835d6  MPC-3.9.1-Gen1-update.img
    95b694f5880ae905ff3caf927dce2642b2b69acbb0e869838f2e261d3a65c72b  MPC-3.9.1-Gen2-update.img
    c5b941e1b07d261a58b41784e2ff4f911b3ca4dd25969698eaa35aa706d9dec2  MPC-3.10.0-Gen2-update.img

QEngine: https://github.com/Peyton-C/QEngine at commit `83a03e5`, unmodified.

## What a failure looks like

`results/<instance>/<build>/summary.json` lists each scenario with its failed checks; each scenario's folder has the screen
captures (`NN-name.png`, `99-end.png`), the device's `apply.log`, `result`, `device.txt` and `manager.log`. `results/<instance>-<build>.log`
is the run's own log, including every navigation step.

A scenario can fail because of the harness (it reads the screen by OCR and every wait is a timeout). Rerun it alone with `-k`
before reading much into a single failure.
