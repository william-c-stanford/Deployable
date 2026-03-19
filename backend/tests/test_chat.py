"""Tests for chat API endpoints and service layer."""

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.chat import ChatMessage, ChatSession
from app.services import chat_service


# ---------------------------------------------------------------------------
# Test DB setup
# ---------------------------------------------------------------------------

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_chat.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

DEMO_HEADERS = {"X-Demo-Role": "ops", "X-Demo-User-Id": "test-user-1"}
DEMO_HEADERS_2 = {"X-Demo-Role": "technician", "X-Demo-User-Id": "test-user-2"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_db():
    """Create tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# Service layer tests
# ---------------------------------------------------------------------------

class TestChatService:
    def test_create_session(self):
        db = TestingSessionLocal()
        try:
            session = chat_service.create_session(db, "user-1", title="Test session")
            db.commit()
            assert session.user_id == "user-1"
            assert session.title == "Test session"
            assert session.id is not None
        finally:
            db.close()

    def test_add_message(self):
        db = TestingSessionLocal()
        try:
            session = chat_service.create_session(db, "user-1")
            db.flush()
            msg = chat_service.add_message(
                db, session.id, "user-1", "user", "Hello!"
            )
            db.commit()
            assert msg.role == "user"
            assert msg.content == "Hello!"
            assert msg.session_id == session.id
        finally:
            db.close()

    def test_get_messages_ordered(self):
        db = TestingSessionLocal()
        try:
            session = chat_service.create_session(db, "user-1")
            db.flush()
            chat_service.add_message(db, session.id, "user-1", "user", "First")
            chat_service.add_message(db, session.id, "user-1", "assistant", "Second")
            chat_service.add_message(db, session.id, "user-1", "user", "Third")
            db.commit()

            messages = chat_service.get_messages(db, session.id, "user-1")
            assert len(messages) == 3
            assert messages[0].content == "First"
            assert messages[2].content == "Third"
        finally:
            db.close()

    def test_list_sessions(self):
        db = TestingSessionLocal()
        try:
            chat_service.create_session(db, "user-1", title="Session A")
            chat_service.create_session(db, "user-1", title="Session B")
            chat_service.create_session(db, "user-2", title="Other user")
            db.commit()

            sessions, total = chat_service.list_sessions(db, "user-1")
            assert total == 2
            assert len(sessions) == 2
        finally:
            db.close()

    def test_delete_session(self):
        db = TestingSessionLocal()
        try:
            session = chat_service.create_session(db, "user-1")
            db.flush()
            chat_service.add_message(db, session.id, "user-1", "user", "msg")
            db.commit()

            result = chat_service.delete_session(db, session.id, "user-1")
            db.commit()
            assert result is True

            # Verify gone
            assert chat_service.get_session(db, session.id, "user-1") is None
        finally:
            db.close()

    def test_delete_session_wrong_user(self):
        db = TestingSessionLocal()
        try:
            session = chat_service.create_session(db, "user-1")
            db.commit()
            result = chat_service.delete_session(db, session.id, "user-2")
            assert result is False
        finally:
            db.close()

    def test_send_user_message_creates_session(self):
        db = TestingSessionLocal()
        try:
            user_msg, asst_msg, session = chat_service.send_user_message(
                db, "user-1", "Hello there"
            )
            assert user_msg.role == "user"
            assert asst_msg.role == "assistant"
            assert session.title is not None
            assert user_msg.session_id == session.id
        finally:
            db.close()

    def test_send_user_message_existing_session(self):
        db = TestingSessionLocal()
        try:
            session = chat_service.create_session(db, "user-1", title="Existing")
            db.commit()

            user_msg, asst_msg, returned_session = chat_service.send_user_message(
                db, "user-1", "Follow-up", session_id=session.id
            )
            assert returned_session.id == session.id
        finally:
            db.close()

    def test_send_user_message_wrong_session(self):
        db = TestingSessionLocal()
        try:
            with pytest.raises(ValueError, match="not found"):
                chat_service.send_user_message(
                    db, "user-1", "msg", session_id=uuid.uuid4()
                )
        finally:
            db.close()

    def test_auto_title_truncation(self):
        long_text = "A" * 100
        title = chat_service.auto_title_from_content(long_text)
        assert len(title) <= 60
        assert title.endswith("...")

    def test_auto_title_short(self):
        title = chat_service.auto_title_from_content("Hi there")
        assert title == "Hi there"

    def test_user_scoping(self):
        """Messages from one user should not be visible to another."""
        db = TestingSessionLocal()
        try:
            session = chat_service.create_session(db, "user-1")
            db.flush()
            chat_service.add_message(db, session.id, "user-1", "user", "Secret")
            db.commit()

            # User 2 cannot see user 1's session
            assert chat_service.get_session(db, session.id, "user-2") is None
            msgs = chat_service.get_messages(db, session.id, "user-2")
            assert len(msgs) == 0
        finally:
            db.close()


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestChatAPI:
    def test_send_message_creates_session(self):
        resp = client.post(
            "/api/chat/messages",
            json={"content": "Hello!"},
            headers=DEMO_HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["user_message"]["role"] == "user"
        assert data["user_message"]["content"] == "Hello!"
        assert data["assistant_message"]["role"] == "assistant"
        assert data["session_id"] is not None

    def test_send_message_to_existing_session(self):
        # Create first
        resp1 = client.post(
            "/api/chat/messages",
            json={"content": "First"},
            headers=DEMO_HEADERS,
        )
        session_id = resp1.json()["session_id"]

        # Send to same session
        resp2 = client.post(
            "/api/chat/messages",
            json={"content": "Second", "session_id": session_id},
            headers=DEMO_HEADERS,
        )
        assert resp2.status_code == 201
        assert resp2.json()["session_id"] == session_id

    def test_send_message_invalid_session(self):
        resp = client.post(
            "/api/chat/messages",
            json={"content": "msg", "session_id": str(uuid.uuid4())},
            headers=DEMO_HEADERS,
        )
        assert resp.status_code == 404

    def test_send_message_empty_content(self):
        resp = client.post(
            "/api/chat/messages",
            json={"content": ""},
            headers=DEMO_HEADERS,
        )
        assert resp.status_code == 422  # Validation error

    def test_list_sessions(self):
        # Create sessions
        client.post("/api/chat/messages", json={"content": "A"}, headers=DEMO_HEADERS)
        client.post("/api/chat/messages", json={"content": "B"}, headers=DEMO_HEADERS)

        resp = client.get("/api/chat/sessions", headers=DEMO_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["sessions"]) == 2

    def test_list_sessions_pagination(self):
        for i in range(5):
            client.post(
                "/api/chat/messages",
                json={"content": f"Msg {i}"},
                headers=DEMO_HEADERS,
            )
        resp = client.get("/api/chat/sessions?skip=0&limit=2", headers=DEMO_HEADERS)
        data = resp.json()
        assert data["total"] == 5
        assert len(data["sessions"]) == 2

    def test_get_session_detail(self):
        resp1 = client.post(
            "/api/chat/messages",
            json={"content": "Hello"},
            headers=DEMO_HEADERS,
        )
        session_id = resp1.json()["session_id"]

        resp = client.get(f"/api/chat/sessions/{session_id}", headers=DEMO_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == session_id
        assert len(data["messages"]) == 2  # user + assistant

    def test_get_session_not_found(self):
        resp = client.get(
            f"/api/chat/sessions/{uuid.uuid4()}", headers=DEMO_HEADERS
        )
        assert resp.status_code == 404

    def test_get_messages(self):
        resp1 = client.post(
            "/api/chat/messages",
            json={"content": "Hello"},
            headers=DEMO_HEADERS,
        )
        session_id = resp1.json()["session_id"]

        resp = client.get(
            f"/api/chat/sessions/{session_id}/messages", headers=DEMO_HEADERS
        )
        assert resp.status_code == 200
        msgs = resp.json()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_create_session_explicitly(self):
        resp = client.post(
            "/api/chat/sessions",
            json={"title": "My Session"},
            headers=DEMO_HEADERS,
        )
        assert resp.status_code == 201
        assert resp.json()["title"] == "My Session"

    def test_delete_session(self):
        resp1 = client.post(
            "/api/chat/messages",
            json={"content": "Temp"},
            headers=DEMO_HEADERS,
        )
        session_id = resp1.json()["session_id"]

        resp = client.delete(
            f"/api/chat/sessions/{session_id}", headers=DEMO_HEADERS
        )
        assert resp.status_code == 204

        # Confirm gone
        resp2 = client.get(f"/api/chat/sessions/{session_id}", headers=DEMO_HEADERS)
        assert resp2.status_code == 404

    def test_delete_session_not_found(self):
        resp = client.delete(
            f"/api/chat/sessions/{uuid.uuid4()}", headers=DEMO_HEADERS
        )
        assert resp.status_code == 404

    def test_user_isolation(self):
        """User 2 cannot see User 1's sessions."""
        resp1 = client.post(
            "/api/chat/messages",
            json={"content": "Private"},
            headers=DEMO_HEADERS,
        )
        session_id = resp1.json()["session_id"]

        # User 2 tries to access
        resp2 = client.get(
            f"/api/chat/sessions/{session_id}", headers=DEMO_HEADERS_2
        )
        assert resp2.status_code == 404

        # User 2's list should be empty
        resp3 = client.get("/api/chat/sessions", headers=DEMO_HEADERS_2)
        assert resp3.json()["total"] == 0

    def test_no_auth_returns_401(self):
        resp = client.post("/api/chat/messages", json={"content": "Hi"})
        assert resp.status_code == 401

    def test_stub_reply_technician_keyword(self):
        resp = client.post(
            "/api/chat/messages",
            json={"content": "Show me available technicians"},
            headers=DEMO_HEADERS,
        )
        data = resp.json()
        assert "technician" in data["assistant_message"]["content"].lower()

    def test_stub_reply_project_keyword(self):
        resp = client.post(
            "/api/chat/messages",
            json={"content": "What projects need staffing?"},
            headers=DEMO_HEADERS,
        )
        data = resp.json()
        assert "project" in data["assistant_message"]["content"].lower() or \
               "staffing" in data["assistant_message"]["content"].lower()

    def test_stub_reply_greeting(self):
        resp = client.post(
            "/api/chat/messages",
            json={"content": "Hello!"},
            headers=DEMO_HEADERS,
        )
        data = resp.json()
        assert "hello" in data["assistant_message"]["content"].lower() or \
               "deployable" in data["assistant_message"]["content"].lower()

    def test_multi_message_conversation(self):
        """Verify multiple messages accumulate in one session."""
        resp1 = client.post(
            "/api/chat/messages",
            json={"content": "First message"},
            headers=DEMO_HEADERS,
        )
        session_id = resp1.json()["session_id"]

        client.post(
            "/api/chat/messages",
            json={"content": "Second message", "session_id": session_id},
            headers=DEMO_HEADERS,
        )
        client.post(
            "/api/chat/messages",
            json={"content": "Third message", "session_id": session_id},
            headers=DEMO_HEADERS,
        )

        resp = client.get(
            f"/api/chat/sessions/{session_id}/messages", headers=DEMO_HEADERS
        )
        msgs = resp.json()
        # 3 user messages + 3 assistant replies = 6
        assert len(msgs) == 6
