/* The fixture addin: MPC preloads it, and it says so. Each process that loads it adds its name to /tmp/emu-addin.loaded,
 * which is how a test knows the restart after an install really started MPC with the addin. */
#include <stdio.h>
__attribute__((constructor)) static void loaded(void) {
    char comm[64] = "?";
    FILE *c = fopen("/proc/self/comm", "r");
    if (c) { if (!fgets(comm, sizeof comm, c)) comm[0] = 0; fclose(c); }
    FILE *f = fopen("/tmp/emu-addin.loaded", "a");
    if (f) { fputs(comm, f); fclose(f); }
}
