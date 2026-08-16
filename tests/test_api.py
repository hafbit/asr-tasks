from __future__ import annotations

from asr_tasks.models import WorkerState, utcnow


def test_authentication_is_required(client) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        "/v1/transcription-jobs", json={"source_url": "https://example.com/a.mp4"}
    )
    assert response.status_code == 401


def test_upload_glossary_job_and_idempotency(client, app, auth_headers) -> None:  # type: ignore[no-untyped-def]
    asset_response = client.post(
        "/v1/assets",
        headers=auth_headers,
        files={"file": ("video.mp4", b"media", "video/mp4")},
    )
    assert asset_response.status_code == 201
    asset = asset_response.json()

    glossary_response = client.post(
        "/v1/glossaries",
        headers=auth_headers,
        json={
            "name": "产品词",
            "hotwords": ["万维灵枢"],
            "replacements": {"哈福比特": "hafbit"},
        },
    )
    assert glossary_response.status_code == 201

    payload = {
        "asset_id": asset["id"],
        "glossary_id": glossary_response.json()["id"],
    }
    first = client.post(
        "/v1/transcription-jobs",
        headers={**auth_headers, "Idempotency-Key": "same-request"},
        json=payload,
    )
    second = client.post(
        "/v1/transcription-jobs",
        headers={**auth_headers, "Idempotency-Key": "same-request"},
        json=payload,
    )
    assert first.status_code == 202
    assert second.json()["id"] == first.json()["id"]

    status = client.get(f"/v1/transcription-jobs/{first.json()['id']}", headers=auth_headers)
    assert status.json()["status"] == "queued"
    assert status.json()["queue_position"] == 0


def test_ready_requires_model_worker(client, app) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/health/ready").status_code == 503
    factory = app.state.session_factory
    with factory() as session:
        session.add(WorkerState(id="worker", model_ready=True, heartbeat_at=utcnow()))
        session.commit()
    assert client.get("/health/ready").status_code == 200


def test_cancel_queued_job(client, auth_headers) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        "/v1/transcription-jobs",
        headers=auth_headers,
        json={"source_url": "https://example.com/video.mp4"},
    )
    job_id = response.json()["id"]
    cancelled = client.post(f"/v1/transcription-jobs/{job_id}/cancel", headers=auth_headers)
    assert cancelled.json()["status"] == "cancelled"


def test_batch_submission_and_running_cancel(client, app, auth_headers) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        "/v1/transcription-jobs:batch",
        headers=auth_headers,
        json={
            "jobs": [
                {"source_url": "https://example.com/a.mp4", "client_request_id": "batch-a"},
                {"source_url": "https://example.com/b.mp4", "client_request_id": "batch-b"},
            ]
        },
    )
    assert response.status_code == 202
    assert len(response.json()["jobs"]) == 2

    job_id = response.json()["jobs"][0]["id"]
    with app.state.session_factory() as session:
        from asr_tasks.models import TranscriptionJob

        job = session.get(TranscriptionJob, job_id)
        assert job is not None
        job.status = "running"
        session.commit()
    cancelled = client.post(f"/v1/transcription-jobs/{job_id}/cancel", headers=auth_headers)
    assert cancelled.json()["status"] == "cancel_requested"
    status = client.get(f"/v1/transcription-jobs/{job_id}", headers=auth_headers).json()
    assert status["cancel_requested"] is True
