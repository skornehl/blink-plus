<p align="center"><img src="docs/assets/banner.png" alt="Blink Plus: Your Blink cameras. Inside Home Assistant."></p>

# Blink Plus

Native Blink live video and audio, dashboard push-to-talk, local recording, cloud clip browsing, and camera controls in one Home Assistant custom integration. Setup and options are configured in the UI.

[Installation](#installation) | [Dashboard page and card](docs/DASHBOARD.md) | [Options](docs/CONFIGURATION.md) | [HA push-to-talk](docs/NATIVE_PTT.md) | [Talk API](docs/TWO_WAY_TALK.md) | [How it works](docs/ARCHITECTURE.md) | [Test results](docs/VALIDATION.md) | [Changelog](CHANGELOG.md)

**Current release: v1.1.3.** Requires Home Assistant 2026.11.0 or newer and a Blink account. This independent project uses unofficial Blink cloud APIs and is not affiliated with Amazon or Blink.

Live view and cloud browsing are enabled by default. A camera starts streaming when a player connects. Snapshot, recording, motion, arming, and enabled pan/tilt controls can operate your cameras; setup does not automatically arm the system or start recordings. Battery cameras still need to wake and contact Blink's cloud.

## Included features

- A ready-made **Blink Plus** page in the sidebar and a **Blink Plus camera** dashboard card, both working straight after installation with nothing to add under Resources.
- Live video in the card's own player with pause/play, time bar, mute and full screen, and Hold to talk, light, pan/tilt, snapshot, recording and motion controls on the same card.
- Native H.264 video with AAC audio through Home Assistant's stream integration.
- One shared upstream live session per camera, bounded sessions, reconnect cooldown, and cleanup on unload.
- A direct native MPEG-TS stream per camera for low-delay players and tools (`/api/blink_plus/live/<camera entity id>`, authenticated).
- Native HA dashboard push-to-talk, authenticated microphone sessions and in-memory audio encoding.
- An AAC talk API for other compatible clients (/api/blink_plus/talk/{entity_id}).
- Actions for automations: take a snapshot, open and hold a live view, stop it, and play an audio file through the camera speaker (`blink_plus.trigger_camera`, `start_live_view`, `stop_live_view`, `play_audio`).
- Cached camera images plus a button to request a fresh snapshot.
- A **Record local clip** button that saves MP4 video and audio to Home Assistant media storage.
- **Blink cloud clips** in the Media browser, grouped by account and camera, with thumbnails and seekable playback.
- A **USB clip archive** that copies the clips on a Sync Module 2's USB drive into a folder, and can delete archived clips from the drive after N days.
- Camera motion detection switches, and network arming as a switch and as an alarm panel.
- Motion, low battery, and connectivity binary sensors where reported.
- Temperature, calibrated temperature, battery voltage or bars, Wi-Fi signal, Sync Module signal, and last-recording sensors where supported. Unsupported data is omitted; temperature follows HA's unit settings.
- Optional left/right/up/down/home buttons for cameras with a compatible pan/tilt mount.
- An on/off **Floodlight** light entity for Blink Wired Floodlight cameras and a **Spotlight** entity for Blink Mini 2 cameras, showing the lamp state Blink reports.
- UI login, verification-code entry, reauthentication, configuration, and redacted diagnostics.
- Login renewal that retries through a Blink outage and asks you to sign in again only when Blink refuses the stored token. Your password and verification code are never stored.

## Fixed in 1.1.3

**Motion sensors that follow every clip.** The Motion binary sensors now turn on, for two minutes, whenever Blink lists a newer clip for the camera. Earlier versions relied on a short-lived library flag and missed most clips on installations that take snapshots on a timer. See [Motion sensors](docs/CONFIGURATION.md#motion-sensors).

## New in 1.1.0

**Works out of the box.** After installing and signing in, open **Blink Plus** in the sidebar: every camera is there with live view on request, Hold to talk, light and pan/tilt buttons, New picture, Record clip, Motion detection, its readings, and arm/disarm for each Blink system.

**One card for everything.** Add the **Blink Plus camera** card to any dashboard and choose a camera; the card finds the rest. It plays video in its own player with the browser's controls and does not depend on Home Assistant's internal camera player.

**No Resources step.** The integration loads its dashboard code by itself. See the [dashboard guide](docs/DASHBOARD.md).

## Added in 1.0.2

**Real light state.** The Floodlight entity now shows what Blink reports for the lamp, not the last command Home Assistant sent. It follows the lamp when its own timer switches it off and when motion or the Blink app switches it.

**Mini 2 spotlight.** Blink Mini 2 cameras get a **Spotlight** light entity with the same on/off control and reported state. See [Camera lighting](docs/CONFIGURATION.md#camera-lighting).

## Added in 1.0.1

**Fewer false sign-in prompts.** Blink Plus stores Blink's tokens, not your password. Previously, if Blink was unreachable, failing or throttling at the moment the token was renewed (for example while Home Assistant restarted), the integration could show **Reauthenticate** although nothing was wrong with your login. Blink Plus now renews the token itself: only a token Blink actually refuses asks for a new login, and everything else is retried automatically.

**Floodlight control.** Blink Wired Floodlight cameras get a **Floodlight** light entity with on/off control.

The version number moves from 0.3.0 to 1.0.1. There is no 1.0.0 release and nothing to migrate.

## Added in 0.3.0

Add a **Hold to talk** button directly to an HA camera dashboard. The included custom element uses your signed-in HA connection, captures the microphone while held and converts audio inside the integration. It works as an overlay on a picture-elements live camera card or as a separate card.

The [native push-to-talk guide](docs/NATIVE_PTT.md) includes the resource URL, YAML example, microphone permissions, HTTPS requirements and usage. The existing [AAC talk API](docs/TWO_WAY_TALK.md) remains available for other clients. Repeated holds use fixed speaker start/stop control IDs, and the button waits for stopping to finish before it can be held again.

## Installation

### HACS / Marketplace (recommended)

Blink Plus is in the HACS default catalog, so there is no repository to add.

1. Open HACS (called **Marketplace** on newer installations).
2. Search for **Blink Plus** and open it. [This link](https://my.home-assistant.io/redirect/hacs_repository/?owner=zperkd&repository=blink-plus&category=integration) opens the same page in your Home Assistant.
3. Choose **Download**, then restart Home Assistant.
4. Open **Settings > Devices & services > Add integration** and search for **Blink Plus**.
5. Enter your Blink account email and password, then the verification code if requested.
6. Open the integration's **Configure** menu to adjust polling, streaming, recording, cloud browsing, and optional pan/tilt settings.

HACS must already be installed. UI wording can vary by version.

### If Blink Plus is not in the search results

An older HACS installation, or one that has not refreshed its catalog yet, may not list Blink Plus. Add it as a custom repository instead:

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/zperkd/blink-plus` with category **Integration**.
3. Continue from step 2 of the list above.

See the [HACS custom repository instructions](https://www.hacs.xyz/docs/faq/custom_repositories/).

### Manual installation

Download the release's `blink-plus-v1.1.3.zip`. Extract its `custom_components/blink_plus` folder into your HA configuration directory, producing `/config/custom_components/blink_plus/manifest.json`. Restart HA, then follow steps 4-6 above. Do not place the enclosing repository folder inside `custom_components`.

### Moving from the built-in Blink integration

Blink Plus uses its own `blink_plus` domain and creates new entities. Existing dashboards and automations must be updated to those entities. Its network control is an **Armed switch** and an alarm panel per system. Disable the original Blink integration after checking the replacement to avoid duplicate polling and controls. No automatic migration is performed.

## Updating from an earlier version

Download 1.1.3 in HACS / Marketplace, restart Home Assistant and refresh the browser page once. The Blink Plus page appears in the sidebar; the option to hide it is in the integration's Configure menu. Existing Blink Plus entities, settings and the stored login are retained; you are not asked to sign in again. A Wired Floodlight camera gains its Floodlight entity and a Mini 2 its Spotlight entity after the restart. Live viewing, snapshots, recordings, cloud clips, motion/arming and pan/tilt remain available. Talk uses the existing camera entity and an open live session. Add the JavaScript module and button using the [native PTT guide](docs/NATIVE_PTT.md).

## Everyday use

Open **Blink Plus** in the sidebar, or add the **Blink Plus camera** card to a dashboard, to view live video with all of a camera's controls. A camera entity in any other Home Assistant card also works. Use **Take snapshot** for a fresh still; viewing the cached image does not itself request a new snapshot.

Press **Record local clip** to save an MP4. By default, files go into the first configured HA media directory under `blink_plus`. Local recordings persist and have no automatic retention policy. The camera's `last_local_recording` attribute identifies the most recent saved file.

Open **Media > Blink cloud clips**, choose your account, then a camera. This lists existing cloud clips available to your Blink account; it does not add cloud recording entitlement or browse Sync Module USB storage (see the USB clip archive in [Configuration](docs/CONFIGURATION.md#usb-clip-archive)). Local recording and Blink cloud storage are separate features.

Enable pan/tilt only for cameras with a compatible mount in **Configure**. Adjust step size and axis inversion there. Details and automation examples are in [Configuration](docs/CONFIGURATION.md).

## Performance and tested support

Seven cameras, including Mini, battery-camera, and doorbell classes, passed real HA browser playback tests. Final 20-second samples displayed approximately 15-30 fps at native resolution with decoded audio and no buffering events. Browser startup took **10.19-14.09 seconds**. This remains cloud-dependent live view; startup is not instantaneous.

Local MP4 recording was tested on all three camera classes. Cloud browsing listed 1,000 clips across seven camera folders and verified authenticated, seekable playback. Snapshot, motion/arming, and mount controls were exercised and original settings restored.

The automated suite contains **139 tests**, plus five opt-in live-test groups. See [Validation](docs/VALIDATION.md) for measurements and limits. Fresh login/MFA has automated coverage; live tests reused an existing session. Other models, phone playback, casting, and long-term reliability remain unverified.

Two-way talk was verified with a Mini-class camera using real voice and tones, including with the Blink app closed. Other camera models need independent speaker testing. The included HA dashboard element provides microphone capture. See [Native push-to-talk](docs/NATIVE_PTT.md).

## Current limits

Motion is polled rather than pushed: a Motion sensor turns on at the first poll after Blink lists the clip, up to one polling interval after the event. Light control is on/off only, for Wired Floodlight and Mini 2 cameras. A change made outside Home Assistant while the lamp was off appears at the next poll, not instantly. Battery floodlight accessories, brightness and the lamp's timer settings are not supported. Night vision, sensitivity, activity zones, remote clip settings, and browsing Sync Module USB clips in the Media browser are not implemented (the USB clip archive copies them into a folder instead). Camera-reported data varies by model and account. Blink cloud availability, subscription entitlements, and camera settings still apply.

Live sessions are time-limited to protect resources and batteries. Leaving a live card open can cause later reconnects; configure session duration and cooldown deliberately. Home Assistant's per-camera **Preload stream** setting is ignored for Blink Plus cameras, because it would keep waking them with nobody watching. For continuous surveillance, these cloud battery cameras have practical limits.

## Troubleshooting and support

- Slow startup: compare with the Blink app, check Wi-Fi and battery condition, and consult the measured startup range above.
- Missing sensors: wait for the next refresh; only actual reported values create sensors.
- Missing clips: check account entitlement, history window, clip limit, and cloud browsing options.
- Recording fails: ensure the selected directory is allowed by HA and recording duration plus 10 seconds fits within the live-session limit.
- Talk is silent or unavailable: wait for live video, check the AAC format and camera control permission, and use the [talk troubleshooting table](docs/TWO_WAY_TALK.md#http-interface).
- Expired session: complete HA's reauthentication flow. From 1.0.1 this prompt appears only when Blink refuses the stored token (for example after a password change or signing out all devices); an outage shows the entry as retrying instead.
- A light does not respond: from 1.1.2 it works while Blink Plus live view is open. If the Blink app or another viewer holds the camera's live view, or the camera is recording, Blink answers busy; try again afterwards. A lamp that switches itself off after a short time is following its own timer, set in the Blink app.

Download diagnostics from the integration menu and [open an issue](https://github.com/zperkd/blink-plus/issues). Include HA/Blink Plus versions, camera model, symptoms, and steps to reproduce. Review attachments before posting; never include passwords, verification codes, tokens, private clips, or unredacted account exports.

<details>
<summary>Temporary debug logging</summary>

Enable this only while reproducing an issue, then disable it:

```yaml
logger:
  logs:
    custom_components.blink_plus: debug
```

Review logs for private information before sharing. Avoid enabling verbose authentication-library logging in a public report.
</details>

## Development and license

Official HACS and hassfest validation and automated tests run in [GitHub Actions](https://github.com/zperkd/blink-plus/actions). See [Contributing](CONTRIBUTING.md) for test setup and release checks. Original project artwork is included locally for Home Assistant branding. Licensed under [MIT](LICENSE).
