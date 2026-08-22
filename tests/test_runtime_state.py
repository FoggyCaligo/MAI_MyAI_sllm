from __future__ import annotations

from pathlib import Path

from mai.runtime_state import PersistentChatJobStore, PersistentSessionStore


def test_session_store_hashes_token_and_persists_working_root(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    store = PersistentSessionStore(db, ttl_seconds=3600, default_root=tmp_path)
    token, session = store.create(user_id="owner", role="owner")
    assert token
    assert session.working_root == str(tmp_path.resolve())
    store.update_working_root(session_id=session.session_id, working_root=tmp_path / "project")

    refreshed = store.get_by_session_id(session.session_id)
    assert refreshed is not None
    assert refreshed.working_root == str((tmp_path / "project").resolve())
    store.close()

    reopened = PersistentSessionStore(db, ttl_seconds=3600, default_root=tmp_path)
    restored = reopened.get(token)
    assert restored is not None
    assert restored.user_id == "owner"
    assert restored.role == "owner"
    assert restored.working_root == str((tmp_path / "project").resolve())
    restored_by_id = reopened.get_by_session_id(session.session_id)
    assert restored_by_id == restored
    reopened.close()


def test_chat_job_store_marks_inflight_jobs_interrupted_after_reopen(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    store = PersistentChatJobStore(db)
    pending = store.create(user_id="owner", session_id="session", request={"message": "hello"})
    store.mark_running(pending.job_id)
    store.close()

    reopened = PersistentChatJobStore(db)
    restored = reopened.get_for(job_id=pending.job_id, user_id="owner")
    assert restored.status == "interrupted"
    assert restored.error == "server_restarted_during_execution"
    reopened.close()


def test_chat_jobs_are_user_scoped(tmp_path: Path) -> None:
    store = PersistentChatJobStore(tmp_path / "runtime.db")
    job = store.create(user_id="owner", session_id="session", request={"message": "hello"})
    try:
        store.get_for(job_id=job.job_id, user_id="other")
    except KeyError:
        pass
    else:
        raise AssertionError("foreign user must not read another user's chat job")
    store.close()
