// Blink Plus camera card: live video in a player of its own, with the camera's controls on the picture.
//
//   type: custom:blink-plus-camera-card
//   entity: camera.example_camera
//
// The video is a plain <video> element with the browser's controls (pause/play, time bar, mute and
// volume, full screen). The stream comes from Home Assistant's documented websocket call
// `camera/stream` (HLS). Safari plays HLS by itself; other browsers use the bundled hls.js
// (frontend/hls.min.js, Apache-2.0). Nothing here reaches into Home Assistant's own camera player,
// so a frontend update cannot take the controls away.
//
// On the picture: Hold to talk (the bundled push-to-talk element), a light button for cameras with
// a Blink Plus light entity, and a pan/tilt pad for cameras with Blink Plus pan/tilt buttons. Each
// is found through the camera's device and can be switched off or set by hand in the card options.
// Under the picture: the camera's name, Stop/Start live view, New picture, Record clip and Motion detection, and the
// camera's readings (battery, temperature, signal, last recording) where the camera reports them.
//
// Options (all optional): talk, light, pan_tilt, controls, info: false hides that part;
// light: <light entity> picks the light by hand; autoplay: false waits for a tap (or a
// ?camera=<this entity>&live=1 link);
// overlay_opacity: 0.1-1 (default 0.6); aspect_ratio: CSS ratio (default "16 / 9"); name: text.
const HLS_URL = "/blink_plus/frontend/hls.min.js";
const START_WINDOW_MS = 50000; // a sleeping battery camera can take this long before video arrives
const RETRY_MS = 1500;
const MAX_LATENCY_S = 2.5; // further behind the camera than this: skip ahead to the newest video
const HIDDEN_STOP_MS = 15000; // a hidden tab keeps the camera awake for nobody: stop after this long
const PAN_TILT = [
  ["up", "mdi:chevron-up", "Tilt up"],
  ["left", "mdi:chevron-left", "Pan left"],
  ["home", "mdi:home-variant-outline", "Home position"],
  ["right", "mdi:chevron-right", "Pan right"],
  ["down", "mdi:chevron-down", "Tilt down"],
];
let hlsLoading = null;

function loadHls() {
  if (window.Hls) return Promise.resolve();
  if (!hlsLoading) {
    hlsLoading = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = HLS_URL;
      script.onload = resolve;
      script.onerror = () => {
        hlsLoading = null;
        reject(new Error("The video player could not be loaded"));
      };
      document.head.append(script);
    });
  }
  return hlsLoading;
}

class BlinkPlusCameraCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this.shadowRoot.innerHTML =
      "<style>" +
      ":host{display:block}" +
      "ha-card{overflow:hidden}" +
      ".stage{position:relative;width:100%;aspect-ratio:var(--bp-aspect,16 / 9);background:#000}" +
      "video,img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000}" +
      "video[hidden],img[hidden],.note[hidden],.overlay[hidden],.pad[hidden]{display:none}" +
      ".note{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;flex-direction:column;" +
      "align-items:center;gap:10px;max-width:80%;padding:10px 14px;border-radius:12px;text-align:center;" +
      "background:rgba(10,13,28,.72);color:#fff;font-size:14px;line-height:1.35}" +
      ".note button{font:inherit;color:#fff;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.35);" +
      "border-radius:18px;padding:6px 16px;cursor:pointer}" +
      ".overlay{position:absolute;opacity:var(--bp-overlay-opacity,.6);transition:opacity .15s}" +
      ".overlay:hover,.overlay:focus-within{opacity:1}" +
      ".talk{left:4%;top:6%}" +
      ".light{right:4%;top:6%}" +
      ".round{display:flex;align-items:center;justify-content:center;box-sizing:border-box;" +
      "width:min(11vw,46px);height:min(11vw,46px);padding:0;border-radius:50%;cursor:pointer;color:#fff;" +
      "background:rgba(10,13,28,.55);border:1px solid rgba(255,255,255,.28);" +
      "box-shadow:0 2px 10px rgba(0,0,0,.35);--mdc-icon-size:min(6vw,26px)}" +
      ".round:disabled{cursor:default;opacity:.5}" +
      ".round.on{color:#ffc766;border-color:rgba(255,199,102,.7)}" +
      ".round.home{background:rgba(142,162,255,.35)}" +
      ".pad{position:absolute;right:3%;top:50%;transform:translateY(-50%);display:grid;gap:6px;" +
      "grid-template-columns:repeat(3,min(11vw,46px));grid-template-rows:repeat(3,min(11vw,46px));" +
      "opacity:var(--bp-overlay-opacity,.6);transition:opacity .15s}" +
      ".pad:hover,.pad:focus-within{opacity:1}" +
      ".bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 12px}" +
      ".bar[hidden],.info[hidden]{display:none}" +
      ".name{flex:1 1 auto;min-width:8ch;font-weight:500;color:var(--primary-text-color)}" +
      ".chip{font:inherit;font-size:13px;line-height:1;padding:8px 12px;border-radius:16px;cursor:pointer;" +
      "color:var(--primary-text-color);background:transparent;border:1px solid var(--divider-color,#657080)}" +
      ".chip:disabled{cursor:default;opacity:.55}" +
      ".chip.on{color:var(--primary-color,#03a9f4);border-color:var(--primary-color,#03a9f4)}" +
      ".info{display:flex;flex-wrap:wrap;gap:4px 14px;padding:0 12px 10px;font-size:12px;" +
      "color:var(--secondary-text-color,#9aa4b2)}" +
      ".info b{font-weight:500;color:var(--primary-text-color)}" +
      ".pad .up{grid-area:1/2}.pad .left{grid-area:2/1}.pad .home{grid-area:2/2}.pad .right{grid-area:2/3}.pad .down{grid-area:3/2}" +
      "</style>" +
      "<ha-card><div class=\"stage\">" +
      "<video playsinline hidden></video><img alt=\"\" hidden>" +
      "<div class=\"note\" role=\"status\" hidden><span></span><button type=\"button\" hidden>Start live view</button></div>" +
      "<div class=\"overlay talk\" hidden></div>" +
      "<div class=\"overlay light\" hidden><button class=\"round\" type=\"button\"><ha-icon></ha-icon></button></div>" +
      "<div class=\"pad\" hidden></div>" +
      "</div><div class=\"bar\" hidden></div><div class=\"info\" hidden></div></ha-card>";
    const root = this.shadowRoot;
    this.card = root.querySelector("ha-card");
    this.image = root.querySelector("img");
    this.video = root.querySelector("video");
    this.note = root.querySelector(".note");
    this.noteText = root.querySelector(".note span");
    this.noteButton = root.querySelector(".note button");
    this.talkBox = root.querySelector(".talk");
    this.lightBox = root.querySelector(".light");
    this.lightButton = root.querySelector(".light button");
    this.lightIcon = root.querySelector(".light ha-icon");
    this.pad = root.querySelector(".pad");
    this.bar = root.querySelector(".bar");
    this.info = root.querySelector(".info");
    this.session = 0; // increases on every start/stop, so late answers from an old attempt are ignored
    this.live = false;
    this.played = false;
    this.wanted = false;
    this.hls = null;
    this.retryTimer = 0;
    this.catchUpTimer = 0;
    this.hiddenTimer = 0;
    this.busy = new Set();
    this.noteButton.addEventListener("click", () => this.start());
    this.lightButton.addEventListener("click", () => this.toggleLight());
    this.video.addEventListener("playing", () => this.onPlaying());
    this.video.addEventListener("error", () => this.onNativeError());
    this.onVisibility = () => this.visibilityChanged();
  }

  static getStubConfig(hass) {
    const camera = Object.keys(hass?.states || {}).find((id) => id.startsWith("camera."));
    return {entity: camera || "camera.example_camera"};
  }

  setConfig(config) {
    if (!config || typeof config.entity !== "string" || !config.entity.startsWith("camera.")) {
      throw new Error("Choose a Blink Plus camera entity");
    }
    const opacity = config.overlay_opacity === undefined ? 0.6 : Number(config.overlay_opacity);
    if (!(opacity >= 0.1 && opacity <= 1)) throw new Error("overlay_opacity must be between 0.1 and 1");
    for (const key of ["light"]) {
      const value = config[key];
      if (value !== undefined && value !== false && !(typeof value === "string" && value.startsWith("light."))) {
        throw new Error("light must be a light entity, or false to hide the button");
      }
    }
    const changed = this.config && this.config.entity !== config.entity;
    this.config = config;
    this.style.setProperty("--bp-overlay-opacity", String(opacity));
    this.style.setProperty("--bp-aspect", typeof config.aspect_ratio === "string" ? config.aspect_ratio : "16 / 9");
    this.found = null;
    if (changed) this.stop();
    this.render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this.talk) this.talk.hass = hass;
    this.render();
    if (first && this.isConnected) this.autoStart();
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return {columns: 12, min_columns: 6};
  }

  connectedCallback() {
    document.addEventListener("visibilitychange", this.onVisibility);
    if (this._hass) this.autoStart();
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this.onVisibility);
    clearTimeout(this.hiddenTimer);
    this.wanted = false;
    this.stop();
  }

  autoStart() {
    if (!this.config || this.live || (this.config.autoplay === false && !this.linkedLive())) {
      if (this.config && !this.live) this.say("", "Start live view");
      return;
    }
    this.start();
  }

  // A link such as /dashboard/door?camera=camera.front_door&live=1 (from a doorbell
  // notification) starts this card's live view even with autoplay: false. Without a
  // camera in the link, live=1 starts every card on the page.
  linkedLive() {
    const query = new URLSearchParams(window.location.search);
    if (query.get("live") !== "1") return false;
    const wanted = query.get("camera");
    return !wanted || wanted === this.config.entity;
  }

  visibilityChanged() {
    clearTimeout(this.hiddenTimer);
    if (document.hidden) {
      if (this.live) this.hiddenTimer = setTimeout(() => this.stop("Live view paused while this tab was hidden."), HIDDEN_STOP_MS);
    } else if (this.wanted && !this.live && this.config.autoplay !== false) {
      this.start();
    }
  }

  // ---------- which entities belong to this camera ----------
  related() {
    const hass = this._hass;
    if (!hass || !this.config) return {light: null, panTilt: null, snapshot: null, record: null, motion: null, readings: []};
    if (this.found && this.foundFor === hass.entities) return this.found;
    const camera = this.config.entity;
    const objectId = camera.split(".")[1];
    const registry = hass.entities || {};
    const device = registry[camera] && registry[camera].device_id;
    const sameDevice = (id) => (device ? registry[id] && registry[id].device_id === device : id.split(".")[1].startsWith(objectId));
    const ids = Object.keys(hass.states);
    let light = null;
    if (this.config.light === false) light = null;
    else if (typeof this.config.light === "string") light = this.config.light;
    else light = ids.find((id) => id.startsWith("light.") && sameDevice(id)) || null;
    let panTilt = null;
    if (this.config.pan_tilt !== false) {
      const buttons = {};
      for (const [action] of PAN_TILT) {
        const id = ids.find((candidate) => candidate.startsWith("button.") && candidate.endsWith("_pan_tilt_" + action) && sameDevice(candidate));
        if (id) buttons[action] = id;
      }
      if (Object.keys(buttons).length === PAN_TILT.length) panTilt = buttons;
    }
    const mine = ids.filter(sameDevice);
    const ending = (domain, suffix) => mine.find((id) => id.startsWith(domain + ".") && id.endsWith(suffix)) || null;
    this.found = {
      light,
      panTilt,
      snapshot: ending("button", "_take_snapshot"),
      record: ending("button", "_record_local_clip"),
      motion: ending("switch", "_motion_detection"),
      // Only with the device registry at hand: entity names alone could pull in another device's readings.
      readings: device ? mine.filter((id) => id.startsWith("sensor.") || id.startsWith("binary_sensor.")).sort() : [],
    };
    this.foundFor = hass.entities;
    return this.found;
  }

  // ---------- painting ----------
  render() {
    const hass = this._hass;
    if (!hass || !this.config) return;
    const state = hass.states[this.config.entity];
    const picture = state && state.attributes && state.attributes.entity_picture;
    if (!this.live || !this.played) {
      if (picture && this.image.dataset.src !== picture) {
        this.image.dataset.src = picture;
        this.image.src = hass.hassUrl ? hass.hassUrl(picture) : picture;
      }
      this.image.hidden = !picture;
    }
    const {light, panTilt} = this.related();
    this.renderFooter(state);

    // Hold to talk: the bundled element, loaded on first use
    const wantsTalk = this.config.talk !== false;
    if (wantsTalk && !this.talk) {
      if (customElements.get("blink-plus-ptt")) {
        this.talk = document.createElement("blink-plus-ptt");
        this.talk.setConfig({entity: this.config.entity});
        this.talk.hass = hass;
        this.talkBox.replaceChildren(this.talk);
      } else if (!this.talkLoading) {
        this.talkLoading = import("./blink-plus-ptt.js").then(() => this.render()).catch(() => {});
      }
    } else if (this.talk && (!wantsTalk || this.talk.config.entity !== this.config.entity)) {
      this.talkBox.replaceChildren();
      this.talk = null;
      if (wantsTalk) { this.render(); return; }
    }
    // Talk and pan/tilt act on the live session, so they appear once the video is playing.
    const watching = this.live && this.played;
    this.talkBox.hidden = !this.talk || !watching;

    // Light
    const lamp = light ? hass.states[light] : null;
    this.lightBox.hidden = !lamp;
    if (lamp) {
      const on = lamp.state === "on";
      const kind = light.endsWith("_spotlight") ? "Spotlight" : light.endsWith("_floodlight") ? "Floodlight" : "Light";
      this.lightIcon.setAttribute("icon", this.config.light_icon ||
        (kind === "Spotlight" ? "mdi:spotlight-beam" : kind === "Floodlight" ? "mdi:light-flood-down" : "mdi:lightbulb"));
      this.lightButton.classList.toggle("on", on);
      this.lightButton.disabled = lamp.state === "unavailable" || this.busy.has("light");
      this.lightButton.title = kind + (on ? ": on" : ": off");
      this.lightButton.setAttribute("aria-label", this.lightButton.title);
      this.lightButton.setAttribute("aria-pressed", on ? "true" : "false");
    }

    // Pan/tilt
    this.pad.hidden = !panTilt || !watching;
    if (panTilt && this.padFor !== panTilt) {
      this.padFor = panTilt;
      this.pad.replaceChildren(...PAN_TILT.map(([action, icon, title]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "round " + action;
        button.title = title;
        button.setAttribute("aria-label", title);
        const glyph = document.createElement("ha-icon");
        glyph.setAttribute("icon", icon);
        button.append(glyph);
        button.addEventListener("click", () => this.press(panTilt[action], button));
        return button;
      }));
    }
  }

  cameraName(state) {
    return this.config.name || (state && state.attributes && state.attributes.friendly_name) || this.config.entity;
  }

  chip(key, label, handler) {
    this.chips = this.chips || {};
    let chip = this.chips[key];
    if (!chip) {
      chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.addEventListener("click", handler);
      this.chips[key] = chip;
    }
    if (!this.busy.has(key)) chip.textContent = label;
    return chip;
  }

  renderFooter(state) {
    const hass = this._hass;
    const {snapshot, record, motion, readings} = this.related();
    const showBar = this.config.controls !== false;
    this.bar.hidden = !showBar;
    if (showBar) {
      if (!this.nameEl) {
        this.nameEl = document.createElement("span");
        this.nameEl.className = "name";
      }
      this.nameEl.textContent = this.cameraName(state);
      const parts = [this.nameEl];
      // Stop closes the video at once; the camera's session ends when Home Assistant lets go of the stream.
      const liveChip = this.chip("live", this.live ? "Stop live view" : "Start live view", () => {
        if (this.live) this.end();
        else this.start();
      });
      liveChip.classList.toggle("on", this.live);
      parts.push(liveChip);
      const usable = (id) => id && hass.states[id] && hass.states[id].state !== "unavailable";
      if (snapshot && hass.states[snapshot]) {
        const chip = this.chip("snapshot", "New picture", () => this.act("snapshot", "button", "press", snapshot, "Taking picture…"));
        chip.disabled = !usable(snapshot) || this.busy.has("snapshot");
        parts.push(chip);
      }
      if (record && hass.states[record]) {
        const chip = this.chip("record", "Record clip", () => this.act("record", "button", "press", record, "Recording…"));
        chip.disabled = !usable(record) || this.busy.has("record");
        parts.push(chip);
      }
      if (motion && hass.states[motion]) {
        const on = hass.states[motion].state === "on";
        const chip = this.chip("motion", on ? "Motion detection: on" : "Motion detection: off",
          () => this.act("motion", "switch", hass.states[motion].state === "on" ? "turn_off" : "turn_on", motion, "Changing…"));
        chip.classList.toggle("on", on);
        chip.setAttribute("aria-pressed", on ? "true" : "false");
        chip.disabled = !usable(motion) || this.busy.has("motion");
        parts.push(chip);
      }
      if (parts.length !== this.bar.children.length || parts.some((part, i) => this.bar.children[i] !== part)) {
        this.bar.replaceChildren(...parts);
      }
    }
    const lines = [];
    if (this.config.info !== false) {
      const cameraName = (state && state.attributes && state.attributes.friendly_name) || "";
      for (const id of readings) {
        const reading = hass.states[id];
        if (!reading || reading.state === "unavailable" || reading.state === "unknown") continue;
        let label = (reading.attributes && reading.attributes.friendly_name) || id;
        if (cameraName && label.startsWith(cameraName)) label = label.slice(cameraName.length).trim() || label;
        let value;
        try {
          value = hass.formatEntityState ? hass.formatEntityState(reading) : null;
        } catch (error) {
          value = null;
        }
        if (!value) value = reading.state + (reading.attributes.unit_of_measurement ? " " + reading.attributes.unit_of_measurement : "");
        lines.push([label.charAt(0).toUpperCase() + label.slice(1), value]);
      }
    }
    const text = JSON.stringify(lines);
    if (text !== this.infoText) {
      this.infoText = text;
      this.info.replaceChildren(...lines.map(([label, value]) => {
        const item = document.createElement("span");
        const strong = document.createElement("b");
        strong.textContent = value;
        item.append(label + ": ", strong);
        return item;
      }));
    }
    this.info.hidden = !lines.length;
  }

  async act(key, domain, service, entityId, working) {
    if (this.busy.has(key)) return;
    this.busy.add(key);
    const chip = this.chips[key];
    chip.disabled = true;
    chip.textContent = working;
    try {
      await this._hass.callService(domain, service, {entity_id: entityId});
    } catch (error) {
      this.flash((error && error.message) || "The camera did not respond");
    } finally {
      this.busy.delete(key);
      this.render();
    }
  }

  say(text, action) {
    this.noteText.textContent = text || "";
    this.noteText.hidden = !text;
    this.noteButton.hidden = !action;
    if (action) this.noteButton.textContent = action;
    this.note.hidden = !text && !action;
  }

  // ---------- controls ----------
  async toggleLight() {
    const {light} = this.related();
    const lamp = light && this._hass.states[light];
    if (!lamp || this.busy.has("light")) return;
    this.busy.add("light");
    this.render();
    try {
      await this._hass.callService("light", lamp.state === "on" ? "turn_off" : "turn_on", {entity_id: light});
    } catch (error) {
      this.flash((error && error.message) || "The light did not respond");
    } finally {
      this.busy.delete("light");
      this.render();
    }
  }

  async press(entityId, button) {
    if (button.disabled) return;
    button.disabled = true;
    try {
      await this._hass.callService("button", "press", {entity_id: entityId});
    } catch (error) {
      this.flash((error && error.message) || "The camera did not respond");
    } finally {
      button.disabled = false;
    }
  }

  flash(text) {
    if (this.live && this.played) {
      this.say(text);
      clearTimeout(this.flashTimer);
      this.flashTimer = setTimeout(() => { if (this.live && this.played) this.say(""); }, 5000);
    } else {
      this.say(text, this.live ? "" : "Start live view");
    }
  }

  // ---------- live video ----------
  async start() {
    if (!this._hass || !this.config) return;
    this.stop();
    const session = ++this.session;
    this.live = true;
    this.wanted = true;
    this.played = false;
    this.began = Date.now();
    this.say("Connecting to the camera… (Blink takes 10–20 seconds)");
    this.render();
    const video = this.video;
    video.muted = true; // browsers only autoplay muted video; unmute with the player's own button
    video.autoplay = true;
    video.playsInline = true;
    video.controls = true;
    try {
      const answer = await this._hass.callWS({type: "camera/stream", entity_id: this.config.entity, format: "hls"});
      if (session !== this.session) return;
      if (!answer || typeof answer.url !== "string") throw new Error("No live stream");
      this.streamUrl = this._hass.hassUrl ? this._hass.hassUrl(answer.url) : answer.url;
      // hls.js wherever the browser can run it (it keeps the picture close to live); browsers without
      // Media Source Extensions, such as an iPhone, play HLS by themselves.
      if (window.MediaSource || window.ManagedMediaSource) await loadHls().catch(() => {});
      if (session !== this.session) return;
      this.attach(session);
    } catch (error) {
      if (session === this.session) this.fail((error && error.message) || "");
    }
  }

  attach(session) {
    if (session !== this.session) return;
    const video = this.video;
    const Hls = window.Hls;
    if (Hls && Hls.isSupported()) {
      // Stay close to the live edge so a pan/tilt press shows quickly.
      const hls = new Hls({lowLatencyMode: true, backBufferLength: 10, maxLiveSyncPlaybackRate: 1.5,
        liveSyncDuration: 1, liveSyncOnStallIncrease: 0.2, liveMaxLatencyDuration: 3, maxBufferLength: 4});
      this.hls = hls;
      clearInterval(this.catchUpTimer);
      this.catchUpTimer = setInterval(() => {
        if (session !== this.session || this.hls !== hls || video.paused) return;
        const edge = hls.liveSyncPosition;
        if (this.played && edge && hls.latency > MAX_LATENCY_S && edge > video.currentTime) video.currentTime = edge;
      }, 2000);
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (session !== this.session || !data || !data.fatal) return;
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR && this.played) { hls.recoverMediaError(); return; }
        hls.destroy();
        if (this.hls === hls) this.hls = null;
        // Before the first picture Home Assistant may not have a playlist yet: try again for a while.
        if (!this.played && Date.now() - this.began < START_WINDOW_MS) {
          this.retryTimer = setTimeout(() => this.attach(session), RETRY_MS);
        } else {
          this.fail(this.played ? "Live view ended." : "");
        }
      });
      hls.loadSource(this.streamUrl);
      hls.attachMedia(video);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = this.streamUrl;
    } else {
      this.fail("This browser can't play live video");
      return;
    }
    video.hidden = false;
    const playing = video.play();
    if (playing && playing.catch) playing.catch(() => {});
  }

  onPlaying() {
    if (!this.live || this.played) return;
    this.played = true;
    this.image.hidden = true;
    this.say("");
    this.render();
  }

  onNativeError() {
    if (!this.live || this.hls) return;
    const session = this.session;
    if (!this.played && Date.now() - this.began < START_WINDOW_MS) {
      this.retryTimer = setTimeout(() => {
        if (session !== this.session) return;
        this.video.src = this.streamUrl;
        this.video.load();
      }, RETRY_MS);
    } else {
      this.fail(this.played ? "Live view ended." : "");
    }
  }

  end() {
    this.wanted = false; // stopped by hand: returning to the tab must not start it again
    this.stop("Live view stopped.");
  }

  fail(message) {
    this.wanted = false; // a failed or ended view restarts only when asked
    this.stop(message || "Live view could not start. The camera may be busy or asleep; try again in a minute.");
  }

  stop(message) {
    this.session += 1;
    this.live = false;
    this.played = false;
    clearTimeout(this.retryTimer);
    clearInterval(this.catchUpTimer);
    clearTimeout(this.flashTimer);
    if (this.hls) { this.hls.destroy(); this.hls = null; }
    const video = this.video;
    try { video.pause(); } catch (error) { /* not started */ }
    video.removeAttribute("src");
    try { video.load(); } catch (error) { /* nothing loaded */ }
    video.hidden = true;
    this.image.hidden = !this.image.getAttribute("src");
    if (this.config) {
      this.say(message || "", "Start live view");
      this.render();
    }
  }
}

// Loaded automatically by the integration; this also makes the push-to-talk element available.
import("./blink-plus-ptt.js").catch(() => {});

if (!customElements.get("blink-plus-camera-card")) {
  customElements.define("blink-plus-camera-card", BlinkPlusCameraCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "blink-plus-camera-card",
    name: "Blink Plus camera",
    description: "Live video with player controls, hold to talk, light and pan/tilt buttons.",
  });
}
