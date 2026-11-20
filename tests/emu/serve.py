#!/usr/bin/env python3
"""The fixture server: serves the current directory on 127.0.0.1:<port>, one thread per request (the Manager fetches in parallel),
and logs each request (PM_FIXTURE_LOG) so a test can see what the device asked for.
A file named .fail holds lines "<path> <count>": the next <count> requests for that path get their connection closed with no answer
(a network that drops), which is how a test checks the Manager tries again."""
import http.server, os, sys, time

LOG = os.environ.get("PM_FIXTURE_LOG", "/tmp/pm-fixture-requests.log")


def should_fail(path):
    try: lines = open(".fail").read().split("\n")
    except OSError: return False
    hit = False
    for i, l in enumerate(lines):
        f = l.split()
        if len(f) == 2 and f[0] == path and int(f[1]) > 0:
            lines[i] = "%s %d" % (f[0], int(f[1]) - 1); hit = True; break
    if hit: open(".fail", "w").write("\n".join(lines))
    return hit


class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if should_fail(self.path.split("?")[0]):
            self.log_message("DROPPED %s", self.path)
            self.close_connection = True
            return
        super().do_GET()

    def log_message(self, fmt, *a):
        try:
            with open(LOG, "a") as f: f.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))
        except OSError: pass


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
