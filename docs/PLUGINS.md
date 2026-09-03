# OpenMagia plugins

OpenMagia plugins are independent HTML/JavaScript extensions loaded from a local repository. Version 1 is a developer workflow; the in-app Store is intentionally marked **Coming soon**.

## Architecture and trust model

OpenMagia borrows the useful boundary from Figma’s plugin model: a manifest declares an entry point and capabilities, custom UI runs in an isolated iframe, and the iframe communicates with the editor through messages. OpenMagia does not execute plugin Python, Node, shell commands, or arbitrary host filesystem operations.

An enabled plugin can run two instances:

- **UI mode** when a user opens it from the floating Plugins button. The plugin appears in a draggable, resizable, modeless utility window; the editor remains interactive behind it.
- **Background mode** when it has `generation.events`, allowing lightweight notification workflows while the OpenMagia page remains open.

Every host operation is checked against the user-approved grant list. Plugins cannot grant themselves new capabilities. Removing a plugin only removes its registry entry; its independent source repository is untouched.

This architecture is informed by Figma's official documentation for [plugin runtime separation](https://developers.figma.com/docs/plugins/how-plugins-run/), [plugin manifests](https://developers.figma.com/docs/plugins/manifest/), and the [Plugin API](https://developers.figma.com/docs/plugins/). OpenMagia adapts those concepts to a local video editor; it does not claim Figma API compatibility.

## Create a plugin

Create `openmagia-plugin.json` at the root of a separate repository:

```json
{
  "manifestVersion": 1,
  "id": "com.example.my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "What this plugin does.",
  "author": {"name": "Your name", "url": "https://example.com"},
  "repository": "https://github.com/example/my-plugin",
  "license": "MPL-2.0",
  "ui": "index.html",
  "icon": "icon.svg",
  "cover": "cover.png",
  "permissions": ["project.read", "generation.read"]
}
```

`ui`, `icon`, and `cover` must resolve inside the plugin folder. IDs are lowercase reverse-domain identifiers and versions use semantic versioning. Cover artwork should use a 12:5 ratio; 1200×500 is recommended.

Load the folder using **Plugins → Development → Load plugin**, review its permissions, and enable it. Source edits are served without copying; reload the plugin UI to see changes.

## Bridge API

Copy `plugin-sdk/openmagia-plugin-sdk.js` into a plugin or implement the small `postMessage` contract directly. The SDK exposes:

```js
const openmagia = createOpenMagiaPlugin();
const session = await openmagia.ready;
const context = await openmagia.context.get();
const stop = openmagia.onGeneration(event => console.log(event));
await openmagia.storage.set({enabled: true});
await openmagia.notifications.send('email', 'Render complete', 'Scene 2 is ready.');
```

The initialization payload includes `mode` (`ui` or `background`), approved permissions, stored settings, project metadata, and the current generation snapshot. A background plugin should seed its own status cache from this snapshot and notify only on later changes.

### Permissions

| Permission | Capability |
|---|---|
| `project.read` | Active project identity and stable settings |
| `project.write` | Reviewed project mutations (reserved for API expansion) |
| `media.read` / `media.write` | Media metadata and mutations |
| `timeline.read` / `timeline.write` | Tracks, clips, selection, and edits |
| `generation.read` | Scene job state and progress |
| `generation.create` | Submit/cancel jobs (reserved for API expansion) |
| `generation.events` | Live queue/progress/ready/error events |
| `notifications.email` | SMTP email through host configuration |
| `notifications.imessage` | macOS Messages automation |
| `storage` | Per-plugin local settings |

Reserved permissions are valid manifest declarations but methods are added only after their input/output contracts are stable. This prevents the first API from promising unsafe arbitrary access.

## Events and logs

Generation events are derived from the same `/api/state` lifecycle used by the editor, so listening does not add inference work or query H3. Events include project identity, generation id/name/status/error, progress, ETA, and previous status.

Use `openmagia.log()` for diagnostics. Logs are newline-delimited JSON in OpenMagia’s local data directory and visible in **Plugins → Development → View logs**. Never log passwords, tokens, prompts containing private information, or media bytes.

## Notification example

The first external example lives beside the OpenMagia repository in `OpenMagia Plugins/generation-notifier`. It supports iMessage and SMTP email. Empty recipients create a safe dry-run entry. Messages automation is macOS-only and may trigger an OS consent dialog. SMTP users should use provider-issued app passwords.

## Review checklist for future Store submissions

- Manifest validates and uses the minimum permissions.
- UI is responsive and works in an isolated iframe.
- UI works in the modeless floating window without assuming it owns or blocks the editor.
- No dynamic code download, `eval`, hidden telemetry, credential logging, or undisclosed network access.
- Destructive actions require an explicit user gesture and confirmation.
- Background event handling is idempotent and does not duplicate notifications after initialization.
- Repository includes license, privacy notes, screenshots/cover, support link, and reproducible test instructions.
- Plugin remains usable when optional services are unavailable and clearly reports platform-specific features.

## Future API directions

Planned extensions include reviewed media processors, timeline tools, export destinations, prompt analyzers, caption/transcript workflows, asset integrations, and marketplace installation/signing. These should extend the capability table rather than expose a generic shell or unrestricted filesystem API.
