# RoadProof desktop packaging

The desktop app packages the existing RoadProof GUI and its verified Vinext
worker. It starts a private loopback server on a random port, opens an isolated
Electron window, and does not expose Node.js APIs to page content.

## Local validation

```bash
npm run desktop:smoke
```

## Installer builds

```bash
npm run desktop:dist
```

The current target is Windows x64: a one-click, per-user NSIS installer
(`.exe`) with desktop and Start Menu shortcuts.

GitHub Actions stores installers as workflow artifacts for manual builds. A
desktop-packaging change on `main` publishes the version from `package.json`;
a tag such as `v1.0.0` can also create the matching GitHub Release.

## Signing

Unsigned builds are useful for internal testing but Windows can show a security
warning. Production releases should configure these repository secrets:

- `WINDOWS_CSC_LINK` and `WINDOWS_CSC_KEY_PASSWORD`

The workflow passes those values only to the packaging process. Certificates
and passwords must never be committed to the repository.
