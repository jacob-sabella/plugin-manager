"""The MPC Sample's scenarios: how a plugin gets onto a Sample today, with no Plugin Manager (the Sample has no plugin browser).
Each starts from the machine snapshot (sample.py): MPC Sample on its main screen, an SD card at /media/MPCSD (noexec, as edisksd
mounts it) with a Synths folder, nothing installed. The steps are the ones docs/NOTES.md (mpc-vst-plugins, 2026-11-04 and 11-05)
describes on the device:
  1. the plugin's package installer, with -t <card>/Synths;
  2. the card's Synths folder bind-mounted onto itself and remounted exec, by hand (the card itself stays noexec);
  3. the plugin put into the project's mixer by the RACK addin's apply helper (what its APPLY runs: MPC stopped, the working copy
     of the project edited, MPC started), since only a plugin in the project is loaded, and only when MPC starts.
What the device does that a user must know of (and that is not this harness's to fix) is recorded as a finding, not a failure."""
import os, shlex, time
import emu, sample
from emu import Fail, log, slow
from run import T, sample_scenario

FX = "Emu-FX-1.0.0"                                       # the fixture effect (src/fxprobe.c), as a release zip
RACK = "MPC-Sample-rack-addin-"                          # the RACK addin as build.sh serves it (rack_name(): its version follows)
FOLDER = sample.SYNTHS + "/acme - VST - Emu FX"
SO = FOLDER + "/emu_fx.so"
FXLOG = "/tmp/emu-fx.log"                                 # the effect writes a line here when MPC loads it
RACK_DIR = "/data/mpc-addins/sample-rack"
LOGS = (FXLOG, sample.SETTINGS)


class ST(T):
    """T, for the MPC Sample: its settings file, its process and its service."""
    def __init__(self, vm, fx, info, out):
        super().__init__(vm, fx, info, out)
        self.settings = sample.SETTINGS
        self.njournal = 0

    def journal(self, pid):
        """What one MPC Sample process logged."""
        return self.vm.out("journalctl --no-pager -o cat _PID=%s || true" % pid)

    def keep_journal(self, name):
        self.njournal += 1
        txt = self.vm.out("journalctl --no-pager -o short-monotonic -u %s -u 'pm-*' || true" % sample.SERVICE)
        open(os.path.join(self.out, "journal-%d-%s.txt" % (self.njournal, name)), "w").write(txt)


def unpack(t, name):
    """Download a fixture zip into /tmp in the guest and unpack it; returns the folder."""
    t.vm.fetch(name + "-mpc-aarch64.zip", "/tmp/%s.zip" % name)
    t.vm.sh("cd /tmp && rm -rf %s && unzip -q -o %s.zip && rm -f %s.zip" % (shlex.quote(name), shlex.quote(name), shlex.quote(name)))
    return "/tmp/" + name


def install_fx(t, target=sample.SYNTHS):
    """The effect's own install.sh (-t target, or none). Returns (exit status, output)."""
    d = unpack(t, FX)
    return t.vm.sh("cd %s && sh install.sh -y%s 2>&1" % (d, " -t %s" % shlex.quote(target) if target else ""), check=False,
                   timeout=int(slow(180)))


def installed_fx(t):
    rc, out = install_fx(t)
    if not t.check(rc == 0 and "Done." in out, "the effect's install.sh -t %s: %s" % (sample.SYNTHS, out[-600:])): raise Fail("no install")
    return out


def rack_name(t):
    """The RACK release the fixture serves (one), e.g. MPC-Sample-rack-addin-1.0.1: build.sh's default, or RACK_ZIP's."""
    n = sorted(f[:-len("-mpc-aarch64.zip")] for f in os.listdir(t.fx.src) if f.startswith(RACK) and f.endswith("-mpc-aarch64.zip"))
    if len(n) != 1: raise Fail("the fixture has %d RACK zips: run tests/emu/build.sh" % len(n))
    return n[0]


def install_rack(t):
    """The RACK addin, with its zip's own install.sh. Returns its output."""
    d = unpack(t, rack_name(t))
    rc, out = t.vm.sh("cd %s && sh install.sh -y 2>&1" % d, check=False, timeout=int(slow(180)))
    if not t.check(rc == 0, "the RACK addin's install.sh: %s" % out[-600:]): raise Fail("no RACK addin")
    sample.wait_mpc(t.vm)
    return out


def fxlog(t):
    return t.vm.out("cat %s 2>/dev/null || true" % FXLOG)


def mpc_preload(t):
    pid = sample.wait_mpc(t.vm)
    return t.vm.out("tr '\\0' '\\n' < /proc/%s/environ | grep '^LD_PRELOAD=' || true" % pid).strip()


def in_project(t):
    return int(t.vm.out("zcat %s | grep -c emu_fx.so || true" % sample.PROJECT).strip() or 0)


def put_in_project(t):
    """Add the effect to the main output's inserts as RACK's APPLY does: the queued edit (one line, as the addin writes it) and the
    helper, run by systemd-run as a unit of its own (stopping MPC's service stops everything in its cgroup). MPC is restarted by it.
    Returns the new MPC's pid."""
    old = sample.mpc_pid(t.vm)
    t.vm.write("/tmp/pm-rack.edits", "1\toutput:0\t0\t1\t\tEmu FX\tacme\t456d4658\t%s\n" % SO)
    rc, out = t.vm.sh("systemd-run --quiet --collect --wait --unit pm-rack-apply -- %s/sample-rack-apply /tmp/pm-rack.edits %s %s.service 2>&1"
                      % (RACK_DIR, sample.PROJECT, sample.SERVICE), check=False, timeout=int(slow(180)))
    t.check(rc == 0, "RACK's apply helper ran (%d): %s %s" % (rc, out, t.vm.out("journalctl --no-pager -o cat -u pm-rack-apply | tail -n 8")))
    t.eq(in_project(t), 1, "the project names the effect once")
    t.check(t.vm.exists(sample.PROJECT + ".rack-backup"), "the helper kept the project as it was (.rack-backup)")
    return sample.wait_mpc(t.vm, other_than=old)


def load_attempt(t, pid, secs=90):
    """Wait until MPC (process pid) has tried to load the effect and is done trying; returns its log."""
    end = time.time() + slow(secs); j = ""
    while time.time() < end:
        j = t.journal(pid)
        if "Initialising VST: emu_fx" in j: return j
        if "Attempting to load VST: %s" % SO in j:
            time.sleep(slow(10))            # a load that works says so within a second; one that fails says nothing more
            return t.journal(pid)
        time.sleep(2)
    t.check(False, "MPC (pid %s) never tried to load the effect" % pid)
    return j


def loads(t, pid):
    """Did MPC (pid) load the effect: its log, the effect's own line, the library mapped in MPC."""
    j = load_attempt(t, pid)
    mapped = SO in t.vm.out("cat /proc/%s/maps 2>/dev/null || true" % pid)
    said = "main %s %s" % (sample.PROCESS, pid) in fxlog(t) or "main %s Main %s" % (sample.PROCESS, pid) in fxlog(t)
    return "Creating VST instance: emu_fx" in j, said, mapped


def card_line(t, where):
    return [l for l in t.vm.read("/proc/mounts").splitlines() if l.split(" ")[1:2] == [where]]


def bind_exec(t):
    """The workaround from the device notes: the card's Synths folder bound onto itself and remounted exec."""
    t.vm.sh("mount --bind %s %s && mount -o remount,bind,exec %s" % ((shlex.quote(sample.SYNTHS),) * 3))
    line = card_line(t, sample.SYNTHS)
    t.check(line and "noexec" not in line[-1], "the bind mount of %s is exec: %s" % (sample.SYNTHS, line))
    t.check("noexec" in card_line(t, sample.CARD)[0], "the card itself stays noexec")


def loaded(t):
    """The effect installed, RACK installed, the effect in the project, the card's Synths exec, and MPC restarted with it loaded.
    Returns MPC's pid."""
    installed_fx(t)
    install_rack(t)
    put_in_project(t)
    bind_exec(t)
    pid = sample.restart_mpc(t.vm, settle=0)
    ok = loads(t, pid)
    if not t.eq(ok, (True, True, True), "MPC loaded the effect once Synths is exec (log, the effect's own line, mapped)"): raise Fail("not loaded")
    return pid


# ---- the scenarios -------------------------------------------------------------------------------------------------------------

@sample_scenario
def sample_install(t):
    """A Sample-capable plugin (the aarch64 zip) installed with its own install.sh onto the SD card: files, MPC Sample.settings,
    MPC restarted. Without -t the installer does not find the card."""
    before, pid = t.settings_text(), sample.mpc_pid(t.vm)
    rc, out = install_fx(t, target=None)
    t.check(rc != 0, "install.sh without -t did not report success: %s" % out[-300:])
    t.eq(sample.mpc_pid(t.vm), pid, "MPC was not restarted by the refused install")
    t.eq(t.settings_text(), before, "MPC Sample.settings is unchanged by the refused install")
    if "/sdcard/Synths" in out:
        t.finding("install.sh without -t tries /sdcard/Synths, which is on the Sample's read-only root, and stops (%s); the SD card "
                  "is never tried: on a Sample -t %s is needed" % (out.strip().splitlines()[-1], sample.SYNTHS))

    rc, out = install_fx(t)
    t.check(rc == 0 and "Done." in out, "install.sh -t %s: %s" % (sample.SYNTHS, out[-600:]))
    open(os.path.join(t.out, "install.txt"), "w").write(out)
    t.check(t.vm.exists(SO), "the effect's .so is on the card")
    t.check(t.vm.exists(FOLDER + "/Plugin Skins"), "its skin folder is on the card")
    new = sample.wait_mpc(t.vm)
    t.check(new != pid, "MPC was restarted by the install")
    txt = t.settings_when(lambda x: SO in x)
    t.has(txt, 'name="pluginList-arm-64bit"', "MPC Sample.settings has the 64-bit plugin list")
    t.has(txt, 'file="%s" uid="456d4658"' % SO, "MPC Sample.settings lists the effect on the card")
    t.eq(txt.count(SO), 1, "the effect is listed once")
    t.has(txt, "<Location>%s</Location>" % sample.SYNTHS, "the card's Synths is still a content location")
    t.settings_ok("MPC Sample.settings")
    t.check(t.vm.out("ls '%s'.bak-emu_fx-* 2>/dev/null || true" % sample.SETTINGS).strip(), "a settings backup was kept")
    warn = [l for l in out.splitlines() if "noexec" in l]
    t.check(warn, "install.sh warns that the card is noexec")
    if warn and "/sdcard/Synths" in warn[0]:
        t.finding("the noexec warning's advice is for other MPCs: %r. On a Sample /sdcard is read-only; what works is a bind mount of "
                  "the card's Synths folder remounted exec" % warn[0].split(". ", 1)[-1])
    if "plugin browser" in out:
        t.finding("install.sh ends with %r: the Sample has no plugin browser; a plugin loads only from a project's inserts"
                  % [l for l in out.splitlines() if "plugin browser" in l][0])
    t.vm.settle(); t.shot("after-install")


@sample_scenario
def sample_plugin_loads(t):
    """The installed effect loads in MPC Sample once it is in the project (RACK's apply helper) and the card's Synths folder is bound
    exec. Before the bind mount MPC tries and fails without a word, as on the device."""
    installed_fx(t)
    pre = mpc_preload(t)
    out = install_rack(t)
    dropin = "/run/systemd/system/%s.service.d/90-mpc-addins.conf" % sample.SERVICE
    t.check(t.vm.exists(dropin), "the addin installer put its drop-in in /run (the root cannot be remounted): %s" % out[-400:])
    t.check(t.vm.exists(RACK_DIR + "/sample-rack-apply") and t.vm.sh("[ -x %s/sample-rack-apply ]" % RACK_DIR, check=False)[0] == 0,
            "RACK's apply helper is installed executable")
    env = mpc_preload(t)
    t.has(env, RACK_DIR + "/mpc_sample_rack.so", "MPC Sample preloads the RACK addin")
    for lib in [x for x in pre.replace("LD_PRELOAD=", "").split(":") if x]:
        t.has(env, lib, "MPC's LD_PRELOAD keeps what it had")

    pid = put_in_project(t)
    j = load_attempt(t, pid)
    t.has(j, "Attempting to load VST: %s" % SO, "MPC tries to load the effect from the project")
    t.hasnt(j, "Creating VST instance: emu_fx", "MPC's log, with the card noexec")
    t.eq(fxlog(t), "", "the effect never ran while the card is noexec")
    why = t.vm.out("/lib/ld-linux-aarch64.so.1 --list %s 2>&1 || true" % shlex.quote(SO))
    t.has(why, "failed to map segment", "the loader's reason")
    t.finding("with the card noexec MPC logs only \"Attempting to load VST\" for the plugin and nothing after it: no message on "
              "the screen or in the log says it failed")

    bind_exec(t)
    rc, out = install_fx(t)                 # the installer again, over the bind mount: no noexec warning now, and its restart loads it
    t.check(rc == 0 and "Done." in out, "install.sh over the bind mount: %s" % out[-400:])
    t.hasnt(out, "noexec", "install.sh's output once Synths is exec")
    pid = sample.wait_mpc(t.vm, other_than=pid)
    t.eq(loads(t, pid), (True, True, True), "MPC loaded the effect (log, the effect's own line, mapped)")
    t.eq(in_project(t), 1, "the project still names the effect once")
    t.settings_ok("MPC Sample.settings")
    t.keep_journal("loaded")
    sample.main_screen(t.vm); t.shot("loaded")


@sample_scenario
def sample_reboot(t):
    """The effect loading, then a reboot (a clean power-off and power-on): what is still there, and what MPC does with the plugin.
    The bind mount and the addin's drop-in are expected to be gone (docs/NOTES.md 2026-11-05)."""
    loaded(t)
    t.vm.sh("rm -f %s; systemctl poweroff" % FXLOG, check=False, timeout=20)
    end = time.time() + slow(120)
    while t.vm.proc.poll() is None and time.time() < end: time.sleep(1)
    t.check(t.vm.proc.poll() is not None, "the guest powered off")
    t.vm.kill()
    t0 = time.time()
    t.vm.start()
    pid = sample.wait_mpc(t.vm, 300)
    log("%s: rebooted, MPC Sample up after %ds" % (t.vm.instance, time.time() - t0))

    card = card_line(t, sample.CARD)
    t.check(card and "noexec" in card[0], "MPC mounted the card again, noexec: %s" % card)
    t.check(t.vm.exists(SO), "the effect is still on the card")
    t.has(t.settings_text(), 'file="%s"' % SO, "MPC Sample.settings still lists the effect")
    t.eq(in_project(t), 1, "the project still names the effect")
    t.check(t.vm.exists(RACK_DIR + "/mpc_sample_rack.so"), "the RACK addin's files are still in %s" % RACK_DIR)
    bound = card_line(t, sample.SYNTHS)
    dropin = t.vm.exists("/run/systemd/system/%s.service.d/90-mpc-addins.conf" % sample.SERVICE)
    rack = "mpc_sample_rack.so" in mpc_preload(t)
    created, said, mapped = loads(t, pid)
    t.eq((bool(bound), dropin, rack), (False, False, False), "after the reboot: the bind mount, the addin's drop-in, RACK preloaded")
    t.eq((created, said, mapped), (False, False, False), "after the reboot MPC does not load the effect")
    t.finding("after a reboot the exec bind mount of %s is gone, so the plugin still in the project and in the settings is not loaded: "
              "MPC logs \"Attempting to load VST\" and nothing else, and nothing on the screen says so" % sample.SYNTHS)
    t.finding("after a reboot the addin installer's drop-in (in /run, as the root is read-only) is gone: MPC starts without the RACK "
              "addin until its install.sh is run again")
    sample.main_screen(t.vm); t.shot("after-reboot")

    bind_exec(t)                            # the same fix by hand brings it back
    pid = sample.restart_mpc(t.vm, settle=0)
    t.eq(loads(t, pid), (True, True, True), "the effect loads again once Synths is bound exec and MPC restarted")
    t.keep_journal("reboot")


@sample_scenario
def sample_uninstall(t):
    """The loaded effect removed with its own uninstall.sh: its folder and its settings entry go, MPC restarts and keeps running with
    a project that still names it."""
    pid = loaded(t)
    d = "/tmp/" + FX
    rc, out = t.vm.sh("cd %s && sh uninstall.sh -y 2>&1" % d, check=False, timeout=int(slow(180)))
    open(os.path.join(t.out, "uninstall-no-t.txt"), "w").write(out)
    gone = 'file="%s"' % SO not in t.settings_text()
    if t.vm.exists(SO):
        t.finding("uninstall.sh without -t (rc %d) looks in /sdcard/Synths: the folder on the card stays%s" %
                  (rc, ", but the settings entry is removed (it is matched by uid), so the plugin is left unlisted on the card" if gone else ""))
    pid = sample.wait_mpc(t.vm)

    rc, out = t.vm.sh("cd %s && sh uninstall.sh -y -t %s 2>&1" % (d, shlex.quote(sample.SYNTHS)), check=False, timeout=int(slow(180)))
    open(os.path.join(t.out, "uninstall.txt"), "w").write(out)
    t.check(rc == 0 and "Removed" in out, "uninstall.sh -t %s: %s" % (sample.SYNTHS, out[-600:]))
    t.check(not t.vm.exists(FOLDER), "the effect's folder is gone from the card")
    t.eq(t.vm.out("ls -a %s | grep -c -E '\\.new$|\\.old$|\\.keep$' || true" % shlex.quote(sample.SYNTHS)).strip(), "0", "nothing left over in Synths")
    pid = sample.wait_mpc(t.vm, other_than=pid)
    txt = t.settings_when(lambda x: "emu_fx.so" not in x)
    t.hasnt(txt, "emu_fx.so", "MPC Sample.settings after the removal")
    t.has(txt, "<Location>%s</Location>" % sample.SYNTHS, "the card's Synths is still a content location")
    t.settings_ok("MPC Sample.settings")
    time.sleep(slow(20))                    # MPC has opened the project by now; with the plugin unlisted it does not try to load it
    j = t.journal(pid)
    t.hasnt(j, "Creating VST instance: emu_fx", "MPC's log after the removal")
    t.hasnt(fxlog(t), "main %s Main %s" % (sample.PROCESS, pid), "the effect's own log after the removal")
    t.eq(sample.mpc_pid(t.vm), pid, "MPC keeps running with a project that names the removed plugin")
    t.eq(in_project(t), 1, "the project still names the removed effect (the uninstaller does not edit projects)")
    sample.main_screen(t.vm); t.shot("after-uninstall")
    t.keep_journal("uninstall")
