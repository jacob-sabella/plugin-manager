"""Bring a freshly built QEngine instance to the state every test starts from, and snapshot it as "ready":
MPC running a project with the Plugin Manager (the test build, tests/emu/build.sh) loaded on a track and its screen open.

What is done by hand here that a real device already has:
  * somewhere to install. Gen1: /media/az01-internal (the instance's internal storage). Gen2: QEngine does not emulate the NVMe drive,
    so an ext4 image is mounted where MPC expects it, at /nvme, by a unit that survives a reboot (the power-cut test reboots).
  * SynthContentLocations in MPC.settings, which a never-used MPC has not written yet.
Everything on screen is found by its text (ocr.py), since the first-run screens and the plugin list differ between firmwares."""
import os, time
import emu, machine
from emu import Fail, log
from machine import arch, add_location, SETTINGS   # noqa: F401 (SETTINGS: run.py reads prepare.SETTINGS)

VERSION_A, VERSION_B = "9.0.0", "9.0.1"
MANAGER_DIR = "OpenMPC - VST - Plugin Manager"


def install_manager(vm, synths, version=VERSION_A):
    a = arch(vm)
    vm.sh("cd /tmp && rm -rf Plugin-Manager-G2-* && wget -q http://%s/Plugin-Manager-G2-%s-mpc-%s.zip && unzip -q -o Plugin-Manager-G2-%s-mpc-%s.zip"
          % (emu.FIXTURE_ADDR, version, a, version, a))
    out = vm.out("sh /tmp/Plugin-Manager-G2-%s/install.sh -y -n %s 2>&1" % (version, "-t '%s'" % synths if synths else ""), timeout=300)
    if "Done." not in out: raise Fail("the Manager's installer failed:\n" + out)
    vm.sh("rm -rf /tmp/Plugin-Manager-G2-*")


def open_manager(vm, secs=420):
    """From wherever MPC is, get to the Manager's screen: dismiss what is in the way, make a plugin track, pick the Manager, open it.
    A screen is recognised by a few pixels and by any of its texts (OCR misses some labels), and what to tap is at a fixed place:
    these screens are laid out the same on every firmware that has them. The plugin list is the exception, and is read."""
    end = time.time() + secs; step = 0
    added = picked = by_mfr = False
    near = lambda c, want, tol=40: all(abs(c[i] - want[i]) <= tol for i in range(3))
    while time.time() < end:
        step += 1
        ws = vm.words(save=os.path.join(vm.dir, "nav-%02d.png" % step))
        img = vm.last_shot
        has = lambda *ts: any(vm.find(t, fresh=False) for t in ts)
        rows = lambda t: [h for h in vm.find(t, fresh=False) if 80 < h[1] < 730]
        folder, corner = img.getpixel((60, 175)), img.getpixel((100, 100))
        def did(what): log("  nav %02d: %s" % (step, what))
        if has("UPDATE ALL", "OPEN MPC - PLUGIN MANAGER", "LOCATIONS") and vm.exists("/tmp/openmpc-pm/device.txt"):
            return
        if near(corner, (0xd2, 0x0f, 0x44)) or has("Welcome to MPC", "Welcome to"): did("welcome"); vm.tap(640, 640, after=3)
        elif has("GOT IT", "Learn how", "get this info at any"): did("learn"); vm.tap(960, 640, after=3)
        elif has("LETS GO", "to the internet"): did("connect"); vm.tap(546, 760); vm.tap(640, 640, after=5)
        elif has("SELECT") and has("CLOSE") and (has("MANUFACTURER") or [h for h in vm.find("PLUGIN", fresh=False) if h[1] < 70]):
            # the plugin list. Its TYPE and MANUFACTURER buttons are not always read (dark on light when lit): the title is.
            if rows("Plugin Manager"):
                h = rows("Plugin Manager")[0]; did("pick Plugin Manager"); vm.tap(h[0], h[1]); vm.tap(958, 770, after=8); picked = True
            elif rows("OpenMPC"): did("open OpenMPC"); vm.tap(151, rows("OpenMPC")[0][1])
            elif rows("External Plugins"): did("open External Plugins"); vm.tap(151, rows("External Plugins")[0][1])   # MPC OS 3.10.1+
            elif rows("VST"): did("open VST"); vm.tap(151, rows("VST")[0][1])
            elif not by_mfr: did("list by manufacturer"); vm.tap(318, 770); by_mfr = True
            else: raise Fail("the plugin list shows neither OpenMPC nor Plugin Manager: MPC did not accept the plugin (captures in %s)" % vm.dir)
        elif has("ADVANCED") and has("CLOSE"): did("add track: Plugin"); vm.tap(560, 375, after=4); added = True
        elif has("NEW PROJECT", "USER TEMPLATE", "Select Project"): did("new project"); vm.tap(105, 770, after=15)
        elif near(folder, (0xba, 0x8b, 0x01)):                                      # the main screen, nothing over it
            if picked:                          # once: a second tap at this place lands on a row of the Manager's own list
                did("open the plugin's screen"); vm.tap(1042, 478, after=4)
                for _ in range(8):
                    if vm.find("LOCATIONS") or vm.find("UPDATE ALL", fresh=False): break
                    time.sleep(3)
            elif added: did("plugin field"); vm.tap(450, 485, after=0.15); vm.tap(450, 485, after=4)
            else: did("new track"); vm.tap(105, 770, after=3)
        elif near(folder, (0x26, 0x1d, 0x00), 16): did("dialog: left button"); vm.tap(532, 770, after=3)
        else:
            did("nothing known on screen (%s), waiting" % " ".join(w[0] for w in ws)[:80]); time.sleep(4)
    raise Fail("never reached the Manager's screen (captures in %s)" % vm.dir)


def prepare(instance, stage="work", fx=None, minfo=None):
    """From the machine snapshot (machine.py; made first if it is missing or out of date), install the Manager build `stage` and leave
    a "ready" snapshot in that stage. Returns what a test needs to know about the machine."""
    own = fx is None
    if own: fx = emu.Fixture(stage)
    fx.catalog()
    minfo = minfo or machine.ensure(instance)
    machine.copy_to(instance, stage)
    vm = emu.VM(instance, stage)
    t0 = time.time()
    try:
        vm.start(machine.SNAPSHOT)
        synths, ext = minfo["synths"], minfo["external_plugins"]
        if ext:
            install_manager(vm, None)               # no -t: the installer picks the folder this MPC OS loads from
            if not vm.exists("%s/%s/plugin.json" % (synths, MANAGER_DIR)):
                raise Fail("the installer did not put the Manager in %s:\n%s" % (synths, vm.out("ls -la '%s' 2>&1" % synths)))
        else:
            install_manager(vm, synths)
            add_location(vm, synths)
        vm.sh("rm -rf /tmp/openmpc-pm; systemctl start acvs")
        open_manager(vm)
        try: st = vm.state(60, lambda s: s.get("loaded") == 1 and s.get("online") == 1, "catalog loaded")
        except Fail:
            log("manager.log:\n" + vm.read("/tmp/openmpc-pm/manager.log")[-3000:]); log("fixture requests:\n" + fx.requests()[-1500:])
            raise
        if st["version"] != VERSION_A: raise Fail("the running Manager says version %s" % st["version"])
        vm.shot(os.path.join(vm.dir, "ready.png"))
        vm.savevm("ready")
        info = {k: minfo[k] for k in ("arch", "synths", "external_plugins", "boot_seconds", "mpc_seconds", "firmware")}
        info.update(machine=minfo["id"], prepare_seconds=int(time.time() - t0))
        log("%s: ready in %ds from the machine snapshot" % (instance, time.time() - t0))
        return info
    finally:
        vm.stop()
        if own: fx.stop()


if __name__ == "__main__":
    import sys
    try: print(prepare(sys.argv[1], *(sys.argv[2:3])))
    finally: emu.shutdown()
