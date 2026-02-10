from nanobot.agent.tools.todo.models import TodoItem
from nanobot.agent.tools.todo.storage import (
    TODO_AUTO_REVIEW_START_MARKER,
    TODO_DATA_START_MARKER,
    TodoStorage,
    now_iso,
)


def test_init_and_load_roundtrip(tmp_path) -> None:
    storage = TodoStorage(tmp_path)
    created = storage.init_store()
    loaded = storage.load_store()

    assert storage.todo_path.exists()
    assert created.meta.version == 1
    assert loaded.meta.version == 1
    assert loaded.items == []


def test_save_store_creates_backup(tmp_path) -> None:
    storage = TodoStorage(tmp_path)
    store = storage.init_store()

    # First save establishes initial file.
    storage.save_store(store)

    # Second save should create backup.
    store.items.append(
        TodoItem(
            id="T0001",
            title="Sample",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    storage.save_store(store)

    assert storage.todo_backup_path.exists()
    backup = storage.todo_backup_path.read_text(encoding="utf-8")
    assert TODO_DATA_START_MARKER in backup


def test_load_store_raises_on_missing_data_block(tmp_path) -> None:
    storage = TodoStorage(tmp_path)
    storage.memory_dir.mkdir(parents=True, exist_ok=True)
    storage.todo_path.write_text("# TODO only", encoding="utf-8")

    try:
        storage.load_store()
        assert False, "Expected load_store to raise ValueError"
    except ValueError as e:
        assert "TODO data block" in str(e)


def test_auto_review_block_is_idempotent(tmp_path) -> None:
    storage = TodoStorage(tmp_path)
    storage.ensure_auto_review_block()
    storage.ensure_auto_review_block()

    content = storage.heartbeat_path.read_text(encoding="utf-8")
    assert content.count(TODO_AUTO_REVIEW_START_MARKER) == 1
