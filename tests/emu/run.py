#!/usr/bin/env python3
"""Run the emulator scenarios on one instance (inside the pm-emu-host container; tests/emu/run.sh is the one command on the host).

  run.py INSTANCE [--label BUILD] [-k NAME]... [--prepare] [--list]

Every scenario starts from the same snapshot ("ready": prepare.py made it once per instance and build) on a fresh QEMU, so none sees
what another did. Results go to tests/emu/results/<instance>/<build>/<scenario>/ (what was on screen, the device's logs) and a line
per scenario to results/<instance>/<build>/summary.json."""
import argparse, hashlib, json, os, shutil, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emu, prepare
from emu import Fail, log

HERE = os.path.dirname(os.path.abspath(__file__))
PM = "/tmp/openmpc-pm"


class T:
    """What a scenario works with: the machine, the fixture, and the steps a person at the device would take."""
    def __init__(self, vm, fx, info, out):
        self.vm, self.fx, self.out = vm, fx, out
        self.arch, self.synths = info["arch"], info["synths"]
        self.gen = 2 if self.arch == "aarch64" else 1
        self.settings = prepare.SETTINGS
        self.problems = []
        self.nshot = 0

    # -- checks: a failed one is recorded and the scenario goes on, so one run shows everything that is wrong ------------------------
    def check(self, ok, what):
        if not ok:
            self.problems.append(what); log("    FAIL: " + what)
        return bool(ok)

    def eq(self, got, want, what):
        return self.check(got == want, "%s: got %r, want %r" % (what, got, want))

    def has(self, hay, needle, what):
        return self.check(needle in hay, "%s: %r not in %r" % (what, needle, hay[-600:] if len(hay) > 600 else hay))

    def hasnt(self, hay, needle, what):
        return self.check(needle not in hay, "%s: %r is in it" % (what, needle))

    def shot(self, name):
        self.nshot += 1
        p = os.path.join(self.out, "%02d-%s.png" % (self.nshot, name))
        self.vm.shot(p)
        return p

    def screen_shows(self, *texts, name="screen"):
        """The screen shows each text (OCR of a capture that is kept)."""
        import ocr
        ws = ocr.words(self.shot(name))
        for t in texts:
            self.check(ocr.find(None, t, ws), "the screen shows %r (%s)" % (t, name))

    # -- the Manager -------------------------------------------------------------------------------------------------------------
    def state(self, **kw):
        return self.vm.state(**kw)

    def plugin(self, pid, st=None):
        st = st or self.state()
        for p in st.get("plugins", []):
            if p["id"] == pid: return p
        return {}

    def refresh(self):
        """Fetch the catalog again and wait for it."""
        self.vm.api("refresh", post=True)
        time.sleep(1)
        return self.state(secs=60, until=lambda s: s.get("loaded") == 1 and not s.get("busy"), why="catalog reloaded")

    def queue(self, pid, do="install"):
        self.vm.api("queue?id=%s&do=%s" % (pid, do), post=True)

    def prepare(self, secs=240):
        """The first APPLY: download and unpack. Returns the state once it is ready (ready=1) or has failed (failed=1)."""
        self.vm.api("apply", post=True)
        time.sleep(1)
        return self.state(secs=secs, until=lambda s: not s.get("busy") and (s.get("ready") or s.get("failed")), why="APPLY prepared")

    def launch(self):
        """The second APPLY (the restart prompt's GO): MPC stops, the changes are made, MPC starts again."""
        self.vm.sh("rm -f %s/result" % PM)
        self.vm.api("apply", post=True, check=False)

    def wait_done(self, secs=420):
        """Wait for the apply script to finish and MPC to be running again; returns (result lines, apply.log)."""
        end = time.time() + secs
        while time.time() < end:
            lg = self.vm.read(PM + "/apply.log")
            if "== done" in lg and self.vm.sh("pidof MPC", check=False)[0] == 0: break
            time.sleep(3)
        else:
            self.check(False, "the apply never finished: apply.log ends %r" % self.vm.read(PM + "/apply.log")[-400:])
        self.keep("apply.log", "result")
        return self.results(), self.vm.read(PM + "/apply.log")

    def apply(self, secs=420):
        """Both APPLYs. Returns (result lines as {id: (status, detail)}, apply.log); a failed prepare returns ({}, the status line)."""
        st = self.prepare()
        if not st.get("ready"):
            return {}, st.get("status", "")
        self.launch()
        return self.wait_done(secs)

    def results(self):
        out = {}
        for line in self.vm.read(PM + "/result").splitlines():
            f = line.split("|", 2)
            if len(f) == 3: out[f[0]] = (f[1], f[2])
        return out

    def reopen(self):
        """After MPC restarted: a new project with the Manager on a track again."""
        prepare.open_manager(self.vm)
        return self.state(secs=90, until=lambda s: s.get("loaded") == 1 and not s.get("busy"), why="the Manager loaded its catalog")

    def keep(self, *names):
        """Copy the device's log files into the results."""
        for n in names:
            txt = self.vm.read("%s/%s" % (PM, n))
            if txt:
                k = len([f for f in os.listdir(self.out) if f.endswith(n)])
                open(os.path.join(self.out, "%d-%s" % (k + 1, n)), "w").write(txt)

    def settings_text(self):
        return self.vm.read(self.settings)

    def settings_ok(self, what="MPC.settings"):
        """MPC.settings is whole: well-formed XML with its PROPERTIES root."""
        rc, out = self.vm.sh("tail -c 14 %s; echo; wc -c < %s" % (self.settings, self.settings), check=False)
        txt = self.settings_text()
        ok = txt.lstrip().startswith("<?xml") and txt.rstrip().endswith("</PROPERTIES>")
        if ok:
            import xml.etree.ElementTree as ET
            try: ET.fromstring(txt.encode())
            except ET.ParseError as e: ok = False; out = str(e)
        return self.check(ok, "%s is whole (%s)" % (what, out.strip()[-80:]))


SCENARIOS = []


def scenario(f):
    SCENARIOS.append(f)
    return f


def run_one(instance, label, sc, info, fx):
    out = os.path.join(HERE, "results", instance, label, sc.__name__)
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
    vm = emu.VM(instance, label)
    t0 = time.time(); t = None
    try:
        fx.catalog()
        vm.start("ready")
        t = T(vm, fx, info, out)
        sc(t)
        problems = t.problems
    except Fail as e:
        problems = (t.problems if t else []) + ["stopped: %s" % e]
    except Exception:
        problems = (t.problems if t else []) + ["harness error: %s" % traceback.format_exc()[-800:]]
    try:
        if vm.proc and vm.proc.poll() is None:
            vm.shot(os.path.join(out, "99-end.png"))
            for n in ("device.txt", "manager.log", "apply.log", "result"):
                txt = vm.read("%s/%s" % (PM, n))
                if txt: open(os.path.join(out, "end-" + n), "w").write(txt)
    except Exception:
        pass
    vm.stop()
    secs = int(time.time() - t0)
    log("%s %-28s %s (%ds)%s" % (instance, sc.__name__, "ok" if not problems else "FAILED", secs, "".join("\n      - " + p for p in problems)))
    return {"scenario": sc.__name__, "ok": not problems, "seconds": secs, "problems": problems, "doc": (sc.__doc__ or "").strip().split("\n")[0]}


def build_id(label):
    h = hashlib.sha256()
    d = os.path.join(HERE, "build", "fx", label)
    for f in sorted(os.listdir(d)):
        if f.startswith("Plugin-Manager-G2-" + prepare.VERSION_A): h.update(open(os.path.join(d, f), "rb").read())
    h.update(open(os.path.join(HERE, "prepare.py"), "rb").read())
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instance")
    ap.add_argument("--label", default="work", help="which build of the Manager (tests/emu/build.sh --label); default the working tree's")
    ap.add_argument("-k", action="append", default=[], help="run only scenarios whose name contains this (may repeat)")
    ap.add_argument("--prepare", action="store_true", help="make the ready snapshot again even if it is current")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    import run, scenarios   # noqa: scenarios registers itself with the module named run, which is not this one when run as a script
    SCENARIOS[:] = run.SCENARIOS
    todo = [s for s in SCENARIOS if not a.k or any(k in s.__name__ for k in a.k)]
    if a.list:
        for s in todo: print("%-28s %s" % (s.__name__, (s.__doc__ or "").strip().split("\n")[0]))
        return 0
    fx = emu.Fixture(a.label)
    rc = 1
    try:
        stage = os.path.join(emu.STATE, a.instance, a.label)
        bid = build_id(a.label)
        info = {}
        try: info = json.load(open(os.path.join(stage, "ready.json")))
        except (OSError, ValueError): pass
        if a.prepare or info.get("build") != bid:
            log("%s: preparing (build %s)" % (a.instance, a.label))
            info = prepare.prepare(a.instance, a.label, fx)
            info["build"] = bid
            json.dump(info, open(os.path.join(stage, "ready.json"), "w"))
        res = [run_one(a.instance, a.label, s, info, fx) for s in todo]
        outd = os.path.join(HERE, "results", a.instance, a.label)
        os.makedirs(outd, exist_ok=True)
        prev = {}
        try: prev = {r["scenario"]: r for r in json.load(open(os.path.join(outd, "summary.json")))["scenarios"]}
        except (OSError, ValueError, KeyError): pass
        for r in res: prev[r["scenario"]] = r
        json.dump({"instance": a.instance, "build": a.label, "prepare": info, "scenarios": [prev[s.__name__] for s in SCENARIOS if s.__name__ in prev]},
                  open(os.path.join(outd, "summary.json"), "w"), indent=1)
        bad = [r for r in res if not r["ok"]]
        log("%s: %d scenarios, %d failed, %ds" % (a.instance, len(res), len(bad), sum(r["seconds"] for r in res)))
        rc = 1 if bad else 0
    finally:
        fx.stop(); emu.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
