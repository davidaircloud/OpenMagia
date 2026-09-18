# Live playback, Magia and OBS

Restart OpenMagia after updating, then reopen your project.

## Loop a set

Click **Loop** beside playback, then **Play**. The illuminated Loop button repeats the entire timeline from 0:00 to the last clip's end, including audio lanes and intentional gaps. The preference is remembered in this browser. Looping is preview-only and does not repeat exports. Turn Loop off to stop at the end. Seeking and wrapping resynchronize the audio sources; overlapping instances of the same source have independent audio players.

## Send the live edit to OBS

1. Click **Preview ↗** in playback, or **Plugins → Development → Load bundled OBS Output**, approve its one permission, then open **OBS Output** and click **Open program window**.
2. In OBS on macOS, add **macOS Screen Capture**, set Method to **Window Capture**, and select **[Safari] Preview · project name** (or your browser). Disable Show cursor. On Windows or Linux, use Window Capture.
3. Capture the OpenMagia editor browser's audio once. This is the complete timeline mix: all MP3s and video audio, including overlaps, clip gain, fades and loop boundaries. On supported macOS versions, macOS Screen Capture can include application audio; OBS 30+ also provides macOS Audio Capture on macOS 13+. On Windows use application audio capture when available; on Linux use the appropriate output-device audio source. Do not create one OBS Media Source per song.
4. Add a separate **Audio Input Capture** source in OBS, name it **Link 2 Pro Audio**, and choose the **Insta360 Link 2 Pro** microphone. Keep its gain, mute and monitoring in OBS so the live voice remains independent from the OpenMagia music mix.
5. Keep both browser windows open and unminimized. Use a visible editor window during a set. Double-click the program picture for fullscreen, or crop the browser title bar in OBS and fit the source to your canvas. Check both the OpenMagia and microphone meters before recording, and avoid duplicate capture of either source.

The `/Preview` window is titled **Preview · project name** and contains only the composed picture, with aspect-preserving black letterboxing. Audio remains in the editor, without a second speaker playback. Effects, overlays, audio fades, volume, mute, play/pause, seeking and looping follow the editor. Changing project aspect updates the output resolution. Closing the editor closes the preview window. If popups are blocked, allow them for the local OpenMagia address and retry **Preview ↗** directly.

Do not paste the editor URL into OBS Browser Source to mirror a live session: that starts a separate editor/player inside OBS. This plugin uses local window capture and requires no OBS credentials, WebSocket service, virtual camera driver or cloud connection.

OBS references: [Sources guide](https://obsproject.com/kb/sources-guide), [macOS Screen Capture](https://obsproject.com/kb/macos-screen-capture-source), [macOS audio capture](https://obsproject.com/kb/macos-desktop-audio-capture-guide).

## Magia's simpler workflow

Choose a **Treatment**, choose **Stack effects** or **Replace previous Magia treatment**, optionally add direction, and inspect the proposed clip changes. **Choose effects** expands a compact list of all eight effect families. Pacing, voice cleanup and loudness start off to avoid unexpectedly retiming a live set or suggesting export processing is audible live.

- **Stack effects** preserves the existing transform, fade, blur, mask and timing. Motion is added as another editable transform lane, using the same controls as **Animate → + Stack transform**. Enabled transform zooms multiply and their center offsets add; every lane keeps its own points and can be bypassed or removed. Color adjustments compound in the color controls. Magia preserves occupied transition edges instead of silently superseding a manual fade. Start and end transitions remain separate edge effects.
- Each transform lane and transition appears inside the clip with draggable keyframe diamonds. Clicking a transform point opens its shared Animate editor. Clip splitting preserves and splits every transform lane, including bypassed lanes. Preview, clean output and FFmpeg export compose the transform layers.
- **Replace** restores earlier Magia-owned values before applying the new treatment; unchecked types return to their pre-Magia values.
- Audio-track clips support fades, cleanup and loudness, including Selected clip scope. Applying effects without pacing preserves clip timing.
- Select a clip before opening Magia to see **On this clip → Effect stack**, with Edit and Restore/Remove controls. The Inspector has the same stack. Different effect families operate together. **Undo** steps back one application and restores both the clip values and Magia's treatment metadata.

Voice cleanup and loudness normalization run during FFmpeg export only and are labeled accordingly. Live output includes the browser mix, not those export-only processors. Color preview and FFmpeg export use their existing respective renderers; this change preserves their editable native fields rather than promising pixel-identical rendering.

## Verification and limits

Run:

```sh
python3 -m unittest tests.test_live_performance tests.test_nle_timeline tests.test_plugins
node tests/test_effect_logic.js
```

The checks cover independent transform composition and bypass, repeated Magia preserving manual transform/fade rows, split points, rendered transform-plus-fade pixels, additive color, preservation/restoration of other effect families, audio selected scope, merged processing settings, unchanged timing without pacing, empty-stack behavior, undo metadata, plugin grants, independent audio decoders, fade gain, and loop overshoot.

Manual verification on macOS with Safari and OBS 32.2.2: opened the clean program window, selected it in macOS Screen Capture, observed the rendered picture and an active audio meter. The sandboxed plugin load/permission/open flow and Magia apply/stack/undo were exercised in a disposable local project. No live stream was started. A short recording with your real set remains the practical pre-show check for routing and synchronization.

This remains a browser timeline player, not beat-synchronized or sample-accurate DJ transport. Browser background throttling, media decode/seek latency, and device capture latency can affect a set; keep the editor visible and test your intended clip formats and project resolution. The loop retains frame overshoot but does not promise an inaudible sample-perfect boundary.
