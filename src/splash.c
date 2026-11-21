/* OpenMPC Plugin Manager update screen: what apply.sh is doing, on the MPC's own display, while MPC is stopped.
 *
 *   openmpc-pm-splash PROGRESS [NAMES]                     draw on /dev/dri/card0 until apply.sh stops it
 *   openmpc-pm-splash --ppm OUT [--size WxH] [--rot R] [--tick N] [--state LINE] [--names FILE]    one frame to a PPM
 *
 * PROGRESS is apply.sh's progress file: one line "step<TAB>of<TAB>act<TAB>id<TAB>version" (act: install, remove,
 * register, theme, move, start). NAMES is "id<TAB>display name" lines (the Manager writes it next to apply.sh).
 *
 * Display: MPC is the only DRM client and is stopped while this runs, so this opens card0, takes master (or gives up
 * at once if anyone else holds it), sets the first connected connector's preferred mode with one dumb buffer, and
 * redraws a few times a second. MPC fails to start ("Failed to initialise display") while another process holds the
 * display (see the DrmVnc crash loop), so this lets go in every way it can: apply.sh kills it and waits for it before
 * starting MPC, it exits when its parent dies (PR_SET_PDEATHSIG and a getppid check), when PROGRESS goes away, and
 * after an hour in any case. It never touches MPC's files and nothing it does survives a reboot.
 *
 * Orientation: the panels are portrait 800x1280 scanned out sideways (MPC rotates its landscape picture); like
 * force-shadow (DESIGN.md "Rendering orientation") everything is drawn in landscape coordinates through one put()
 * transform. A portrait mode is drawn rotated 90 degrees, a landscape one (MPC X) as is; PM_SPLASH_ROT=0|90|180|270
 * overrides that. Colours follow the Manager's own skin. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#ifndef PM_SPLASH_NO_DRM
#include "splash_drm.h"
#endif
#include "splash_font.h"
#pragma GCC diagnostic ignored "-Wformat-truncation"   /* names and lines are cut to fit on purpose */

#define BG      0x2a2b2fu
#define PANEL   0x3a3c41u
#define TRACK   0x4a4c52u
#define ACCENT  0xb88426u
#define OKC     0x3fbf7fu
#define TEXT    0xffffffu
#define SUBTEXT 0xc9ccd1u

typedef struct { uint32_t *px; int bw, bh, stride, rot, w, h; } canvas_t;   /* bw/bh/stride: buffer; w/h: landscape view */

static void put(canvas_t *c, int x, int y, uint32_t col) {
    if (x < 0 || y < 0 || x >= c->w || y >= c->h) return;
    int bx, by;
    switch (c->rot) {
    case 90:  bx = y;             by = c->bh - 1 - x; break;   /* == force-shadow: buffer_x = y, buffer_y = (H-1) - x */
    case 270: bx = c->bw - 1 - y; by = x;             break;
    case 180: bx = c->bw - 1 - x; by = c->bh - 1 - y; break;
    default:  bx = x;             by = y;             break;
    }
    c->px[by * c->stride + bx] = col;
}
static uint32_t mix(uint32_t a, uint32_t b, int t) {   /* t of 255 towards b */
    uint32_t r = (((a >> 16) & 255) * (255 - t) + ((b >> 16) & 255) * t) / 255;
    uint32_t g = (((a >> 8) & 255) * (255 - t) + ((b >> 8) & 255) * t) / 255;
    uint32_t bl = ((a & 255) * (255 - t) + (b & 255) * t) / 255;
    return r << 16 | g << 8 | bl;
}
static void rect(canvas_t *c, int x, int y, int w, int h, uint32_t col) {
    for (int j = y; j < y + h; j++) for (int i = x; i < x + w; i++) put(c, i, j, col);
}
/* Rounded rectangle, with an anti-aliased edge (coverage from the distance to the corner circle, in 1/4 px steps). */
static void rrect(canvas_t *c, int x, int y, int w, int h, int r, uint32_t col, uint32_t under) {
    if (r * 2 > h) r = h / 2;
    if (r * 2 > w) r = w / 2;
    for (int j = 0; j < h; j++) for (int i = 0; i < w; i++) {
        int cx = i < r ? r - i : (i >= w - r ? i - (w - r - 1) : 0);
        int cy = j < r ? r - j : (j >= h - r ? j - (h - r - 1) : 0);
        if (!cx || !cy) { put(c, x + i, y + j, col); continue; }
        int in = 0;
        for (int sy = 0; sy < 4; sy++) for (int sx = 0; sx < 4; sx++) {
            int dx = 8 * (cx - 1) + 2 * sx + 1, dy = 8 * (cy - 1) + 2 * sy + 1;   /* 4x4 samples, distance to the corner centre in 1/8 px */
            if (dx * dx + dy * dy <= r * r * 64) in++;
        }
        if (in) put(c, x + i, y + j, in == 16 ? col : mix(under, col, in * 255 / 16));
    }
}

/* ---- text ---- */
static const sglyph_t *glyph(const sfont_t *f, unsigned char ch) { return &f->g[(ch < 32 || ch > 126 ? '?' : ch) - 32]; }
static int text_w(const sfont_t *f, const char *s) {
    int w = 0;
    for (; *s; s++) w += glyph(f, (unsigned char)*s)->adv;
    return w;
}
static void text(canvas_t *c, const sfont_t *f, int x, int y, const char *s, uint32_t col, uint32_t under) {
    for (; *s; s++) {
        const sglyph_t *g = glyph(f, (unsigned char)*s);
        const unsigned char *p = f->px + g->off;
        for (int j = 0; j < f->h; j++) for (int i = 0; i < g->w; i++) {
            int a = p[j * g->w + i];
            if (a) put(c, x - SFONT_PAD + i, y + j, a == 255 ? col : mix(under, col, a));
        }
        x += g->adv;
    }
}
static void text_c(canvas_t *c, const sfont_t *f, int cx, int y, const char *s, uint32_t col, uint32_t under) {
    text(c, f, cx - text_w(f, s) / 2, y, s, col, under);
}
/* Shorten s in place with "..." until it fits in w. */
static void fit(const sfont_t *f, char *s, int w) {
    size_t n = strlen(s);
    if (text_w(f, s) <= w) return;
    while (n > 0) {
        s[--n] = 0;
        char t[512];
        snprintf(t, sizeof t, "%s...", s);
        if (text_w(f, t) <= w) { strcpy(s, t); return; }
    }
}
/* Centred, word-wrapped lines; returns the y below the last one. */
static int para_c(canvas_t *c, const sfont_t *f, int cx, int y, int w, const char *s, uint32_t col, uint32_t under) {
    char line[512] = "";
    const char *p = s;
    while (*p) {
        const char *e = p;
        while (*e && *e != ' ') e++;
        char t[512];
        snprintf(t, sizeof t, "%s%s%.*s", line, *line ? " " : "", (int)(e - p), p);
        if (*line && text_w(f, t) > w) { text_c(c, f, cx, y, line, col, under); y += f->h * 6 / 5; snprintf(line, sizeof line, "%.*s", (int)(e - p), p); }
        else snprintf(line, sizeof line, "%s", t);
        p = *e ? e + 1 : e;
    }
    if (*line) { text_c(c, f, cx, y, line, col, under); y += f->h * 6 / 5; }
    return y;
}

/* ---- what to show ---- */
typedef struct { int step, of; char act[32], id[160], ver[64]; } prog_t;

static int parse(const char *line, prog_t *p) {
    memset(p, 0, sizeof *p);
    char buf[512];
    snprintf(buf, sizeof buf, "%s", line);
    buf[strcspn(buf, "\r\n")] = 0;
    char *f[5] = {0}, *s = buf;
    for (int i = 0; i < 5; i++) { f[i] = s; if (!s) break; char *t = strchr(s, '\t'); if (t) { *t = 0; s = t + 1; } else s = NULL; }
    if (!f[0] || !*f[0]) return -1;
    p->step = atoi(f[0]); p->of = f[1] ? atoi(f[1]) : 0;
    snprintf(p->act, sizeof p->act, "%s", f[2] ? f[2] : "");
    snprintf(p->id, sizeof p->id, "%s", f[3] ? f[3] : "");
    snprintf(p->ver, sizeof p->ver, "%s", f[4] ? f[4] : "");
    return 0;
}
static void name_of(const char *names, const char *id, char *out, size_t n) {
    snprintf(out, n, "%s", id);
    FILE *f = names ? fopen(names, "r") : NULL;
    if (!f) return;
    char l[512];
    size_t k = strlen(id);
    while (fgets(l, sizeof l, f))
        if (k && !strncmp(l, id, k) && l[k] == '\t') { l[strcspn(l, "\r\n")] = 0; snprintf(out, n, "%s", l + k + 1); break; }
    fclose(f);
}

/* One frame. tick moves the light along the current step's part of the bar, so a long step does not look frozen. */
static void render(canvas_t *c, const prog_t *p, const char *names, unsigned tick) {
    rect(c, 0, 0, c->w, c->h, BG);
    int W = c->w, H = c->h, cx = W / 2, mw = W - 160;
    char name[200], what[400];
    name_of(names, p->id, name, sizeof name);
    if (!strcmp(p->act, "install")) snprintf(what, sizeof what, "Installing %s%s%s", name, *p->ver ? " " : "", p->ver);
    else if (!strcmp(p->act, "remove")) snprintf(what, sizeof what, "Removing %s", name);
    else if (!strcmp(p->act, "register")) snprintf(what, sizeof what, "Adding %s to MPC", *p->id && strncmp(p->id, "loc-", 4) ? name : "a plugin folder");
    else if (!strcmp(p->act, "theme")) snprintf(what, sizeof what, "Switching the theme");
    else if (!strcmp(p->act, "move")) snprintf(what, sizeof what, "Moving plugins to VST2 Plugins");
    else if (!strcmp(p->act, "start")) snprintf(what, sizeof what, "Starting MPC");
    else snprintf(what, sizeof what, "Updating plugins");
    int done = !strcmp(p->act, "start");

    text_c(c, &sfont_s, cx, H * 9 / 100, "OpenMPC Plugin Manager", SUBTEXT, BG);

    int py = H * 26 / 100, ph = H * 50 / 100;            /* the panel */
    rrect(c, 60, py, W - 120, ph, 28, PANEL, BG);
    const sfont_t *tf = text_w(&sfont_l, what) <= mw - 40 ? &sfont_l : &sfont_m;   /* a long title steps down a size before it is cut */
    fit(tf, what, mw - 40);
    text_c(c, tf, cx, py + ph * 18 / 100 + (sfont_l.h - tf->h) / 2, what, TEXT, PANEL);
    char sl[64];
    if (done) snprintf(sl, sizeof sl, "Finished");
    else if (p->of > 0) snprintf(sl, sizeof sl, "Step %d of %d", p->step, p->of);
    else *sl = 0;
    text_c(c, &sfont_m, cx, py + ph * 45 / 100, sl, SUBTEXT, PANEL);

    int bx = 140, bw = W - 280, by = py + ph * 70 / 100, bh = 22;   /* the bar: finished steps solid, the current one pulsing */
    rrect(c, bx, by, bw, bh, bh / 2, TRACK, PANEL);
    int of = p->of > 0 ? p->of : 1, st = p->step < 1 ? 1 : (p->step > of ? of : p->step);
    int x0 = done ? bw : bw * (st - 1) / of, x1 = done ? bw : bw * st / of;
    uint32_t pulse = TRACK;
    if (!done && x1 > x0) {   /* the current step, brightening and dimming twice a second */
        int t = tick % 16, a = t < 8 ? t : 16 - t;
        pulse = mix(TRACK, ACCENT, 60 + a * 18);
        rrect(c, bx, by, x1, bh, bh / 2, pulse, TRACK);
    }
    if (x0 > 0) rrect(c, bx, by, x0 < bh ? bh : x0, bh, bh / 2, done ? OKC : ACCENT, pulse);

    para_c(c, &sfont_s, cx, py + ph + H * 7 / 100, mw,
           done ? "The Plugin Manager shows how each step went." : "Don't switch the MPC off. It starts again by itself when this is done.",
           done ? SUBTEXT : ACCENT, BG);
}

static int read_prog(const char *path, prog_t *p) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    char l[512] = "";
    int ok = fgets(l, sizeof l, f) != NULL;
    fclose(f);
    return ok ? parse(l, p) : -1;
}

static int write_ppm(const char *out, canvas_t *c) {
    FILE *f = fopen(out, "wb");
    if (!f) { perror(out); return 1; }
    fprintf(f, "P6\n%d %d\n255\n", c->bw, c->bh);
    for (int y = 0; y < c->bh; y++) for (int x = 0; x < c->bw; x++) {
        uint32_t v = c->px[y * c->stride + x];
        unsigned char rgb[3] = { v >> 16, v >> 8, v };
        fwrite(rgb, 1, 3, f);
    }
    return fclose(f) ? 1 : 0;
}

static int auto_rot(int bw, int bh) {
    const char *e = getenv("PM_SPLASH_ROT");
    if (e && *e) { int r = atoi(e); if (r == 0 || r == 90 || r == 180 || r == 270) return r; }
    return bh > bw ? 90 : 0;
}
static void setup(canvas_t *c, uint32_t *px, int bw, int bh, int stride, int rot) {
    c->px = px; c->bw = bw; c->bh = bh; c->stride = stride; c->rot = rot;
    c->w = rot % 180 ? bh : bw; c->h = rot % 180 ? bw : bh;
}

static int offline(int argc, char **argv) {
    const char *out = NULL, *names = NULL, *state = "2\t5\tinstall\tmodel-72\t1.0.0";
    int bw = 800, bh = 1280, rot = -1;
    unsigned tick = 4;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--ppm") && i + 1 < argc) out = argv[++i];
        else if (!strcmp(argv[i], "--size") && i + 1 < argc) { if (sscanf(argv[++i], "%dx%d", &bw, &bh) != 2) return 2; }
        else if (!strcmp(argv[i], "--rot") && i + 1 < argc) rot = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--tick") && i + 1 < argc) tick = (unsigned)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--state") && i + 1 < argc) state = argv[++i];
        else if (!strcmp(argv[i], "--names") && i + 1 < argc) names = argv[++i];
        else { fprintf(stderr, "unknown argument %s\n", argv[i]); return 2; }
    }
    if (!out || bw < 64 || bh < 64 || bw > 4096 || bh > 4096) { fprintf(stderr, "usage: --ppm OUT [--size WxH] [--rot R] [--tick N] [--state LINE] [--names FILE]\n"); return 2; }
    uint32_t *px = calloc((size_t)bw * bh, 4);
    if (!px) return 1;
    canvas_t c;
    setup(&c, px, bw, bh, bw, rot < 0 ? auto_rot(bw, bh) : rot);
    prog_t p;
    if (parse(state, &p)) { fprintf(stderr, "bad --state\n"); return 2; }
    render(&c, &p, names, tick);
    int r = write_ppm(out, &c);
    free(px);
    return r;
}

#ifndef PM_SPLASH_NO_DRM
static volatile sig_atomic_t stop;
static void on_sig(int s) { (void)s; stop = 1; }

static int xioctl(int fd, unsigned long req, void *arg) {
    int r;
    do r = ioctl(fd, req, arg); while (r < 0 && (errno == EINTR || errno == EAGAIN));
    return r;
}

static int on_display(const char *prog_path, const char *names) {
    pid_t parent = getppid();
    prctl(PR_SET_PDEATHSIG, SIGTERM);
    if (getppid() != parent) return 0;   /* apply.sh already gone */
    signal(SIGTERM, on_sig); signal(SIGINT, on_sig); signal(SIGHUP, on_sig);

    int fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
    if (fd < 0) { fprintf(stderr, "splash: card0: %s\n", strerror(errno)); return 3; }
    if (xioctl(fd, DRM_IOCTL_SET_MASTER, NULL) < 0) { fprintf(stderr, "splash: the display is in use (%s), not drawing\n", strerror(errno)); close(fd); return 3; }

    int ret = 3;
    uint32_t conn_ids[16], crtc_ids[16], enc_ids[16];
    struct drm_mode_card_res res = {0};
    struct drm_mode_modeinfo modes[32], mode;
    struct drm_mode_create_dumb cd = {0};
    struct drm_mode_destroy_dumb dd = {0};
    uint32_t fb = 0, crtc = 0, conn = 0;
    void *map = MAP_FAILED;

    if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0 || !res.count_connectors || !res.count_crtcs) goto out;
    if (res.count_connectors > 16) res.count_connectors = 16;
    if (res.count_crtcs > 16) res.count_crtcs = 16;
    if (res.count_encoders > 16) res.count_encoders = 16;
    res.connector_id_ptr = (uintptr_t)conn_ids; res.crtc_id_ptr = (uintptr_t)crtc_ids; res.encoder_id_ptr = (uintptr_t)enc_ids;
    res.count_fbs = 0; res.fb_id_ptr = 0;
    if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0) goto out;

    for (uint32_t i = 0; i < res.count_connectors && !conn; i++) {
        struct drm_mode_get_connector gc = { .connector_id = conn_ids[i] };
        if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &gc) < 0) continue;
        if (gc.connection != 1 || !gc.count_modes) continue;
        if (gc.count_modes > 32) gc.count_modes = 32;
        uint32_t ne = gc.count_encoders > 16 ? 16 : gc.count_encoders, encs[16];
        struct drm_mode_get_connector g2 = { .connector_id = conn_ids[i], .count_modes = gc.count_modes, .modes_ptr = (uintptr_t)modes,
                                             .count_encoders = ne, .encoders_ptr = (uintptr_t)encs };
        if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &g2) < 0 || !g2.count_modes) continue;
        uint32_t nm = g2.count_modes > 32 ? 32 : g2.count_modes;
        mode = modes[0];
        for (uint32_t m = 0; m < nm; m++) if (modes[m].type & DRM_MODE_TYPE_PREFERRED) { mode = modes[m]; break; }
        uint32_t enc = g2.encoder_id;
        if (!enc && ne) enc = encs[0];
        struct drm_mode_get_encoder ge = { .encoder_id = enc };
        if (!enc || xioctl(fd, DRM_IOCTL_MODE_GETENCODER, &ge) < 0) continue;
        crtc = ge.crtc_id;
        for (uint32_t k = 0; !crtc && k < res.count_crtcs; k++) if (ge.possible_crtcs & (1u << k)) crtc = crtc_ids[k];
        if (crtc) conn = conn_ids[i];
    }
    if (!conn) { fprintf(stderr, "splash: no connected display\n"); goto out; }

    cd.width = mode.hdisplay; cd.height = mode.vdisplay; cd.bpp = 32;
    if (xioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd) < 0) goto out;
    struct drm_mode_fb_cmd fc = { .width = cd.width, .height = cd.height, .pitch = cd.pitch, .bpp = 32, .depth = 24, .handle = cd.handle };
    if (xioctl(fd, DRM_IOCTL_MODE_ADDFB, &fc) < 0) goto out;
    fb = fc.fb_id;
    struct drm_mode_map_dumb md = { .handle = cd.handle };
    if (xioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &md) < 0) goto out;
    map = mmap(NULL, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, md.offset);
    if (map == MAP_FAILED) goto out;

    /* Draw into RAM and copy whole frames, so a half-drawn one is never scanned out for long. */
    uint32_t *back = malloc(cd.size);
    if (!back) goto out;
    canvas_t c;
    setup(&c, back, cd.width, cd.height, cd.pitch / 4, auto_rot(cd.width, cd.height));
    prog_t p = {0};
    if (read_prog(prog_path, &p)) parse("0\t0\twait\t\t", &p);
    render(&c, &p, names, 0);
    memcpy(map, back, cd.size);
    struct drm_mode_crtc sc = { .crtc_id = crtc, .fb_id = fb, .set_connectors_ptr = (uintptr_t)&conn, .count_connectors = 1, .mode = mode, .mode_valid = 1 };
    if (xioctl(fd, DRM_IOCTL_MODE_SETCRTC, &sc) < 0) { fprintf(stderr, "splash: setcrtc: %s\n", strerror(errno)); free(back); goto out; }
    fprintf(stderr, "splash: drawing %ux%u rot %d\n", cd.width, cd.height, c.rot);
    ret = 0;

    time_t t0 = time(NULL);
    int gone = 0;
    for (unsigned tick = 1; !stop; tick++) {
        struct timespec ts = { 0, 125 * 1000 * 1000 };
        nanosleep(&ts, NULL);
        if (getppid() != parent || time(NULL) - t0 > 3600) break;
        if (read_prog(prog_path, &p)) { if (++gone > 16) break; continue; }   /* gone for 2 s: apply.sh finished */
        gone = 0;
        render(&c, &p, names, tick);
        memcpy(map, back, cd.size);
    }
    free(back);
out:
    /* Let go of everything before exiting: MPC only starts on a free display. */
    if (map != MAP_FAILED) munmap(map, cd.size);
    if (fb) xioctl(fd, DRM_IOCTL_MODE_RMFB, &fb);
    if (cd.handle) { dd.handle = cd.handle; xioctl(fd, DRM_IOCTL_MODE_DESTROY_DUMB, &dd); }
    xioctl(fd, DRM_IOCTL_DROP_MASTER, NULL);
    close(fd);
    return ret;
}
#endif

int main(int argc, char **argv) {
    if (argc > 1 && argv[1][0] == '-') return offline(argc, argv);
#ifndef PM_SPLASH_NO_DRM
    if (argc < 2) { fprintf(stderr, "usage: %s PROGRESS [NAMES] | --ppm OUT ...\n", argv[0]); return 2; }
    return on_display(argv[1], argc > 2 ? argv[2] : NULL);
#else
    fprintf(stderr, "built without the display (PM_SPLASH_NO_DRM)\n");
    return 2;
#endif
}
