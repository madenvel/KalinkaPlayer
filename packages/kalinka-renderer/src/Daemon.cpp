#include "Daemon.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>

bool daemonize() {
  pid_t pid = fork();
  if (pid < 0) {
    std::perror("fork");
    return false;
  }
  if (pid > 0) {
    _exit(0);  // parent
  }

  if (setsid() < 0) {
    std::perror("setsid");
    return false;
  }

  // Second fork: not a session leader, can never reacquire a terminal.
  pid = fork();
  if (pid < 0) {
    std::perror("fork");
    return false;
  }
  if (pid > 0) {
    _exit(0);
  }

  umask(022);
  if (chdir("/") < 0) {
    std::perror("chdir");
    return false;
  }

  const int null = open("/dev/null", O_RDWR);
  if (null < 0) {
    return false;
  }
  dup2(null, STDIN_FILENO);
  dup2(null, STDOUT_FILENO);
  dup2(null, STDERR_FILENO);
  if (null > STDERR_FILENO) {
    close(null);
  }
  return true;
}
