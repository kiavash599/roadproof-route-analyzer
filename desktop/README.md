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

The target is selected from the build host:

- Windows: one-click, per-user NSIS installer (`.exe`).
- macOS: universal Apple Silicon/Intel installer (`.pkg`), disk image (`.dmg`),
  and update archive (`.zip`).

GitHub Actions stores installers as workflow artifacts for manual builds. A
desktop-packaging change on `main` publishes the version from `package.json`;
a tag such as `v1.0.0` can also create the matching GitHub Release.

## Signing and notarization

Unsigned builds are useful for internal testing but operating systems can show
security warnings. Production releases should configure these repository
secrets:

- `WINDOWS_CSC_LINK` and `WINDOWS_CSC_KEY_PASSWORD`
- `MACOS_CSC_LINK` and `MACOS_CSC_KEY_PASSWORD`
- `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID`

The workflow passes those values only to the packaging process. Certificates
and passwords must never be committed to the repository.
