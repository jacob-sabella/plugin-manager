"""Drive a QEngine instance (real MPC firmware in QEMU) for the Plugin Manager's emulator tests.

Runs inside the pm-emu-host container (tests/emu/qe): QEMU, ssh and vncdotool are there. The machine never writes the instance's own
disks: shim/qemu-system-aarch64 puts a qcow2 overlay on each, kept in a stage folder, so a stage can be snapshotted (savevm), copied
and started again from the snapshot (loadvm) in seconds instead of booting MPC OS and tapping through MPC each time.

Commands go over ssh (sshd is started once from the serial console, key in /run, nothing written to the root image); the screen and
touches go over QEMU's VNC; the QEMU monitor does snapshots and power cuts. The guest's network is restricted to the fixture server.
"""
import os, re, shlex, shutil, socket, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
QENGINE = os.environ.get("QENGINE", os.path.join(HERE, "..", "..", "..", "QEngine"))
STATE = os.environ.get("PM_EMU_STATE", os.path.join(HERE, "state"))
SERIAL_PORT, MONITOR_PORT, FIXTURE_PORT = 4575, 4576, 8731
FIXTURE_ADDR = "10.0.2.2:8731"   # the fixture server as the guest sees it: QEMU's NAT maps 10.0.2.2 to this container's loopback


# How much slower than a desktop this host runs the guest. Every wait for the guest is multiplied by it; the screen waits below adapt
# by themselves (they wait for the screen, not for a time). run.sh sets it on CI when it is not given.
SCALE = max(1.0, float(os.environ.get("PM_EMU_TIMESCALE") or 1))


def slow(secs):
    """A timeout for the guest, on this host."""
    return secs * SCALE


def changed(a, b, frac=0.002):
    """Two captures differ by more than a sliver: a fade, a dialog, a new screen or a moved highlight, not a blinking cursor."""
    from PIL import ImageChops
    if a is None or b is None: return True
    ta, tb = (i.convert("L").resize((320, 200)) for i in (a, b))
    n = ImageChops.difference(ta, tb).point(lambda v: 255 if v > 24 else 0).histogram()[255]
    return n > frac * 320 * 200


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Fail(Exception):
    pass


def instance_env(name):
    env = {}
    for line in open(os.path.join(QENGINE, "build", "instances", name, "instance.env")):
        if "=" in line:
            k, v = line.rstrip("\n").split("=", 1)
            env[k] = v.strip("'\"")
    return env


class Fixture:
    """What the guest sees at http://10.0.2.2:8731/: the zips of one build (build/fx/<label>), and a catalog.json a test can change.
    One server per container, on 127.0.0.1:FIXTURE_PORT; every request is logged (requests())."""
    def __init__(self, label="work"):
        import json
        self.src = os.path.join(HERE, "build", "fx", label)
        if not os.path.isfile(os.path.join(self.src, "catalog.full.json")):
            raise Fail("no fixture for build %r: run tests/emu/build.sh%s" % (label, "" if label == "work" else " --ref ... --label " + label))
        self.full = json.load(open(os.path.join(self.src, "catalog.full.json")))
        import tempfile
        self.www = tempfile.mkdtemp(prefix="pm-emu-www-")     # /tmp is the host's, shared by every harness container
        for f in os.listdir(self.src):
            dst = os.path.join(self.www, f)
            if not os.path.lexists(dst): os.symlink(os.path.join(self.src, f), dst)
        self.logf = os.path.join(self.www, ".requests")
        self.catalog()
        self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "serve.py"), str(FIXTURE_PORT)], cwd=self.www,
                                     env=dict(os.environ, PM_FIXTURE_LOG=self.logf), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try: socket.create_connection(("127.0.0.1", FIXTURE_PORT), 1).close(); break
            except OSError: time.sleep(0.1)

    def catalog(self, only=None, **versions):
        """Write catalog.json: every entry (or just the ids in `only`), each with all its versions unless named, e.g.
        catalog(emu_probe=["1.0.0"]) offers the plugin at 1.0.0 only (underscores in a keyword stand for the id's hyphens)."""
        import copy, json
        c = copy.deepcopy(self.full)
        want = {k.replace("_", "-"): v for k, v in versions.items()}
        out = []
        for e in c["plugins"]:
            if only is not None and e["id"] not in only: continue
            if e["id"] in want:
                e["versions"] = [v for v in e["versions"] if v["version"] in want[e["id"]]]
                if not e["versions"]: continue
                e["latest"] = e["versions"][0]["version"]
            out.append(e)
        c["plugins"] = out
        tmp = os.path.join(self.www, "catalog.json.tmp")
        json.dump(c, open(tmp, "w"), indent=1)
        os.replace(tmp, os.path.join(self.www, "catalog.json"))

    def put(self, name, data):
        """Serve `data` (bytes or str) as /<name>, replacing a build file of that name for this test."""
        dst = os.path.join(self.www, name)
        if os.path.lexists(dst): os.remove(dst)
        open(dst, "wb").write(data if isinstance(data, bytes) else data.encode())

    def requests(self):
        try: return open(self.logf).read()
        except OSError: return ""

    def stop(self):
        import shutil
        try: self.proc.terminate(); self.proc.wait(5)
        except Exception: pass
        shutil.rmtree(self.www, ignore_errors=True)


class VM:
    def __init__(self, instance, stage, mem=None):
        self.instance, self.stage = instance, stage
        self.env = instance_env(instance)
        self.arch = "aarch64" if self.env["ARCH"] == "arm64" else "armv7"
        self.dir = os.path.join(STATE, instance, stage)
        self.ssh_port = int(self.env["SSH_PORT"])
        self.vnc_port = 5900 + int(self.env["VNC_DISPLAY"])
        self.mem = mem or ("2048" if self.arch == "armv7" else "3072")
        self.proc = self.vnc = None
        self.settled = True
        self.key = os.path.join(STATE, "guest_key")
        os.makedirs(STATE, exist_ok=True)
        if not os.path.exists(self.key):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pm-emu", "-f", self.key], check=True)

    # ---- lifecycle
    def start(self, loadvm=None):
        """Boot (or resume the snapshot `loadvm`) and return once ssh answers."""
        os.makedirs(self.dir, exist_ok=True)
        env = dict(os.environ, QEMU_BIN=os.path.join(HERE, "shim", "qemu-system-aarch64"), PM_OVERLAY_DIR=self.dir,
                   # the guest reaches only what this container reaches: nothing (in-container runs it with no network) but its loopback
                   PM_QEMU_APPEND="-loadvm %s" % loadvm if loadvm else "",
                   SERIAL="tcp:127.0.0.1:%d,server,nowait" % SERIAL_PORT, QEMU_AUDIO="off", MEM=self.mem,
                   QEMU_EXTRA_ARGS="-monitor tcp:127.0.0.1:%d,server,nowait" % MONITOR_PORT)
        self.qlog = open(os.path.join(self.dir, "qemu.log"), "ab")
        self.proc = subprocess.Popen([os.path.join(QENGINE, "scripts", "qemu", "run_instance.sh"), "--name", self.instance,
                                      "--display", "vnc", "--no-gl"], env=env, stdout=self.qlog, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, cwd=QENGINE, start_new_session=True)
        self.t0 = time.time()
        self._wait_port(MONITOR_PORT, 30)
        self._serial_stop = threading.Event()
        self._serial = threading.Thread(target=self._serial_console, args=(not loadvm,), daemon=True)
        self._serial.start()
        self.wait_ssh(slow(60 if loadvm else 420))
        log("%s/%s: ssh up after %ds%s" % (self.instance, self.stage, time.time() - self.t0, " (snapshot %s)" % loadvm if loadvm else ""))
        return self

    def _wait_port(self, port, secs):
        end = time.time() + secs
        while time.time() < end:
            if self.proc.poll() is not None:
                raise Fail("QEMU exited: see %s" % os.path.join(self.dir, "qemu.log"))
            try:
                socket.create_connection(("127.0.0.1", port), 1).close()
                return
            except OSError:
                time.sleep(0.5)
        raise Fail("port %d never opened" % port)

    def _serial_console(self, login):
        """Hold the serial console for the machine's whole life (QEMU serves one client, and stalls if a client leaves bytes unread
        by the guest), log it, and on a fresh boot log in at the first prompt and start sshd. Nothing is sent before `login:`."""
        try:
            s = socket.create_connection(("127.0.0.1", SERIAL_PORT), 10)
        except OSError:
            return
        s.settimeout(1)
        pub = open(self.key + ".pub").read().strip()
        # No default route: the firmware's connman left the emulator's NIC down (the MPC Sample tells connman to switch wired
        # networking off when it starts; its SSH runs over the USB gadget link, which QEMU has not got). connman is told to leave the
        # NIC alone (a copy of its main.conf in /run, bound over it) and the NIC gets QEMU's usual address. Other firmware has a route.
        net = ("if ! awk '$2 == \"00000000\" { f = 1 } END { exit !f }' /proc/net/route; then i=$(ls /sys/class/net | grep -v '^lo$' | head -n 1); c=/etc/connman/main.conf; "
               "if [ -f $c ] && ! grep -q NetworkInterfaceBlacklist $c; then { cat $c; echo \"NetworkInterfaceBlacklist = $i\"; } > /run/pm/connman.conf; "
               "mount --bind /run/pm/connman.conf $c; systemctl restart connman; fi; "
               "ifconfig $i 10.0.2.15 netmask 255.255.255.0 up; route add default gw 10.0.2.2; fi; ")
        setup = ("mkdir -p /run/sshd /run/pm; " + net + "echo '%s' > /run/pm/authorized_keys; chmod 600 /run/pm/authorized_keys; "
                 "k=$(ls /etc/ssh/ssh_host_*_key 2>/dev/null | head -n 1); [ -n \"$k\" ] || { k=/run/pm/hostkey; [ -f $k ] || ssh-keygen -q -t ed25519 -N '' -f $k; }; "
                 "/usr/sbin/sshd -h $k -o AuthorizedKeysFile=/run/pm/authorized_keys -o PermitRootLogin=yes -o StrictModes=no; echo PM''SSHD $?\n" % pub)
        buf, state = b"", "boot" if login else "done"
        with open(os.path.join(self.dir, "serial.log"), "ab") as f:
            while not self._serial_stop.is_set():
                try:
                    d = s.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not d:
                    return
                f.write(d); f.flush()
                buf = (buf + d)[-4000:]
                if state == "boot" and re.search(rb"login: *$", buf):
                    s.sendall(b"root\n"); state, buf = "login", b""
                elif state == "login" and b"assword" in buf:
                    s.sendall(b"\n"); buf = b""
                elif state == "login" and re.search(rb"[#$] *$", buf):
                    s.sendall(setup.encode()); state, buf = "sshd", b""
                elif state == "sshd" and b"PMSSHD" in buf:
                    state = "done"

    def wait_ssh(self, secs):
        end = time.time() + secs
        while time.time() < end:
            if self.proc.poll() is not None:
                raise Fail("QEMU exited: see %s" % os.path.join(self.dir, "qemu.log"))
            if self.sh("true", timeout=8, check=False)[0] == 0:
                return
            time.sleep(2)
        raise Fail("ssh never answered (serial log: %s)" % os.path.join(self.dir, "serial.log"))

    def mon(self, cmd, timeout=300):
        """One QEMU monitor (HMP) command; returns what it printed."""
        s = socket.create_connection(("127.0.0.1", MONITOR_PORT), 10)
        s.settimeout(timeout)
        def until_prompt():
            b = b""
            while not b.endswith(b"(qemu) "):
                d = s.recv(65536)
                if not d: break
                b += d
            return b
        until_prompt()
        s.sendall(cmd.encode() + b"\n")
        out = until_prompt() if cmd != "quit" else b""
        s.close()
        txt = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out.decode("utf-8", "replace")).replace("\r", "")
        return "\n".join(l for l in txt.split("\n")[1:] if not l.startswith("(qemu)")).strip()

    def savevm(self, tag):
        self.sh("sync", check=False)
        t = time.time()
        out = self.mon("savevm " + tag, 900)
        if out: raise Fail("savevm: " + out)
        log("%s/%s: snapshot %s saved in %ds" % (self.instance, self.stage, tag, time.time() - t))

    def kill(self):
        """Power cut: QEMU goes at once, nothing in the guest is flushed."""
        if self.vnc is not None:
            try: self.vnc.disconnect()
            except Exception: pass
            self.vnc = None
        if self.proc and self.proc.poll() is None:
            try: self.mon("quit", 5)
            except Exception: pass
            try: self.proc.wait(15)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, 9); self.proc.wait()
        if getattr(self, "_serial_stop", None): self._serial_stop.set()
        self.proc = None

    def stop(self):
        """Clean shutdown: the guest syncs and powers off, then QEMU goes."""
        if self.proc and self.proc.poll() is None:
            self.sh("sync; sync", check=False, timeout=60)
        self.kill()

    # ---- the guest's shell and files
    def _ssh(self):
        return ["ssh", "-q", "-p", str(self.ssh_port), "-i", self.key, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR", "root@127.0.0.1"]

    def sh(self, cmd, timeout=120, check=True, input=None):
        """Run a shell command in the guest: (exit status, output). check=True raises on a non-zero status."""
        try:
            r = subprocess.run(self._ssh() + [cmd], capture_output=True, timeout=timeout, input=input)
        except subprocess.TimeoutExpired:
            if check: raise Fail("guest command timed out after %ds: %s" % (timeout, cmd))
            return 124, ""
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        if check and r.returncode:
            raise Fail("guest command failed (%d): %s\n%s" % (r.returncode, cmd, out[-2000:]))
        return r.returncode, out

    def out(self, cmd, **kw):
        return self.sh(cmd, **kw)[1]

    def put(self, local, remote):
        with open(local, "rb") as f:
            self.sh("mkdir -p %s && cat > %s" % (shlex.quote(os.path.dirname(remote) or "/"), shlex.quote(remote)), input=f.read(), timeout=600)

    def write(self, remote, text):
        self.sh("mkdir -p %s && cat > %s" % (shlex.quote(os.path.dirname(remote) or "/"), shlex.quote(remote)), input=text.encode())

    def read(self, remote, check=False):
        return self.sh("cat %s" % shlex.quote(remote), check=check)[1]

    def exists(self, remote):
        return self.sh("[ -e %s ]" % shlex.quote(remote), check=False)[0] == 0

    def fetch(self, name, dest):
        """Download http://<fixture>/<name> in the guest (the way the Manager gets its files) to dest."""
        self.sh("wget -q -O %s http://%s/%s" % (shlex.quote(dest), FIXTURE_ADDR, name), timeout=600)

    # ---- screen and touch
    def _vnc(self):
        if self.vnc is None:
            from vncdotool import api
            self.vnc = api.connect("127.0.0.1::%d" % self.vnc_port, password=None)
            self.vnc.timeout = 30
        return self.vnc

    def shot(self, path=None):
        """The screen as a PIL image (saved to path when given)."""
        c = self._vnc()
        try: c.refreshScreen()
        except TimeoutError:                # a slow host (a 2-core CI runner) can miss the 30 s: connect again, once
            log("%s/%s: VNC timed out, reconnecting" % (self.instance, self.stage))
            try: self.vnc.disconnect()
            except Exception: pass
            self.vnc = None; c = self._vnc(); c.refreshScreen()
        img = c.screen.convert("RGB")
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            img.save(path)
        return img

    def tap(self, x, y, hold=0.08, after=1.2):
        """A finger tap. MPC ignores a press and release that arrive together, so it is held (80 ms: a quick tap)."""
        c = self._vnc()
        c.mouseMove(x, y); time.sleep(0.15)
        c.mouseDown(1); time.sleep(hold); c.mouseUp(1)
        time.sleep(after)

    def settle(self, secs=30, quiet=None):
        """Wait until the screen has stopped changing (a slow guest draws a new screen over several seconds, and a tap on a
        half-drawn one lands somewhere else) and return it. Gives up after `secs` (scaled) and returns what is there: some screens
        never stop moving."""
        quiet = quiet if quiet is not None else 1.0 + 0.5 * (SCALE - 1)
        end = time.time() + slow(secs)
        last = self.shot(); since = time.time(); same = 0
        self.settled = True
        while time.time() < end:
            time.sleep(0.25)
            img = self.shot()
            if changed(last, img): last, since, same = img, time.time(), 0
            else:
                same += 1
                if same >= 2 and time.time() - since >= quiet: return img
        self.settled = False                # e.g. a progress bar: the caller decides whether to act on it
        return last

    def double_tap(self, x, y):
        """Two quick taps, as MPC wants to open a field's list. Spaced as two tap()s are, a slow guest takes them as two single taps."""
        c = self._vnc()
        c.mouseMove(x, y); time.sleep(0.15)
        c.mouseDown(1); time.sleep(0.06); c.mouseUp(1); time.sleep(0.06)
        c.mouseDown(1); time.sleep(0.06); c.mouseUp(1)
        time.sleep(0.2)

    def press(self, x, y, before=None, change=10, double=False, **kw):
        """Tap once the screen is still, then wait for the tap to show (the screen changes) and for the screen to settle again.
        Returns the settled screen, or None if nothing changed within `change` seconds (scaled): the tap was not taken, or the
        guest is still busy with it; the caller looks at the screen again before doing anything else."""
        before = before if before is not None else self.settle()
        if double: self.double_tap(x, y)
        else: self.tap(x, y, after=0.2, **kw)
        end = time.time() + slow(change)
        while time.time() < end:
            if changed(before, self.shot()): return self.settle()
            time.sleep(0.3)
        return None

    def wait_stable(self, secs=60, quiet=1.5, not_black=True):
        """Wait until the screen stops changing (two equal captures `quiet` apart) and, by default, shows something. Returns the image."""
        end, last = time.time() + slow(secs), None
        while time.time() < end:
            img = self.shot()
            b = img.resize((160, 100)).tobytes()
            if b == last and (not not_black or max(img.resize((16, 10)).convert("L").getdata()) > 40):
                return img
            last = b
            time.sleep(quiet)
        raise Fail("the screen never settled")

    # -- reading the screen ------------------------------------------------------------------------------------------------------
    def words(self, save=None, img=None):
        """OCR of the screen now (or of the capture `img`): ocr.words(). `save` keeps the capture."""
        import ocr
        path = save or os.path.join(self.dir, "screen.png")
        if img is None: img = self.shot(path)
        else: img.save(path)
        self.last_shot = img
        self._words = ocr.words(path)
        return self._words

    def find(self, text, fresh=True):
        import ocr
        ws = self.words() if fresh or not getattr(self, "_words", None) else self._words
        return ocr.find(None, text, ws)

    def tap_text(self, text, n=0, dx=0, dy=0, fresh=False, **kw):
        """Tap the n-th place the screen shows `text` (from the last capture unless fresh)."""
        hits = self.find(text, fresh)
        if len(hits) <= n: raise Fail("the screen does not show %r" % text)
        self.tap(hits[n][0] + dx, hits[n][1] + dy, **kw)

    def wait_text(self, text, secs=60):
        end = time.time() + slow(secs)
        while time.time() < end:
            hits = self.find(text)
            if hits: return hits
            time.sleep(1)
        raise Fail("the screen never showed %r (see %s)" % (text, os.path.join(self.dir, "screen.png")))

    # -- the Manager's own web API, from inside the guest ---------------------------------------------------------------------------
    def api(self, path="state", post=False, check=True):
        """GET (or POST) http://127.0.0.1:8790/api/<path> on the device and return the parsed JSON (or the text)."""
        import json
        cmd = "wget -q -T 20 -O - %s 'http://127.0.0.1:8790/api/%s'" % ("--header 'X-PM: 1' --post-data ''" if post else "", path)
        rc, out = self.sh(cmd, check=False)
        if rc and check: raise Fail("the Manager's web API did not answer /api/%s: %s" % (path, out[-300:]))
        try: return json.loads(out)
        except ValueError: return out

    def state(self, secs=0, until=None, why=""):
        """The Manager's /api/state; with `until` (a function of the state), poll until it is true or `secs` (scaled) pass."""
        end = time.time() + slow(secs)
        while True:
            st = self.api("state", check=False)
            if isinstance(st, dict) and (until is None or until(st)): return st
            if time.time() >= end:
                raise Fail("the Manager never reached: %s (last state: %s)" % (why or "an answer", str(st)[:400]))
            time.sleep(2)


def shutdown():
    try:
        from vncdotool import api
        api.shutdown()
    except Exception:
        pass
