"""Copy a Sync Module's USB clips into a folder, and keep the drive small.

Without a Blink plan, clips are only saved to a USB drive in a Sync Module 2.
The archive copies them into a folder Home Assistant may write to:

    <folder>/<camera>/<YYYY-MM-DD>/<camera>_<YYYY-MM-DD_HH-MM-SS>.mp4

(``..._<clip id>.mp4`` if that name is taken). A run reads the drive's clip
list, then copies each new clip: the module uploads it to Blink's cloud on
request, and the file is downloaded from there. A clip already in the folder
under its name is recognised and not copied again.

With "Delete USB clips older than N days", every complete run then deletes
older clips from the drive, but only clips that are provably archived: copied
to this folder (or found there by name), the file exists and is at least
``MIN_ARCHIVED_BYTES`` big. Deleting is permanent; the clip is also gone from
the Blink app.

A Sync Module does one job at a time, so a run copies or deletes one clip at a
time with a pause in between, never while a live view of the account is
starting or open (it stops and the next run carries on), at most
``MAX_CLIPS_PER_RUN`` clips per run, and each request to the module goes
through the coordinator's control lock, like snapshots and switches.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re
import string

from blinkpy import api as blink_api
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# A real Blink clip is hundreds of KB or more; error pages and cut-off downloads are a few KB.
MIN_ARCHIVED_BYTES = 50 * 1024
PAUSE_SECONDS = 5
MAX_CLIPS_PER_RUN = 50
MAX_ATTEMPTS = 3  # runs a clip may fail before it is skipped
MAX_REMEMBERED = 5000
MANIFEST_TIMEOUT = 90
CLIP_TIMEOUT = 180
DOWNLOAD_TRIES = 3
DELETE_TRIES = 3
RETRY_SECONDS = 3
MAX_DAYS = 3650


@dataclass
class UsbClip:
    id: str
    camera: str
    created: datetime
    size: int | None
    url: str  # path below Blink's base URL, for the "request" endpoint


class LiveViewStarted(Exception):
    """A live view started: stop here, the next run carries on."""


def safe_name(name):
    return re.sub(r"[^\w\- ]+", "_", str(name)).strip() or "camera"


def clip_paths(folder, camera, created, clip_id):
    """Where a clip is saved: <camera>_<time>.mp4, or ..._<clip id>.mp4 if taken."""
    local = dt_util.as_local(created)
    cam = safe_name(camera)
    base = Path(folder) / cam / local.strftime("%Y-%m-%d")
    stem = f"{cam}_{local.strftime('%Y-%m-%d_%H-%M-%S')}"
    return base / f"{stem}.mp4", base / f"{stem}_{clip_id}.mp4"


def parse_manifest(sync, account_id, answer):
    """The clips in a manifest answer, with their request URLs."""
    manifest_id = answer["manifest_id"]
    template = string.Template(blink_api.local_storage_clip_url_template()).substitute(
        account_id=account_id,
        network_id=sync.network_id,
        sync_id=sync.sync_id,
        manifest_id=manifest_id,
        clip_id="$clip_id",
    )
    names = getattr(sync, "_names_table", None) or {}
    clips = []
    for row in answer.get("clips") or ():
        try:
            created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            clip_id = str(int(row["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        camera = names.get(row.get("camera_name"), row.get("camera_name")) or "camera"
        size = row.get("size")
        clips.append(
            UsbClip(
                clip_id,
                str(camera),
                created,
                int(size) if isinstance(size, int | float) else None,
                string.Template(template).substitute(clip_id=clip_id),
            )
        )
    clips.sort(key=lambda clip: clip.created)
    return clips


class UsbArchive:
    """Archive and delete one account's USB clips."""

    def __init__(self, hass, coordinator, entry_id, options):
        self.hass = hass
        self.coordinator = coordinator
        self.folder = options["archive_folder"]
        self.hours = options["archive_hours"]
        self.delete_days = options["usb_delete_days"]
        self.store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.usb_archive")
        self.lock = asyncio.Lock()
        self.saved = {}  # clip id -> path relative to the folder
        self.failures = {}  # clip id -> failed runs
        self.deleted = []  # clip ids deleted from the drive
        self._other_folders = {}
        self.listeners = set()
        self.status = {
            "last_clip": None,
            "last_saved_at": None,
            "saved_total": 0,
            "saved_last_run": 0,
            "last_run": None,
            "last_result": None,
            "last_error": None,
            "deleted_last_run": 0,
            "deleted_total": 0,
            "last_delete_error": None,
        }

    @property
    def enabled(self):
        return bool(self.folder)

    async def load(self):
        data = await self.store.async_load() or {}
        self._other_folders = dict(data.get("folders") or {})
        mine = self._other_folders.pop(self.folder, {})
        self.saved = {str(k): str(v) for k, v in (mine.get("saved") or {}).items()}
        self.failures = {str(k): int(v) for k, v in (mine.get("failures") or {}).items()}
        self.deleted = [str(c) for c in mine.get("deleted") or ()]
        self.status["saved_total"] = int(mine.get("saved_total", 0))
        self.status["deleted_total"] = int(mine.get("deleted_total", 0))
        self.status["last_clip"] = mine.get("last_clip")
        self.status["last_saved_at"] = mine.get("last_saved_at")

    async def _save(self):
        keep = list(self.saved.items())[-MAX_REMEMBERED:]
        self.saved = dict(keep)
        self.deleted = self.deleted[-MAX_REMEMBERED:]
        folders = dict(self._other_folders)
        folders[self.folder] = {
            "saved": self.saved,
            "failures": self.failures,
            "deleted": self.deleted,
            "saved_total": self.status["saved_total"],
            "deleted_total": self.status["deleted_total"],
            "last_clip": self.status["last_clip"],
            "last_saved_at": self.status["last_saved_at"],
        }
        await self.store.async_save({"folders": folders})

    def _notify(self):
        for listener in tuple(self.listeners):
            listener()

    # ---- the Sync Module ------------------------------------------------

    def _usb_syncs(self):
        return [
            sync
            for sync in (getattr(self.coordinator.api, "sync", None) or {}).values()
            if getattr(sync, "local_storage", False)
        ]

    def _check_live(self):
        if self.coordinator.live_active():
            raise LiveViewStarted

    async def _module(self, call, *args, **kwargs):
        """One request to the module, serialized with commands; never during live view."""
        self._check_live()
        async with self.coordinator.control_lock:
            self._check_live()
            return await call(*args, **kwargs)

    async def read_clips(self, sync):
        """The drive's current clip list (oldest first)."""
        async with asyncio.timeout(MANIFEST_TIMEOUT):
            first = await self._module(sync.poll_local_storage_manifest)
            request_id = first.get("id") if isinstance(first, dict) else None
            if not request_id:
                raise RuntimeError("Blink did not start the USB clip list")
            answer = await self._module(sync.poll_local_storage_manifest, request_id)
        if not isinstance(answer, dict) or "manifest_id" not in answer:
            raise RuntimeError("Blink did not return the USB clip list")
        return parse_manifest(sync, self.coordinator.api.account_id, answer)

    def _url(self, clip):
        return self.coordinator.api.urls.base_url + clip.url

    async def _copy(self, clip):
        """Have the module upload the clip, download it, save it. Returns the path."""
        blink = self.coordinator.api
        path, with_id = clip_paths(self.folder, clip.camera, clip.created, clip.id)
        target = path if not await self._exists(path) else with_id
        async with asyncio.timeout(CLIP_TIMEOUT):
            response = await self._module(blink_api.http_post, blink, self._url(clip))
            if isinstance(response, dict) and response.get("id"):
                await self._module(blink_api.wait_for_command, blink, response)
            data = None
            for attempt in range(DOWNLOAD_TRIES):
                video = await self._module(
                    blink_api.http_get, blink, self._url(clip), json=False
                )
                if getattr(video, "status", None) == 200:
                    data = await video.read()
                    break
                if attempt + 1 < DOWNLOAD_TRIES:
                    await asyncio.sleep(RETRY_SECONDS * (attempt + 1))
        if data is None:
            status = getattr(video, "status", "no answer")
            raise RuntimeError(f"download failed (HTTP {status})")
        if len(data) < MIN_ARCHIVED_BYTES:
            raise RuntimeError(f"download too small ({len(data)} bytes)")
        await self.hass.async_add_executor_job(self._write, target, data)
        return target

    @staticmethod
    def _write(target, data):
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        part.write_bytes(data)
        part.replace(target)

    async def _delete(self, clip):
        """Delete one clip from the drive; True on HTTP 200."""
        blink = self.coordinator.api
        url = self._url(clip).replace("/clip/request/", "/clip/delete/")
        status = None
        for attempt in range(DELETE_TRIES):
            answer = await self._module(blink_api.http_post, blink, url, json=False)
            status = getattr(answer, "status", None)
            if status == 200:
                return True, None
            if attempt + 1 < DELETE_TRIES:
                await asyncio.sleep(RETRY_SECONDS * (attempt + 1))
        return False, f"HTTP {status}" if status else "no answer"

    # ---- files ----------------------------------------------------------

    async def _exists(self, path):
        return await self.hass.async_add_executor_job(path.exists)

    async def _archived_file(self, clip):
        """The archive file of this clip and its size, if there is one."""

        def look():
            candidates = []
            if clip.id in self.saved:
                candidates.append(Path(self.folder) / self.saved[clip.id])
            candidates.extend(
                reversed(clip_paths(self.folder, clip.camera, clip.created, clip.id))
            )
            for path in candidates:
                if path.is_file():
                    return path, path.stat().st_size
            return None, 0

        return await self.hass.async_add_executor_job(look)

    def _remember(self, clip, path):
        self.saved[clip.id] = str(Path(path).relative_to(self.folder))
        self.failures.pop(clip.id, None)

    # ---- runs -----------------------------------------------------------

    async def run(self, hours=None, max_clips=MAX_CLIPS_PER_RUN, delete=True):
        """Copy new clips, then (after a complete run) delete archived old ones."""
        if not self.enabled:
            return {"result": "archive is off (no folder set)"}
        if self.lock.locked():
            return {"result": "already running"}
        hours = self.hours if hours is None else hours
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        summary = {"result": "done", "saved": [], "failed": [], "remaining": 0}
        deleted_report = None
        async with self.lock:
            self.status["saved_last_run"] = 0
            budget = max_clips
            try:
                for sync in self._usb_syncs():
                    clips = await self.read_clips(sync)
                    todo = [
                        c
                        for c in clips
                        if c.created >= since
                        and c.id not in self.saved
                        and c.id not in self.deleted
                        and self.failures.get(c.id, 0) < MAX_ATTEMPTS
                    ]
                    for clip in todo:
                        found, size = await self._archived_file(clip)
                        if found is not None and size >= MIN_ARCHIVED_BYTES:
                            self._remember(clip, found)  # copied earlier, e.g. by 0.x
                            continue
                        if budget <= 0:
                            summary["remaining"] += 1
                            continue
                        budget -= 1
                        try:
                            path = await self._copy(clip)
                        except LiveViewStarted:
                            raise
                        except Exception as err:  # noqa: BLE001 - one clip must not end the run
                            self.failures[clip.id] = self.failures.get(clip.id, 0) + 1
                            reason = str(err) if isinstance(err, RuntimeError) else type(err).__name__
                            summary["failed"].append({"clip": clip.id, "camera": clip.camera, "reason": reason})
                            self.status["last_error"] = f"{clip.camera}: {reason}"
                        else:
                            self._remember(clip, path)
                            self.status["saved_total"] += 1
                            self.status["saved_last_run"] += 1
                            self.status["last_clip"] = str(path)
                            self.status["last_saved_at"] = dt_util.utcnow().isoformat()
                            summary["saved"].append(str(path))
                        await asyncio.sleep(PAUSE_SECONDS)
                    if delete and self.delete_days > 0 and not summary["remaining"]:
                        deleted_report = await self._delete_old(
                            clips, self.delete_days, False, MAX_CLIPS_PER_RUN
                        )
            except LiveViewStarted:
                summary["result"] = "paused: a live view started"
            except Exception as err:  # noqa: BLE001 - reported on the sensor
                reason = str(err) if isinstance(err, RuntimeError) else type(err).__name__
                summary["result"] = f"error: {reason}"
                self.status["last_error"] = reason
            if summary["remaining"]:
                summary["result"] = "more clips left for the next run"
            if deleted_report is not None:
                summary["deleted"] = len(deleted_report["deleted"])
            self.status["last_run"] = dt_util.utcnow().isoformat()
            self.status["last_result"] = summary["result"]
            await self._save()
        self._notify()
        return summary

    async def delete_old(self, older_than_days, dry_run=False, max_clips=MAX_CLIPS_PER_RUN):
        """blink_plus.delete_usb_clips: delete archived clips older than N days."""
        report = {"result": "done", "deleted": [], "skipped": [], "errors": [], "remaining": 0}
        if not self.enabled:
            report["result"] = "archive is off (no folder set)"
            return report
        if not 1 <= int(older_than_days) <= MAX_DAYS:
            report["result"] = f"error: older_than_days must be 1 to {MAX_DAYS}"
            return report
        if self.lock.locked():
            report["result"] = "busy: an archive run is in progress"
            return report
        async with self.lock:
            try:
                for sync in self._usb_syncs():
                    clips = await self.read_clips(sync)
                    part = await self._delete_old(clips, older_than_days, dry_run, max_clips)
                    for key in ("deleted", "skipped", "errors"):
                        report[key].extend(part[key])
                    report["remaining"] += part["remaining"]
                    max_clips -= len(part["deleted"]) + len(part["errors"])
                    if part["result"] != "done":
                        report["result"] = part["result"]
                        break
            except LiveViewStarted:
                report["result"] = "paused: a live view started"
            except Exception as err:  # noqa: BLE001
                reason = str(err) if isinstance(err, RuntimeError) else type(err).__name__
                report["result"] = f"error: {reason}"
            await self._save()
        self._notify()
        return report

    async def _delete_old(self, clips, older_than_days, dry_run, max_clips):
        """Delete provably archived clips older than N days from one drive."""
        report = {"result": "done", "deleted": [], "skipped": [], "errors": [], "remaining": 0}
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(older_than_days))
        if not dry_run:
            self.status["deleted_last_run"] = 0
        for clip in clips:
            if clip.created >= cutoff or clip.id in self.deleted:
                continue
            item = {
                "clip": clip.id,
                "camera": clip.camera,
                "created_at": clip.created.isoformat(),
            }
            path, size = await self._archived_file(clip)
            reason = None
            if clip.id not in self.saved and path is None:
                reason = "not archived"
            elif self.failures.get(clip.id) and clip.id not in self.saved:
                reason = "not archived: copying failed"
            elif path is None:
                reason = "archive file missing"
            elif size < MIN_ARCHIVED_BYTES:
                reason = f"archive file too small ({size} bytes)"
            if reason:
                report["skipped"].append({**item, "reason": reason})
                continue
            item["path"] = str(path)
            if dry_run:
                report["deleted"].append(item)
                continue
            if len(report["deleted"]) + len(report["errors"]) >= max_clips:
                report["remaining"] += 1
                continue
            try:
                ok, why = await self._delete(clip)
            except LiveViewStarted:
                report["result"] = "paused: a live view started"
                break
            if ok:
                self._remember(clip, path)
                self.deleted.append(clip.id)
                self.status["deleted_total"] += 1
                self.status["deleted_last_run"] += 1
                report["deleted"].append(item)
            else:
                self.status["last_delete_error"] = f"{clip.camera}: {why}"
                report["errors"].append({**item, "reason": why})
            await asyncio.sleep(PAUSE_SECONDS)
        return report

    async def close(self):
        """Wait briefly for a running copy to reach a safe point."""
        with suppress(TimeoutError):
            async with asyncio.timeout(1):
                async with self.lock:
                    pass
