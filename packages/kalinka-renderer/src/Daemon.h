#pragma once

/**
 * @brief Detach from the controlling terminal.
 *
 * Double fork, setsid, chdir to /, stdio to /dev/null. Must be called before
 * any thread or socket exists.
 *
 * @return false when a step failed, in which case the caller should exit. On
 *         success the caller is the daemonized child; the parent has exited.
 */
bool daemonize();
