"""USB clip archive and deleting archived clips, against a fake Sync Module."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant.common import MockConfigEntry
from test_integration import (
    blink as blink,
)
from test_integration import (
    custom_integrations as custom_integrations,
)

from custom_components.blink_plus import usb_archive
from custom_components.blink_plus.usb_archive import MIN_ARCHIVED_BYTES, clip_paths

DOMAIN = "blink_plus"
VIDEO = b"\0" * (MIN_ARCHIVED_BYTES + 10)
NOW = datetime.now(timezone.utc)


def row(clip_id, age, camera="TestCamera"):
    return {
        "id": clip_id,
        "camera_name": camera,
        "created_at": (NOW - age).isoformat(),
        "size": 900,
    }


class FakeSync:
    """A Sync Module 2 with a USB drive."""

    def __init__(self, *rows):
        self.local_storage = True
        self.name = "Home"
        self.arm = False
        self.network_id = 11
        self.sync_id = 22
        self._names_table = {"TestCamera": "Test camera"}
        self.rows = list(rows)
        self.manifest_requests = 0

    async def poll_local_storage_manifest(self, request_id=None):
        if request_id is None:
            self.manifest_requests += 1
            return {"id": 5}
        return {"manifest_id": "m1", "clips": list(self.rows)}


class FakeBlinkApi:
    """The blinkpy api calls the archive makes, recorded."""

    def __init__(self):
        self.calls = []
        self.download_status = 200
        self.video = VIDEO
        self.delete_status = 200

    def local_storage_clip_url_template(self):
        return (
            "/api/v1/accounts/$account_id/networks/$network_id/sync_modules/$sync_id"
            "/local_storage/manifest/$manifest_id/clip/request/$clip_id"
        )

    async def http_post(self, blink, url, json=True):
        if "/clip/delete/" in url:
            self.calls.append(("delete", url.rsplit("/", 1)[1]))
            return SimpleNamespace(status=self.delete_status)
        self.calls.append(("request", url.rsplit("/", 1)[1]))
        return {"id": 99, "network_id": 11}

    async def wait_for_command(self, blink, response):
        return True

    async def http_get(self, blink, url, json=True):
        self.calls.append(("download", url.rsplit("/", 1)[1]))
        return SimpleNamespace(
            status=self.download_status, read=AsyncMock(return_value=self.video)
        )

    def kinds(self, kind):
        return [clip for what, clip in self.calls if what == kind]


@pytest.fixture
def api():
    fake = FakeBlinkApi()
    with (
        patch.object(usb_archive, "blink_api", fake),
        patch.object(usb_archive, "PAUSE_SECONDS", 0),
        patch.object(usb_archive, "RETRY_SECONDS", 0),
    ):
        yield fake


@pytest.fixture
def folder(hass, tmp_path):
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    return tmp_path / "archive"


@pytest.fixture
def drive(blink):
    blink.account_id = 33
    blink.urls = SimpleNamespace(base_url="https://rest.example.invalid")
    sync = FakeSync()
    blink.sync = {"Home": sync}
    return sync


def entry_with(hass, **options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="test@example.com",
        unique_id="test@example.com",
        data={"username": "test@example.com", "token": "not-real"},
        options={"archive_interval": 0, **options},
    )
    entry.add_to_hass(hass)
    return entry


async def started(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.archive


def put(folder, camera, created, clip_id, data=VIDEO, with_id=False):
    path = clip_paths(folder, camera, created, clip_id)[1 if with_id else 0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def test_archive_copies_new_clips_once(hass, blink, api, folder, drive):
    drive.rows = [row(1, timedelta(hours=2)), row(2, timedelta(hours=1)), row(3, timedelta(days=3))]
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    summary = await archive.run()
    assert summary["result"] == "done"
    assert api.kinds("request") == ["1", "2"]  # oldest first, clip 3 is too old
    assert len(summary["saved"]) == 2
    for saved in summary["saved"]:
        path = Path(saved)
        assert path.is_file() and path.stat().st_size == len(VIDEO)
        assert path.parent.parent.name == "Test camera"
    state = hass.states.get("sensor.blink_plus_clip_archive_last_archived_clip")
    assert state.attributes["saved_total"] == 2
    api.calls.clear()
    assert (await archive.run())["saved"] == []
    assert api.calls == []


async def test_clip_already_in_the_folder_is_not_copied(hass, blink, api, folder, drive):
    clip_row = row(7, timedelta(hours=1))
    drive.rows = [clip_row]
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    created = datetime.fromisoformat(clip_row["created_at"])
    put(folder, "Test camera", created, 7, with_id=True)  # named by an earlier version
    summary = await archive.run()
    assert summary["saved"] == [] and api.calls == []
    assert "7" in archive.saved


async def test_failed_copy_is_retried_then_skipped(hass, blink, api, folder, drive):
    drive.rows = [row(4, timedelta(hours=1))]
    api.download_status = 404
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    for _ in range(usb_archive.MAX_ATTEMPTS):
        summary = await archive.run()
        assert summary["failed"][0]["reason"] == "download failed (HTTP 404)"
    assert archive.failures["4"] == usb_archive.MAX_ATTEMPTS
    api.calls.clear()
    assert (await archive.run())["failed"] == []
    assert api.calls == []
    assert "4" not in archive.saved


async def test_too_small_download_is_not_saved(hass, blink, api, folder, drive):
    drive.rows = [row(4, timedelta(hours=1))]
    api.video = b"<html>error</html>"
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    summary = await archive.run()
    assert "too small" in summary["failed"][0]["reason"]
    assert not any(folder.rglob("*.mp4"))


async def test_per_run_cap(hass, blink, api, folder, drive):
    drive.rows = [row(i, timedelta(minutes=60 - i)) for i in range(1, 6)]
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    summary = await archive.run(max_clips=2)
    assert len(summary["saved"]) == 2 and summary["remaining"] == 3
    assert summary["result"] == "more clips left for the next run"


async def test_no_run_during_a_live_view(hass, blink, api, folder, drive):
    drive.rows = [row(1, timedelta(hours=1))]
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    archive.coordinator.live_active = lambda: True
    summary = await archive.run()
    assert summary["result"] == "paused: a live view started"
    assert drive.manifest_requests == 0 and api.calls == []


async def test_delete_only_provably_archived_clips(hass, blink, api, folder, drive):
    old = timedelta(days=40)
    drive.rows = [
        row(1, old + timedelta(minutes=4)),  # archived: deleted
        row(2, old + timedelta(minutes=3)),  # never archived
        row(3, old + timedelta(minutes=2)),  # archived, file gone
        row(4, old + timedelta(minutes=1)),  # archived, file too small
        row(5, timedelta(days=5)),  # too young
    ]
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    for clip_id, data in ((1, VIDEO), (3, VIDEO), (4, b"x" * 100)):
        created = datetime.fromisoformat(drive.rows[clip_id - 1]["created_at"])
        path = put(folder, "Test camera", created, clip_id, data)
        archive.saved[str(clip_id)] = str(path.relative_to(folder))
    clip_paths(folder, "Test camera", datetime.fromisoformat(drive.rows[2]["created_at"]), 3)[0].unlink()

    report = await archive.delete_old(30, dry_run=True)
    assert [d["clip"] for d in report["deleted"]] == ["1"]
    assert api.kinds("delete") == []

    report = await archive.delete_old(30)
    assert [d["clip"] for d in report["deleted"]] == ["1"]
    assert api.kinds("delete") == ["1"]
    reasons = {s["clip"]: s["reason"] for s in report["skipped"]}
    assert reasons == {
        "2": "not archived",
        "3": "archive file missing",
        "4": "archive file too small (100 bytes)",
    }
    assert archive.status["deleted_total"] == 1
    # Once deleted, never tried again.
    api.calls.clear()
    assert (await archive.delete_old(30))["deleted"] == []
    assert api.kinds("delete") == []


async def test_failed_copy_is_never_deleted(hass, blink, api, folder, drive):
    drive.rows = [row(1, timedelta(days=40))]
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    archive.failures["1"] = 1
    report = await archive.delete_old(30)
    assert report["skipped"][0]["reason"] == "not archived"
    assert api.kinds("delete") == []


async def test_delete_error_is_reported_and_retried_later(hass, blink, api, folder, drive):
    drive.rows = [row(1, timedelta(days=40))]
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    created = datetime.fromisoformat(drive.rows[0]["created_at"])
    put(folder, "Test camera", created, 1)
    api.delete_status = 500
    report = await archive.delete_old(30)
    assert report["errors"][0]["reason"] == "HTTP 500"
    assert api.kinds("delete") == ["1"] * usb_archive.DELETE_TRIES
    assert archive.status["last_delete_error"] == "Test camera: HTTP 500"
    api.delete_status = 200
    assert [d["clip"] for d in (await archive.delete_old(30))["deleted"]] == ["1"]


async def test_run_deletes_after_a_complete_archive_run(hass, blink, api, folder, drive):
    drive.rows = [row(1, timedelta(days=40)), row(2, timedelta(hours=1))]
    archive = await started(
        hass, entry_with(hass, archive_folder=str(folder), usb_delete_days=30)
    )
    created = datetime.fromisoformat(drive.rows[0]["created_at"])
    put(folder, "Test camera", created, 1)
    summary = await archive.run()
    assert summary["deleted"] == 1
    assert api.kinds("request") == ["2"] and api.kinds("delete") == ["1"]
    assert drive.manifest_requests == 1  # the delete reused the run's clip list


async def test_no_delete_after_an_incomplete_run(hass, blink, api, folder, drive):
    drive.rows = [row(1, timedelta(days=40)), row(2, timedelta(hours=2)), row(3, timedelta(hours=1))]
    archive = await started(
        hass, entry_with(hass, archive_folder=str(folder), usb_delete_days=30)
    )
    put(folder, "Test camera", datetime.fromisoformat(drive.rows[0]["created_at"]), 1)
    summary = await archive.run(max_clips=1)
    assert "deleted" not in summary
    assert api.kinds("delete") == []


async def test_actions(hass, blink, api, folder, drive):
    drive.rows = [row(1, timedelta(hours=1)), row(2, timedelta(days=40))]
    entry = entry_with(hass, archive_folder=str(folder))
    await started(hass, entry)
    answer = await hass.services.async_call(
        DOMAIN, "archive_clips", {"max_clips": 5}, blocking=True, return_response=True
    )
    assert len(answer["test@example.com"]["saved"]) == 1
    answer = await hass.services.async_call(
        DOMAIN,
        "delete_usb_clips",
        {"older_than_days": 30, "dry_run": True},
        blocking=True,
        return_response=True,
    )
    assert answer["test@example.com"]["skipped"][0]["reason"] == "not archived"
    with pytest.raises(Exception):  # noqa: B017 - schema rejects 0 days
        await hass.services.async_call(
            DOMAIN, "delete_usb_clips", {"older_than_days": 0}, blocking=True
        )


async def test_actions_need_a_folder(hass, blink, api, drive):
    await started(hass, entry_with(hass))
    with pytest.raises(ServiceValidationError, match="archive folder"):
        await hass.services.async_call(
            DOMAIN, "archive_clips", {}, blocking=True, return_response=True
        )
    assert hass.states.get("sensor.blink_plus_clip_archive_last_archived_clip") is None


async def test_memory_survives_a_restart(hass, blink, api, folder, drive):
    drive.rows = [row(1, timedelta(hours=1))]
    entry = entry_with(hass, archive_folder=str(folder))
    archive = await started(hass, entry)
    await archive.run()
    assert await hass.config_entries.async_unload(entry.entry_id)
    archive = await started(hass, entry)
    assert "1" in archive.saved and archive.status["saved_total"] == 1
    api.calls.clear()
    await archive.run()
    assert api.calls == []


async def test_options_reject_a_folder_home_assistant_may_not_use(hass, blink, tmp_path):
    entry = entry_with(hass)
    await started(hass, entry)
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"archive_folder": "/etc/blink"}
    )
    assert result["errors"] == {"archive_folder": "invalid_folder"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"archive_folder": str(tmp_path / "usb"), "usb_delete_days": 30},
    )
    assert entry.options["archive_folder"] == str(tmp_path / "usb")
    assert entry.options["usb_delete_days"] == 30
    await hass.async_block_till_done()


def test_sync_without_usb_drive_is_ignored():
    archive = usb_archive.UsbArchive.__new__(usb_archive.UsbArchive)
    archive.coordinator = SimpleNamespace(
        api=SimpleNamespace(
            sync={"a": SimpleNamespace(local_storage=False), "b": MagicMock(local_storage=True)}
        )
    )
    assert len(archive._usb_syncs()) == 1


async def test_live_view_starting_mid_run_stops_after_the_clip(
    hass, blink, api, folder, drive
):
    drive.rows = [row(1, timedelta(hours=2)), row(2, timedelta(hours=1))]
    archive = await started(hass, entry_with(hass, archive_folder=str(folder)))
    live = {"on": False}
    archive.coordinator.live_active = lambda: live["on"]
    real_write = archive._write

    def write_then_go_live(target, data):
        real_write(target, data)
        live["on"] = True

    archive._write = write_then_go_live
    summary = await archive.run()
    assert summary["result"] == "paused: a live view started"
    assert api.kinds("request") == ["1"]
    assert "1" in archive.saved and "2" not in archive.saved
