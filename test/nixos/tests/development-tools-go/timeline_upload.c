// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
static int send_all(int fd, const char *data, size_t size) {
  while (size) {
    ssize_t n = send(fd, data, size, MSG_NOSIGNAL);
    if (n <= 0)
      return -1;
    data += n;
    size -= (size_t)n;
  }
  return 0;
}
static int upload(const char *file, const char *name) {
  int input = open(file, O_RDONLY);
  if (input < 0) {
    perror("open upload");
    return 1;
  }
  struct stat stat;
  if (fstat(input, &stat) || stat.st_size <= 0 ||
      stat.st_size >= 16 * 1024 * 1024) {
    close(input);
    return 2;
  }
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) {
    perror("socket");
    close(input);
    return 3;
  }
  struct timeval timeout = {.tv_sec = 60};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  struct sockaddr_in address = {.sin_family = AF_INET,
                                .sin_port = htons(18781)};
  inet_pton(AF_INET, "10.0.2.2", &address.sin_addr);
  if (connect(fd, (struct sockaddr *)&address, sizeof(address))) {
    perror("connect collector");
    close(input);
    close(fd);
    return 4;
  }
  char header[1024];
  int length =
      snprintf(header, sizeof(header),
               "POST /%s HTTP/1.0\r\nHost: 10.0.2.2\r\nContent-Length: "
               "%ld\r\nConnection: close\r\n\r\n",
               name, (long)stat.st_size);
  if (length <= 0 || length >= (int)sizeof(header) ||
      send_all(fd, header, (size_t)length)) {
    close(input);
    close(fd);
    return 5;
  }
  char buffer[65536];
  ssize_t count;
  while ((count = read(input, buffer, sizeof(buffer))) > 0)
    if (send_all(fd, buffer, (size_t)count)) {
      close(input);
      close(fd);
      return 6;
    }
  close(input);
  if (count < 0) {
    close(fd);
    return 7;
  }
  char response[64];
  ssize_t received = 0;
  while (received < 12) {
    ssize_t n = recv(fd, response + received,
                     sizeof(response) - 1 - (size_t)received, 0);
    if (n <= 0) {
      close(fd);
      return 8;
    }
    received += n;
  }
  close(fd);
  response[received] = 0;
  if (strncmp(response, "HTTP/1.0 201", 12) &&
      strncmp(response, "HTTP/1.1 201", 12)) {
    fprintf(stderr, "collector response: %s\n", response);
    return 9;
  }
  return 0;
}
int main(int argc, char **argv) {
  if (argc != 3)
    return 2;
  for (const char *c = argv[2]; *c; c++)
    if (!isalnum((unsigned char)*c) && *c != '-' && *c != '_')
      return 3;
  char name[512], file[512];
  if (snprintf(name, sizeof(name), "%s.bin", argv[2]) >= (int)sizeof(name))
    return 4;
  int result = upload(argv[1], name);
  if (result)
    return 10 + result;
  if (snprintf(name, sizeof(name), "%s.child.log", argv[2]) >=
          (int)sizeof(name) ||
      snprintf(file, sizeof(file), "%s.child.log", argv[1]) >=
          (int)sizeof(file))
    return 5;
  result = upload(file, name);
  if (result)
    return 30 + result;
  printf("TIMELINE_UPLOAD_OK %s\n", argv[2]);
  return 0;
}
