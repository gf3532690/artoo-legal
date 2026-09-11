# Agent Note: Align the Windows offline-packaging script with build.sh

Status: implemented

## Problem

The offline deployment bundle can be produced by either `deploy/build.sh` (Linux / macOS)
or `deploy/build.ps1` (Windows). The two had drifted, and the Windows one is the one
that runs on this project's development machine:

1. **It did not ship `deploy/reset-knowledge-data.sh`** — the script that clears knowledge
   content when the Milvus topology changes. `build.sh` copies it with the comment
   "必须随包交付，否则运维在服务器上无脚本可执行", and `DEPLOY.md` (the operations
   manual) was missing for the same reason.
2. **`docker save` was called without `--platform`** while `build` / `pull` passed it.
   Saving a foreign-architecture image without the platform either fails
   (`no suitable export target found … does not provide the specified platform`) or, on
   the classic overlay2 image store, silently exports whatever single architecture the
   tag currently holds.
3. **Nothing removed a locally cached image of the wrong architecture before pulling.**
   On the classic store a tag holds exactly one architecture, so `docker pull
   --platform linux/arm64 postgres:16-alpine` does not convert an existing amd64 copy —
   the bundle would ship amd64 layers for an arm64 server. `build.sh` already handles this.

## Decision

Bring `build.ps1` in line with `build.sh`, without changing the bundle layout:

- `docker save` for both `app-images.tar` and `infra-images.tar` receives
  `--platform linux/<arch>` when `-Arch` is given, and both saves fail loudly on a
  non-zero exit.
- Before pulling each middleware image, inspect the local copy's architecture and remove
  it when it does not match the target.
- Copy `deploy/reset-knowledge-data.sh` (into `dist/deploy/`) and `deploy/DEPLOY.md`
  (into `dist/`), matching `build.sh`.

## Alternatives considered

**Only use `build.sh` and tell the packager to run it from WSL / a Linux host.**
Rejected: the development machine is Windows and `build.ps1` exists precisely so the
bundle can be produced there; leaving a known-wrong script in the tree invites someone to
ship an amd64 bundle to an arm64 server.

**Build a multi-platform tar with buildx and let the server pick.**
Rejected: the target is a single architecture, multi-platform tars are much larger, and
loading them needs extra flags on the server side.

## Consequences

Windows and Linux packaging now produce the same bundle contents, and packaging for a
non-native architecture actually yields that architecture.

Packaging overwrites the local `artoo-backend:latest` / `artoo-frontend:latest` tags and
the middleware tags with the target architecture — anyone who also runs an amd64 Artoo
locally from those tags has to rebuild or re-pull afterwards. The local 法条库 test stack
is unaffected because it uses the separate `artoo-*-:legal` tags.

## Verification

Environment checks on the packaging host: Docker Desktop with buildx 0.30.1, classic
`overlay2` image store, and working arm64 emulation (`docker run --platform linux/arm64
alpine uname -m` → `aarch64`); `docker save --platform` is accepted by this daemon.

The full arm64 bundle was **not** produced here — the packager asked to run it
personally — so the end-to-end result of this script is unverified until that run.
