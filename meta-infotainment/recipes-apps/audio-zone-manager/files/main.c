/*
 * audio-zone-manager -- a small daemon representing the kind of
 * component that sits in an automotive infotainment audio stack:
 * it owns the set of audio "zones" (front, rear, etc.), each with a
 * volume and mute state, loaded from a JSON config at startup, and
 * exposes a tiny line-oriented control protocol over a Unix domain
 * socket so other processes (a HMI, a diagnostics tool) can query
 * and change zone state at runtime.
 *
 * This is a demo-scale program, not a production audio stack --
 * it exists to be a real recipe target (a real C program, linked
 * against a real third-party library, cJSON) rather than a stub.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <errno.h>
#include <unistd.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <cjson/cJSON.h>

#define MAX_ZONES 16
#define SOCK_PATH "/tmp/audio-zone-manager.sock"
#define LINE_BUF 256

typedef struct {
    char name[32];
    int volume;   /* 0-100 */
    int muted;    /* 0 or 1 */
} zone_t;

static zone_t zones[MAX_ZONES];
static int zone_count = 0;
static volatile int running = 1;

static void handle_signal(int sig) {
    (void)sig;
    running = 0;
}

static zone_t *find_zone(const char *name) {
    for (int i = 0; i < zone_count; i++) {
        if (strcasecmp(zones[i].name, name) == 0) {
            return &zones[i];
        }
    }
    return NULL;
}

/* Returns 0 on success, -1 on a config/parse error. Caller owns the
 * returned error message lifetime (static buffer, not thread-safe --
 * fine for this single-threaded demo). */
static int load_config(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "audio-zone-manager: cannot open config %s: %s\n", path, strerror(errno));
        return -1;
    }

    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)size + 1);
    if (!buf) {
        fclose(f);
        return -1;
    }
    size_t read_n = fread(buf, 1, (size_t)size, f);
    buf[read_n] = '\0';
    fclose(f);

    cJSON *root = cJSON_Parse(buf);
    free(buf);
    if (!root) {
        fprintf(stderr, "audio-zone-manager: invalid JSON in %s: %s\n", path, cJSON_GetErrorPtr());
        return -1;
    }

    cJSON *zone_arr = cJSON_GetObjectItemCaseSensitive(root, "zones");
    if (!cJSON_IsArray(zone_arr)) {
        fprintf(stderr, "audio-zone-manager: config missing \"zones\" array\n");
        cJSON_Delete(root);
        return -1;
    }

    cJSON *z;
    cJSON_ArrayForEach(z, zone_arr) {
        if (zone_count >= MAX_ZONES) break;
        cJSON *name = cJSON_GetObjectItemCaseSensitive(z, "name");
        cJSON *vol = cJSON_GetObjectItemCaseSensitive(z, "default_volume");
        cJSON *muted = cJSON_GetObjectItemCaseSensitive(z, "muted");
        if (!cJSON_IsString(name)) continue;

        zone_t *zn = &zones[zone_count++];
        snprintf(zn->name, sizeof(zn->name), "%s", name->valuestring);
        zn->volume = cJSON_IsNumber(vol) ? vol->valueint : 50;
        zn->muted = cJSON_IsBool(muted) ? cJSON_IsTrue(muted) : 0;
    }

    cJSON_Delete(root);
    return 0;
}

/* Handles one line of the control protocol, writing the reply
 * (no trailing newline) into `reply`, sized LINE_BUF. */
static void handle_command(const char *line, char *reply) {
    char cmd[16], zone_name[32], arg[32];
    int n = sscanf(line, "%15s %31s %31s", cmd, zone_name, arg);

    if (n >= 2 && strcasecmp(cmd, "GET") == 0) {
        zone_t *z = find_zone(zone_name);
        if (!z) {
            snprintf(reply, LINE_BUF, "ERR unknown zone %s", zone_name);
            return;
        }
        snprintf(reply, LINE_BUF, "OK %s volume=%d muted=%d", z->name, z->volume, z->muted);
        return;
    }

    if (n >= 3 && strcasecmp(cmd, "SET") == 0) {
        zone_t *z = find_zone(zone_name);
        if (!z) {
            snprintf(reply, LINE_BUF, "ERR unknown zone %s", zone_name);
            return;
        }
        if (strcasecmp(arg, "mute") == 0) {
            z->muted = 1;
        } else if (strcasecmp(arg, "unmute") == 0) {
            z->muted = 0;
        } else {
            int vol = atoi(arg);
            if (vol < 0) vol = 0;
            if (vol > 100) vol = 100;
            z->volume = vol;
        }
        snprintf(reply, LINE_BUF, "OK %s volume=%d muted=%d", z->name, z->volume, z->muted);
        return;
    }

    if (n >= 1 && strcasecmp(cmd, "LIST") == 0) {
        int off = 0;
        off += snprintf(reply + off, LINE_BUF - off, "OK");
        for (int i = 0; i < zone_count && off < LINE_BUF - 1; i++) {
            off += snprintf(reply + off, LINE_BUF - off, " %s=%d/%d",
                             zones[i].name, zones[i].volume, zones[i].muted);
        }
        return;
    }

    snprintf(reply, LINE_BUF, "ERR unrecognized command");
}

int main(int argc, char **argv) {
    const char *config_path = (argc > 1) ? argv[1] : "/etc/audio-zone-manager/zones.json";

    if (load_config(config_path) != 0) {
        return 1;
    }
    printf("audio-zone-manager: loaded %d zone(s) from %s\n", zone_count, config_path);

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    unlink(SOCK_PATH);
    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd < 0) {
        perror("socket");
        return 1;
    }

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", SOCK_PATH);

    if (bind(server_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        close(server_fd);
        return 1;
    }
    if (listen(server_fd, 4) < 0) {
        perror("listen");
        close(server_fd);
        return 1;
    }
    printf("audio-zone-manager: listening on %s\n", SOCK_PATH);

    while (running) {
        int client_fd = accept(server_fd, NULL, NULL);
        if (client_fd < 0) {
            if (running) perror("accept");
            continue;
        }

        char line[LINE_BUF];
        ssize_t n = read(client_fd, line, sizeof(line) - 1);
        if (n > 0) {
            line[n] = '\0';
            char *nl = strchr(line, '\n');
            if (nl) *nl = '\0';

            char reply[LINE_BUF];
            handle_command(line, reply);
            size_t reply_len = strlen(reply);
            reply[reply_len] = '\n';
            write(client_fd, reply, reply_len + 1);
        }
        close(client_fd);
    }

    close(server_fd);
    unlink(SOCK_PATH);
    printf("audio-zone-manager: shut down\n");
    return 0;
}
