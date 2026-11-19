# Blink Plus on your dashboards

From 1.1.0 Blink Plus brings its own dashboard pieces and loads them by itself. There is no Resource to add and no card code to write to start using it.

## What you get after installing

- **A "Blink Plus" page in the sidebar.** Every Blink Plus camera appears on it with its still picture, a **Start live view** button, its controls and its readings. Each Blink system has an arm/disarm button at the top of the page.
- **A "Blink Plus camera" card** you can add to any dashboard from the card picker, or as `custom:blink-plus-camera-card`.
- **The push-to-talk element** (`custom:blink-plus-ptt`), for dashboards that place it by hand. See [push-to-talk](NATIVE_PTT.md).

After installing or updating, restart Home Assistant and refresh the browser page once.

To remove the sidebar page, open the integration's **Configure** menu and switch off **Show the Blink Plus page in the sidebar**. The card stays available.

## The camera card

~~~yaml
type: custom:blink-plus-camera-card
entity: camera.example_camera
~~~

That is the whole configuration. The card finds everything else through the camera's device.

On the picture:

- **Player controls** from the browser: pause/play, the time bar, mute and volume, full screen. Video starts muted because browsers only autoplay muted video; use the player's mute button for sound.
- **Hold to talk**, while live video is playing.
- **A light button** for a Wired Floodlight or Mini 2. It shows the state Blink reports, and from 1.1.2 it works while live video is playing.
- **A pan/tilt pad** for cameras selected as having a pan/tilt mount in the integration's options, while live video is playing.

Under the picture:

- The camera's name, **Stop live view** (or **Start live view** when nothing is playing), **New picture**, **Record clip** and **Motion detection**.
- The camera's readings, such as battery, temperature, Wi-Fi signal and last recording, where the camera reports them.

The buttons on the picture are see-through until you point at or focus them.

### Options

All are optional.

| Option | Default | Meaning |
|--------|---------|---------|
| `autoplay` | `true` | Start live video when the card appears. `false` waits for **Start live view**. |
| `talk` | `true` | `false` hides Hold to talk. |
| `light` | found automatically | A light entity to use, or `false` to hide the button. |
| `pan_tilt` | found automatically | `false` hides the pan/tilt pad. |
| `controls` | `true` | `false` hides the name and buttons under the picture. |
| `info` | `true` | `false` hides the readings. |
| `overlay_opacity` | `0.6` | How solid the buttons on the picture are, from `0.1` to `1`. |
| `aspect_ratio` | `16 / 9` | Shape of the picture, as a CSS ratio. |
| `name` | camera name | Text shown under the picture. |
| `light_icon` | by light type | An `mdi:` icon for the light button. |

A battery camera wakes whenever live video starts. On a dashboard that shows several cameras, set `autoplay: false` on them, as the sidebar page does.

### Live video behaviour

- Starting takes about 10 to 20 seconds, as in the Blink app; a sleeping battery camera can take longer.
- **Stop live view** closes the video at once and keeps it closed until you start it again. The camera's session with Blink ends shortly afterwards, when Home Assistant releases the stream (about half a minute).
- Blink Plus ends a live session at the **Maximum live session** option. The card then shows **Start live view** again.
- A hidden browser tab stops its live video after 15 seconds and starts it again when you return.
- A busy camera can refuse a command during live view or recording. The card shows Blink's answer over the picture.

## How it is built

The card plays video in a plain `<video>` element. It asks Home Assistant for the camera's stream with the documented `camera/stream` websocket call and plays the HLS stream with a bundled copy of hls.js; browsers without Media Source Extensions, such as an iPhone, play HLS by themselves. Buttons call ordinary Home Assistant actions on the camera's own entities.

Nothing in the card or the page reaches into Home Assistant's internal camera player, so a Home Assistant frontend update cannot remove the controls. The parts of Home Assistant it does rely on are the custom card interface, `ha-card` and `ha-icon`, the entity list given to cards, and the frontend's support for loading an integration's script and sidebar page.

The integration registers its script with Home Assistant's frontend when it starts, with its version in the address so browsers fetch the new file after an update. Dashboards that still list `/blink_plus/frontend/blink-plus-ptt.js` under Resources keep working; that entry is no longer needed and can be removed.

hls.js is included unmodified under the Apache License 2.0 (`custom_components/blink_plus/frontend/hls.LICENSE`).

## Open a camera from a notification

A card with `autoplay: false` waits for a tap, so opening the dashboard never wakes a camera. A link with `?camera=<camera entity>&live=1` starts that card's live view right away (without `camera`, `live=1` starts every card on the page). For a doorbell notification on the Companion app:

```yaml
- action: notify.mobile_app_my_phone
  data:
    title: Doorbell
    message: Someone is at the front door
    data:
      url: /dashboard-intercom/door?camera=camera.front_door&live=1          # iOS
      clickAction: /dashboard-intercom/door?camera=camera.front_door&live=1  # Android
```

Run `blink_plus.start_live_view` in the same automation to wake the camera while the notification is on its way.
