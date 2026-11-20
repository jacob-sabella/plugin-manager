/* The fixture plugin's "binary": a real shared object for the device's CPU, so nothing that looks at it sees a broken file.
 * MPC never loads it (no test puts it on a track); the tests only check that the Manager installs, updates and removes its package. */
int emu_probe(void) { return 1; }
