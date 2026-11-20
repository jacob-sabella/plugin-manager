"""Bring a freshly built QEngine instance to the state every test starts from, and snapshot it as "ready":
MPC running a project with the Plugin Manager (the test build, tests/emu/build.sh) loaded on a track and its screen open.

What is done by hand here that a real device already has:
  * somewhere to install. Gen1: /media/az01-internal (the instance's internal storage). Gen2: QEngine does not emulate the NVMe drive,
    so an ext4 image is mounted where MPC expects it, at /nvme, by a unit that survives a reboot (the power-cut test reboots).
  * SynthContentLocations in MPC.settings, which a never-used MPC has not written yet.
Everything on screen is found by its text (ocr.py), since the first-run screens and the plugin list differ between firmwares."""
import os, time
import emu
from emu import Fail, log

VERSION_A, VERSION_B = "9.0.0", "9.0.1"
MANAGER_DIR = "OpenMPC - VST - Plugin Manager"
SETTINGS = "/data/Settings/MPC/MPC.settings"


def arch(vm):
    return "aarch64" if "aarch64" in vm.out("uname -m") else "armv7"


# QEngine sets its own LD_PRELOAD (its panel and audio shims) in a drop-in called override.conf, which on the Gen2 image sits on a
# read-only root. A drop-in of that name sorts after the addin installer's own (90-mpc-addins.conf) and so wins: MPC then starts
# without the addin. A device's firmware keeps its list in the unit itself, so the emulator's drop-in is renamed to sort first.
QENGINE_DROPIN = ("d=/etc/systemd/system/acvs.service.d; if [ -f $d/override.conf ]; then mv $d/override.conf $d/10-qengine.conf "
                  "&& systemctl daemon-reload; fi")


USER_DISK = "/dev/disk/by-id/virtio-pmuser"       # shim/qemu-system-aarch64 adds it: a real disk, since MPC is told to ignore loop devices


# A unit says in its device tree which internal drive it has (chosen/inmusic,nvme-fitted on Gen2, inmusic,acvs-emmc-content on a
# Gen1 that keeps its content on the eMMC, as an MPC Key 37 does: /storage, also /media/acvs-content), and MPC asks: without the flag MPC OS 3.10.1 has no internal drive (it never looks in /nvme/VST2 Plugins
# when it starts, and Splice reports "Download directory could not be found"), though its Developer panel still finds the plugins
# there. QEngine serves the product code and serial only, so the flag is added to what the kernel shows, before MPC starts and again
# after every reboot.
FITTED = """[Unit]
Description=The emulator's stand-in for the device tree flag of a fitted internal drive
DefaultDependencies=no
Before=sysinit.target local-fs-pre.target local-fs.target acvs.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'c=/sys/firmware/devicetree/base/chosen; [ -e "$c/inmusic,%s" ] && exit 0; mkdir -p /run/pm-chosen && cp $c/* /run/pm-chosen/ 2>/dev/null; : > "/run/pm-chosen/inmusic,%s"; mount --bind /run/pm-chosen $c'
"""


def internal_drive(vm, where, flag):
    """The harness's own disk, formatted and mounted where this model keeps its internal drive, with the device tree flag for it; by
    units, so that it is there again after a reboot (the power-cut test reboots).
    The units go into /etc on the root filesystem itself. On Gen1, /etc is an overlay whose upper layer is on /data and is mounted
    during boot, after systemd has read its units: a unit written through it is not there at the next boot (and on Gen2 the
    stock nvme.mount, which waits for the flag, wins). So the root is reached through a bind mount, under the overlay."""
    unit = where.strip("/").replace("/", "-") + ".mount"
    mnt = ("[Unit]\nDescription=The emulator's stand-in for the internal user drive\nRequires=pm-fitted.service\n"
           "After=pm-fitted.service\nBefore=acvs.service\n\n[Mount]\nWhat=%s\nWhere=%s\nType=ext4\n" % (USER_DISK, where))
    vm.sh("mke2fs -q -t ext4 -F %s" % USER_DISK)
    vm.sh("mount -o remount,rw /; mkdir -p /run/pm-root && mount --bind / /run/pm-root")   # only this VM's overlay of the image is written
    vm.write("/run/pm-root/etc/systemd/system/" + unit, mnt)
    vm.write("/run/pm-root/etc/systemd/system/pm-fitted.service", FITTED % (flag, flag))
    vm.sh("cd /run/pm-root/etc/systemd/system && mkdir -p local-fs.target.wants sysinit.target.wants && "
          "ln -sf ../%s local-fs.target.wants/%s && ln -sf ../pm-fitted.service sysinit.target.wants/pm-fitted.service" % (unit, unit))
    vm.sh(QENGINE_DROPIN)
    vm.sh("umount /run/pm-root; mount -o remount,ro / 2>/dev/null; true")
    vm.sh("systemctl daemon-reload && systemctl start pm-fitted.service %s && mkdir -p %s/Synths '%s/MPC Documents'" % (unit, where, where))
    return where + "/Synths"


def storage(vm, ext):
    """Make the install location and return its Synths folder. `ext`: this firmware loads plugins from "<drive>/VST2 Plugins"."""
    if arch(vm) == "aarch64": return internal_drive(vm, "/nvme", "nvme-fitted")
    # Gen1. MPC OS 3.10.1 looks for "VST2 Plugins" on the content drive and on attached drives, not on the data partition
    # (/media/az01-internal), which is where older firmware is content to load from once it is a content location. Laid out as a
    # real MPC Key 37 on 3.10.1: an ext4 eMMC partition at /storage (acva-emmc-content-generator mounts it when the device tree
    # has chosen/inmusic,acvs-emmc-content; media-acvs-content.mount binds it to /media/acvs-content), plugins in
    # /storage/VST2 Plugins. QEngine's eMMC image has no such partition, so the harness's disk stands in for it; the unit written
    # to /etc takes the place of the generated one.
    if ext: return internal_drive(vm, "/storage", "acvs-emmc-content")
    vm.sh("mkdir -p /media/az01-internal/Synths; " + QENGINE_DROPIN)
    return "/media/az01-internal/Synths"


def external_plugins(vm):
    """Does this firmware load plugins from "<drive>/VST2 Plugins" (MPC OS 3.10.1 and later)? Asked of the MPC binary, which has the
    name of the switch, so that a firmware nobody has listed here is handled by what it can do and not by its version."""
    return vm.out("grep -c -a 'External Plugin Support' /usr/bin/MPC 2>/dev/null || true").strip() not in ("", "0")


def enable_external_plugins(vm):
    """Preferences, Enhancements: Enable Enhancements and External Plugin Support, which a user turns on by hand. Without them the
    plugin list offers only MPC's own plugins. MPC must be stopped: it writes its settings when it exits."""
    for key in ("Enhancements.Enabled", "Enhancements.VST2Support"):
        vm.sh("if grep -q 'name=\"%s\"' %s; then sed -i 's|\\(name=\"%s\" val=\"\\)[^\"]*|\\11|' %s; "
              "else sed -i 's|</PROPERTIES>|  <VALUE name=\"%s\" val=\"1\"/>\\n</PROPERTIES>|' %s; fi" % (key, SETTINGS, key, SETTINGS, key, SETTINGS))
    got = vm.read(SETTINGS)
    for key in ("Enhancements.Enabled", "Enhancements.VST2Support"):
        if 'name="%s" val="1"' % key not in got: raise Fail("could not switch on %s in MPC.settings" % key)


def install_manager(vm, synths, version=VERSION_A):
    a = arch(vm)
    vm.sh("cd /tmp && rm -rf Plugin-Manager-G2-* && wget -q http://%s/Plugin-Manager-G2-%s-mpc-%s.zip && unzip -q -o Plugin-Manager-G2-%s-mpc-%s.zip"
          % (emu.FIXTURE_ADDR, version, a, version, a))
    out = vm.out("sh /tmp/Plugin-Manager-G2-%s/install.sh -y -n %s 2>&1" % (version, "-t '%s'" % synths if synths else ""), timeout=300)
    if "Done." not in out: raise Fail("the Manager's installer failed:\n" + out)
    vm.sh("rm -rf /tmp/Plugin-Manager-G2-*")


def add_location(vm, synths):
    if "SynthContentLocations" in vm.read(SETTINGS): return
    block = ('  <VALUE name="SynthContentLocations">\\n    <SynthContentLocations>\\n      <Location>%s</Location>\\n'
             '    </SynthContentLocations>\\n  </VALUE>\\n</PROPERTIES>' % synths)
    vm.sh("sed -i 's|</PROPERTIES>|%s|' %s" % (block, SETTINGS))


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


def prepare(instance, stage="work", fx=None):
    """Boot the instance from its own disks, install the Manager build `stage` and leave a "ready" snapshot in that stage. Returns what a
    test needs to know about the machine."""
    import shutil
    own = fx is None
    if own: fx = emu.Fixture(stage)
    fx.catalog()
    vm = emu.VM(instance, stage)
    shutil.rmtree(vm.dir, ignore_errors=True)
    t0 = time.time()
    try:
        vm.start()
        boot = time.time() - t0
        end = time.time() + 300
        while not vm.exists(SETTINGS):
            if time.time() > end: raise Fail("MPC never wrote its settings file")
            time.sleep(3)
        mpc_up = time.time() - t0
        time.sleep(10)
        vm.sh("systemctl stop acvs"); time.sleep(2)
        ext = external_plugins(vm)
        synths = storage(vm, ext)
        if ext:                                     # MPC OS 3.10.1+: plugins live in "<drive>/VST2 Plugins", behind two switches
            add_location(vm, synths)
            synths = synths.rsplit("/", 1)[0] + "/VST2 Plugins"
            vm.sh("mkdir -p '%s'" % synths)
            enable_external_plugins(vm)
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
        info = {"arch": arch(vm), "synths": synths, "external_plugins": ext, "boot_seconds": int(boot), "mpc_seconds": int(mpc_up), "prepare_seconds": int(time.time() - t0),
                "firmware": vm.out("cat /tmp/com.akaipro.mpc.version 2>/dev/null || true").strip()}
        log("%s: ready in %ds (ssh after %ds, MPC after %ds)" % (instance, time.time() - t0, boot, mpc_up))
        return info
    finally:
        vm.stop()
        if own: fx.stop()


if __name__ == "__main__":
    import sys
    try: print(prepare(sys.argv[1], *(sys.argv[2:3])))
    finally: emu.shutdown()
