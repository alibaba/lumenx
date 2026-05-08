# Runtime Files

`tmp/lumenx-*.json` files are ephemeral launch manifests, not project data.

They are written by the local dev launcher and CI smoke scripts so helper
processes can discover the actual LumenX backend/frontend URLs after port
conflict resolution. Do not commit them, do not store user/project state in
them, and do not treat an old file as authoritative unless its `launcherPid`
is still alive and `startedAt` is fresh. It is safe to delete these files only
when no LumenX dev session is running.

`LUMENX_OUTPUT_DIR` selects the runtime output root for a launch. If unset,
the app still writes to the normal `output/` tree. CI/dev smoke jobs override
it to an isolated directory so their files do not mix with a real workspace.
Browser smoke jobs also write a scenario-specific `browser-smoke-*.json`
summary into the same output root so failures can be inspected without opening
the screenshot first. In CI, the `browser-e2e-smoke-summary-screenshots`
artifact uploads that JSON next to `browser-e2e-smoke-failure.png`.

Open the summary JSON first when diagnosing a failed smoke run. The highest
signal fields are `projectIds`, `backendUrl`, `frontendUrl`, `lastEndpoint`,
`dialogMessages`, `error`, and `screenshotPath`.

Current manifests:

- `tmp/lumenx-backend-dev.json`: backend URL, host, selected port and launcher PID.
- `tmp/lumenx-frontend-dev.json`: frontend URL, selected port, backend URL and launcher PID.
- `tmp/e2e-output-*`: isolated runtime data root for browser/API smoke jobs; safe to delete after the job exits and the place to inspect preserved smoke output on failure, including the browser smoke summary JSON and failure screenshot.
