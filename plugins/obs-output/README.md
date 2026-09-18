# OBS Output for OpenMagia

An unpacked OpenMagia v1 plugin. Load this directory in **Plugins → Development → Load plugin**, approve `output.open`, then open **OBS Output**. This folder is self-contained and can be copied into an independent repository.

The only capability is opening the host's fixed clean program window. It cannot control OBS, access arbitrary files, change the timeline, send messages, or open arbitrary URLs. No credentials, telemetry, stored settings, external code or network service are used. The documentation link is opened only by the user. Licensed under AGPL-3.0-only; see LICENSE.

The program window mirrors the live preview canvas, including visual effects and overlays, at project resolution with aspect-preserving letterboxing. Audio stays in the editor browser. Capture that browser application in OBS using one audio source; it contains the complete OpenMagia timeline mix, including multiple and overlapping audio clips, fades, volume, mute and loop playback. Add the live microphone as a separate OBS Audio Input Capture source so its gain, mute, monitoring and latency remain independent. For Insta360 Link 2 Pro, name the source `Link 2 Pro Audio` and select the camera's microphone device. Voice cleanup and loudness normalization are export-only. This is a local window-capture connection, not a Browser Source URL or a virtual camera.

Keep the editor and program windows open and unminimized. A visible editor window is recommended because browsers may throttle background or occluded tabs; browser playback is not sample-accurate DJ transport. Closing the program window ends its output; closing the editor closes its program window. Popup blocking is reported with a toolbar fallback.

## Verification

Run `python3 -m unittest tests.test_live_performance tests.test_plugins` from the OpenMagia root. In the app, open the plugin, click Open program window, play two clips with audio, turn on Loop, and confirm the timeline wraps and the program picture follows. In OBS, capture the program window and browser audio, record 20 seconds through a loop boundary, and review the recording. Check aspect changes, pause, seek, mute, effects, repeated media, closing/reopening output, and denying `output.open`.

Setup references: [OBS sources](https://obsproject.com/kb/sources-guide), [macOS audio](https://obsproject.com/kb/macos-desktop-audio-capture-guide). Support: [OpenMagia issues](https://github.com/davidaircloud/OpenMagia/issues).
