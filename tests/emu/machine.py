"""The machine every test of one instance starts from, before any Plugin Manager is installed: MPC OS booted from the instance's own
disks, the internal drive set up as the model has it, and on MPC OS 3.10.1+ the two Enhancements switches on. Saved as the snapshot
"machine" in state/<instance>/machine/; prepare.py starts each build's "ready" snapshot from it.

The snapshot depends only on the instance (its firmware), QEngine, QEMU, this file and the QEMU shim (machine_id()), not on the
Manager: so it can be made once and kept, e.g. as a prebuilt image in CI.
What is done here that a real device already has:
  * somewhere to install (see storage()).
  * SynthContentLocations in MPC.settings, which a never-used MPC has not written yet (add_location()).
"""
import hashlib, json, os, shutil, subprocess, time
import emu
from emu import Fail, log

STAGE, SNAPSHOT = "machine", "machine"
SETTINGS = "/data/Settings/MPC/MPC.settings"
HERE = os.path.dirname(os.path.abspath(__file__))


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


def add_location(vm, synths):
    if "SynthContentLocations" in vm.read(SETTINGS): return
    block = ('  <VALUE name="SynthContentLocations">\\n    <SynthContentLocations>\\n      <Location>%s</Location>\\n'
             '    </SynthContentLocations>\\n  </VALUE>\\n</PROPERTIES>' % synths)
    vm.sh("sed -i 's|</PROPERTIES>|%s|' %s" % (block, SETTINGS))


def machine_id(instance):
    """What the machine snapshot is made of: the firmware image and the product code it is booted as, QEngine's commit, QEMU's version
    (a snapshot is QEMU's own format), this file and the shim. PM_FIRMWARE_SHA256 and PM_PRODUCT_CODE give the first two without the
    instance (CI names a prebuilt image by this before the instance exists); otherwise they come from the instance."""
    sha, code = os.environ.get("PM_FIRMWARE_SHA256", ""), os.environ.get("PM_PRODUCT_CODE", "")
    if not (sha and code):
        env = emu.instance_env(instance)
        img = env.get("FIRMWARE_IMG", "")
        code = code or env.get("PRODUCT_CODE", "")
        sha = sha or "%s:%s" % (os.path.basename(img), os.path.getsize(img) if os.path.exists(img) else "?")
    ref = os.environ.get("PM_QENGINE_REF") or subprocess.run(["git", "-c", "safe.directory=*", "-C", emu.QENGINE, "rev-parse", "HEAD"],
                                                              capture_output=True, text=True).stdout.strip()
    qemu = subprocess.run(["qemu-system-aarch64", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    h = hashlib.sha256()
    for part in (sha, code, ref, qemu):
        h.update(part.encode() + b"\0")
    import sample
    for f in ("machine.py", "shim/qemu-system-aarch64") + (("sample.py",) if sample.is_sample_code(code) else ()):
        h.update(open(os.path.join(HERE, f), "rb").read())
    return h.hexdigest()[:16]


def current(instance):
    """The machine snapshot's info if it exists and is current, else None."""
    try: info = json.load(open(os.path.join(emu.STATE, instance, STAGE, "machine.json")))
    except (OSError, ValueError): return None
    return info if info.get("id") == machine_id(instance) else None


def make(instance):
    """Boot the instance from its own disks, set the machine up and save the snapshot "machine" (MPC stopped). Returns its info."""
    vm = emu.VM(instance, STAGE)
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
        firmware = vm.out("cat /tmp/com.akaipro.mpc.version 2>/dev/null || true").strip()
        vm.sh("systemctl stop acvs"); time.sleep(2)
        ext = external_plugins(vm)
        synths = storage(vm, ext)
        if ext:                                     # MPC OS 3.10.1+: plugins live in "<drive>/VST2 Plugins", behind two switches
            add_location(vm, synths)
            synths = synths.rsplit("/", 1)[0] + "/VST2 Plugins"
            vm.sh("mkdir -p '%s'" % synths)
            enable_external_plugins(vm)
        vm.savevm(SNAPSHOT)
        info = {"id": machine_id(instance), "arch": arch(vm), "synths": synths, "external_plugins": ext, "boot_seconds": int(boot),
                "mpc_seconds": int(mpc_up), "machine_seconds": int(time.time() - t0), "firmware": firmware}
        json.dump(info, open(os.path.join(vm.dir, "machine.json"), "w"))
        log("%s: machine ready in %ds (ssh after %ds, MPC after %ds)" % (instance, time.time() - t0, boot, mpc_up))
        return info
    finally:
        vm.stop()


def ensure(instance, force=False):
    info = None if force else current(instance)
    if info: return info
    log("%s: making the machine snapshot" % instance)
    import sample
    if sample.is_sample(instance): return sample.make(instance, machine_id(instance))   # MPC Sample: a machine of its own kind
    return make(instance)


def copy_to(instance, stage):
    """A stage starts as a copy of the machine's disks (overlays with the snapshot in them). Copied, not layered: QEMU's loadvm finds
    a snapshot only in the image it opens."""
    src = os.path.join(emu.STATE, instance, STAGE); dst = os.path.join(emu.STATE, instance, stage)
    shutil.rmtree(dst, ignore_errors=True); os.makedirs(dst)
    for f in os.listdir(src):
        if f.endswith(".qcow2"):
            subprocess.run(["cp", "--reflink=auto", "--sparse=always", os.path.join(src, f), os.path.join(dst, f)], check=True)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["--id"]: print(machine_id(sys.argv[2])); sys.exit(0)
    try: print(ensure(sys.argv[1], force="--force" in sys.argv))
    finally: emu.shutdown()
