#pragma once

// Detach from the controlling terminal (double fork, setsid, chdir /,
// stdio -> /dev/null). Returns false if any step failed; the caller should
// exit. On success the caller is the daemonized child.
bool daemonize();
