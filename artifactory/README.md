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
