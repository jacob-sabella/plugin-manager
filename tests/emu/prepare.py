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


def track_panel(img):
    """The main screen's selected track, read from its panel: ("plugin", "manager" | "none" | ""), ("other", ""), or (None, "")
    when the panel could not be read."""
    import ocr
    t = ocr.norm(ocr.line(img, (280, 350, 1100, 510)))
    if "arrangement" not in t: return None, ""
    head, _, field = t.partition("arrangement")
    if "plugin" not in head: return "other", ""
    return "plugin", "manager" if "pluginmanager" in field else "none" if "none" in field else ""


def open_manager(vm, secs=420, keep=None):
    """From wherever MPC is, get to the Manager's screen: dismiss what is in the way, make a plugin track, pick the Manager, open it.
    A screen is recognised by a few pixels and by any of its texts (OCR misses some labels), and what to tap is at a fixed place:
    these screens are laid out the same on every firmware that has them. The plugin list is the exception, and is read.
    Each step looks at the screen only once it has stopped changing, and after a tap waits for the screen to change: on a slow
    host a tap made while the guest is still drawing lands on the wrong screen. A step that did not take is simply seen again.
    A screen nothing here knows that stays put for a minute means a tap went astray: MPC is restarted and it starts over (twice at
    most). `keep`: a folder the captures go to if the Manager's screen is never reached."""
    import glob, shutil
    for f in glob.glob(os.path.join(vm.dir, "nav-*.png")): os.remove(f)
    end = time.time() + emu.slow(secs); step = 0
    added = picked = by_mfr = False; opens = restarts = 0
    stuck_since = stuck_img = None
    near = lambda c, want, tol=40: all(abs(c[i] - want[i]) <= tol for i in range(3))
    try:
        while time.time() < end:
            step += 1
            img = vm.settle()
            ws = vm.words(save=os.path.join(vm.dir, "nav-%02d.png" % step), img=img)
            has = lambda *ts: any(vm.find(t, fresh=False) for t in ts)
            rows = lambda t: [h for h in vm.find(t, fresh=False) if 80 < h[1] < 730]
            folder, corner = img.getpixel((60, 175)), img.getpixel((100, 100))
            def did(what): log("  nav %02d: %s" % (step, what))
            def go(x, y, change=10):
                if vm.press(x, y, before=img, change=change) is None: did("the screen did not change; looking again")
            manager = has("UPDATE ALL", "OPEN MPC - PLUGIN MANAGER", "LOCATIONS")
            if manager and vm.exists("/tmp/openmpc-pm/device.txt"):
                return
            known = True
            if has("PROGRESS", "Loading Track", "Loading"):     # a project loading: a tap now cancels it ("OPERATION CANCELLED")
                did("loading, waiting"); time.sleep(2)
            elif not vm.settled: did("the screen is still moving, waiting")
            elif near(corner, (0xd2, 0x0f, 0x44)) or has("Welcome to MPC", "Welcome to"): did("welcome"); go(640, 640)
            elif has("GOT IT", "Learn how", "get this info at any"): did("learn"); go(960, 640)
            elif has("LETS GO", "to the internet"): did("connect"); vm.tap(546, 760, after=0.5); go(640, 640, change=15)
            elif has("SELECT") and has("CLOSE") and (has("MANUFACTURER") or [h for h in vm.find("PLUGIN", fresh=False) if h[1] < 70]):
                # the plugin list. Its TYPE and MANUFACTURER buttons are not always read (dark on light when lit): the title is.
                if rows("Plugin Manager"):
                    h = rows("Plugin Manager")[0]; did("pick Plugin Manager")
                    vm.press(h[0], h[1], before=img, change=5)
                    if vm.press(958, 770, change=30) is None: did("SELECT did not change the screen; looking again")
                    picked = True
                elif rows("OpenMPC"): did("open OpenMPC"); go(151, rows("OpenMPC")[0][1], 5)
                elif rows("External Plugins"): did("open External Plugins"); go(151, rows("External Plugins")[0][1], 5)   # MPC OS 3.10.1+
                elif rows("VST"): did("open VST"); go(151, rows("VST")[0][1], 5)
                elif not by_mfr: did("list by manufacturer"); go(318, 770); by_mfr = True
                else: raise Fail("the plugin list shows neither OpenMPC nor Plugin Manager: MPC did not accept the plugin (captures in %s)" % vm.dir)
            elif has("ADVANCED") and has("CLOSE"): did("add track: Plugin"); go(560, 375); added = True
            elif has("NEW PROJECT", "USER TEMPLATE", "Select Project"): did("new project"); go(105, 770, change=40)
            elif near(folder, (0xba, 0x8b, 0x01)):                                      # the main screen, nothing over it
                # which track is selected, from its panel; when that cannot be read, from what was done
                kind, field = track_panel(img)
                if kind == "plugin" and field == "manager" or kind is None and picked:
                    if opens >= 3: did("the plugin's screen does not open; waiting"); time.sleep(5); continue
                    did("open the plugin's screen"); opens += 1; go(1042, 478, change=40)
                elif kind == "plugin" or kind is None and added:
                    did("plugin field")
                    if vm.press(450, 485, before=img, change=15, double=True) is None: did("the screen did not change; looking again")
                else: did("new track"); go(105, 770)
            elif near(folder, (0x26, 0x1d, 0x00), 16):
                # a dialog over the main screen. A progress dialog (a project or a plugin loading) has CANCEL, or no button, where
                # the others have OK: tapping it there cancels the load, and with no button the tap reaches the screen beneath
                import ocr
                title, button = ocr.norm(ocr.line(img, (260, 180, 1020, 260))), ocr.norm(ocr.line(img, (380, 745, 700, 795)))
                if "rogress" in title or "loading" in title or "cancel" in button: did("progress, waiting"); time.sleep(2)
                elif button: did("dialog: %s" % button); go(532, 770)
                else: known = False
            else: known = False
            if known:
                stuck_since = stuck_img = None; continue
            text = " ".join(w[0] for w in ws)
            did("nothing known on screen (%s), waiting" % text[:80])
            waiting = manager or not text.strip() or "Display output" in text or near(folder, (0xf7, 0xf6, 0xf3), 8)   # starting, or the Manager drawing
            if waiting or stuck_img is None or emu.changed(stuck_img, img):
                stuck_since, stuck_img = (None, None) if waiting else (time.time(), img)
            elif time.time() - stuck_since > emu.slow(60):
                if restarts >= 2: raise Fail("lost on a screen nothing here knows, after restarting MPC twice (captures in %s)" % vm.dir)
                restarts += 1; did("lost: restarting MPC and starting over")
                vm.sh("systemctl restart acvs", check=False, timeout=int(emu.slow(120)))
                until = time.time() + emu.slow(120)
                while vm.sh("pidof MPC", check=False)[0] != 0 and time.time() < until: time.sleep(3)
                added = picked = by_mfr = False; opens = 0; stuck_since = stuck_img = None
            time.sleep(2)
        raise Fail("never reached the Manager's screen (captures in %s)" % vm.dir)
    except Fail:
        if keep:
            os.makedirs(os.path.join(keep, "nav"), exist_ok=True)
            for f in glob.glob(os.path.join(vm.dir, "nav-*.png")): shutil.copy(f, os.path.join(keep, "nav"))
        raise


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
