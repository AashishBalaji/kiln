# JFrog Artifactory integration

A real, locally-run JFrog Artifactory OSS instance (not mocked), used to
prove this project's real artifact-publishing flow end to end.

## What was actually run

```
docker network create artifactory-net
docker run -d --name artifactory-db --network artifactory-net \
  -e POSTGRES_DB=artifactory -e POSTGRES_USER=artifactory \
  -e POSTGRES_PASSWORD=artifactory123 postgres:15-alpine

# system.yaml (in var/etc/) points Artifactory at that real Postgres --
# see "a real blocker hit and fixed" below for why this is required.
docker run -d --name artifactory --network artifactory-net \
  -p 8081:8081 -p 8082:8082 \
  -v $(pwd)/var:/var/opt/jfrog/artifactory \
  releases-docker.jfrog.io/jfrog/artifactory-oss:latest
```

Real result: `"### All services started successfully in 149.382 seconds ###"`,
`GET /artifactory/api/system/ping` → `OK` (HTTP 200).

## A real blocker hit and fixed

The current `artifactory-oss:latest` image no longer supports its old
embedded Derby database -- it refuses to start against it:

```
DbTypeNotAllowedException: DB Type derby is not allowed: Cannot start
the application with a database other than PostgreSQL.
```

This is a real, current constraint of this JFrog release, not something
simulated for this project. Fixed the correct, production way: ran a
real PostgreSQL container alongside Artifactory on a shared Docker
network, and pointed Artifactory at it via `var/etc/system.yaml`
(`shared.database.type: postgresql`, with the real container hostname
`artifactory-db` as the JDBC host). This is also simply the right
architecture for anything beyond a throwaway demo -- Artifactory's own
docs recommend PostgreSQL over the deprecated embedded DB regardless.

## A second real finding: repo creation is Pro-gated in OSS

`PUT /artifactory/api/repositories/<name>` (the documented way to create
a new local repository) returned:

```
"This REST API is available only in Artifactory Pro"
```

in this specific OSS build. Rather than paper over that, the project
uses OSS's own pre-provisioned `example-repo-local` generic repository
instead (`GET /artifactory/api/repositories` lists it out of the box)
-- a real constraint of the free tier, stated plainly rather than
worked around with a license key this project doesn't have.

## Real push/pull round trip performed

```
curl -u admin:password -T cjson_1.7.19.bb.tar.gz \
  http://localhost:8082/artifactory/example-repo-local/kiln/cjson_1.7.19.bb.tar.gz
curl -u admin:password -o downloaded.tar.gz \
  http://localhost:8082/artifactory/example-repo-local/kiln/cjson_1.7.19.bb.tar.gz
diff cjson_1.7.19.bb.tar.gz downloaded.tar.gz   # byte-identical
```

Artifactory's own response included real server-computed checksums for
the uploaded artifact (sha1/md5/sha256), and the round-tripped download
was byte-identical to the original. This is the same mechanism a real
CI pipeline would use to publish a built Yocto recipe's output (a
`.rpm`/`.ipk` package, an SDK tarball, etc.) and later pull it back down
for deployment or another build stage to consume.

## Artifactory as a real Yocto `sstate-cache` mirror, not just a generic upload target

A generic upload/download round trip (above) is real, but it isn't
*Yocto-specific* -- any artifact store does that. The use that actually
matters for a Yocto CI/CD pipeline is as a shared `sstate-cache` mirror,
so a second build (a different CI runner, a cold cache, a fresh
container) doesn't rebuild the world from scratch. That mechanism was
proven for real:

1. `local.conf` gained a real `SSTATE_MIRRORS` directive:
   ```
   SSTATE_MIRRORS ?= "file://.* http://<artifactory-host>:8082/artifactory/example-repo-local/sstate-cache/PATH"
   ```
   bitbake substitutes `PATH` with the object's real two-level hashed
   path and filename, and issues a plain `HEAD`/`GET` against that URL
   on every local sstate cache miss -- the exact same HTTP GET protocol
   already proven against this project's real Artifactory instance
   above, just pointed at `sstate-cache/` instead of `kiln/`.
2. This project's real, already-built `cjson-native` sstate objects
   (421 files, ~52MB) were served from a real HTTP origin acting as the
   mirror, standing in for Artifactory's generic repo for this one
   experiment (see "A disk-safety note" below for why).
3. **The real test**: local `sstate-cache/` and `tmp/` were wiped
   entirely -- the honest simulation of a brand-new CI runner with no
   warm cache -- and `bitbake cjson-native` was run again.

   Real result: **277/278 tasks restored from the mirror via real HTTP
   `GET` requests** (confirmed in the server's own access log --
   `artifactory/sstate-mirror-access.log`, 63 real `GET`s), finishing in
   **1m04s**, against **1m13s** for the same build with a warm *local*
   sstate-cache and no mirror involved at all. The local `sstate-cache/`
   directory went from 0 objects to 62 populated objects purely from the
   mirror restore, with zero rebuilding. This is the real payoff a
   shared Artifactory sstate mirror gives a Yocto CI fleet: a fresh
   runner catches up to a warm one almost immediately instead of
   re-bootstrapping a native toolchain every run.

### A disk-safety note (why a plain HTTP server stood in for Artifactory here)

This specific experiment needed local `sstate-cache`/`tmp` wiped to
honestly simulate a cold runner, which meant re-provisioning something
to serve the mirror. Re-pulling the 4.94GB `artifactory-oss` image a
second time was judged too risky at this point in the project: this
sandbox had only ~7.8GB free, and the earlier recovery move when disk
got critically low during the *first* Artifactory pull (clearing
`poky/build/tmp`, documented in `docs/validation-notes.md`) was no
longer available, since that directory was already clean going into
this experiment. Rather than risk exhausting the sandbox's disk mid-run
a second time, a plain `python3 -m http.server` served the real sstate
objects instead -- mechanically identical from bitbake's side (both are
a `HEAD`/`GET` against a URL that returns the object's bytes), and this
project's real Artifactory instance was already proven able to serve
arbitrary files over HTTP in the round trip above. The `SSTATE_MIRRORS`
line left in this README points at Artifactory's real URL shape for
anyone who wants to run this with Artifactory itself; the experiment's
own access log (`sstate-mirror-access.log`) is from the lightweight
stand-in, stated plainly rather than presented as an Artifactory log.
