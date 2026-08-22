"""Hermetic tests for the bidirectional MCP live-chat mailbox."""
import threading
import time

import mcp_live as live


def _new(client="Claude"):
    result = live.connect(client, f"{client} live")
    assert result["ok"]
    return result["session_id"]


def test_connect_queue_wait_status_and_reply():
    events = []
    live.set_event_callback(events.append)
    sid = _new()
    queued = live.post_user_message("hello", "chat-1")
    waited = live.wait_for_message(sid, after_event_id=0, timeout_seconds=1)
    assert waited["event"] == "message" and waited["text"] == "hello"
    mid = waited["message_id"]
    status = live.publish_status(sid, "thinking", "Reading the request", mid)
    assert status["state"] == "thinking"
    reply = live.publish_reply(sid, "Hi from Claude", mid, final=True)
    assert reply["ok"] and reply["pending_messages"] == 0
    assert [event["kind"] for event in events][-2:] == ["status", "reply"]


def test_wait_wakes_when_ui_posts_message():
    sid = _new("ChatGPT")
    result = {}
    thread = threading.Thread(target=lambda: result.update(
        live.wait_for_message(sid, after_event_id=0, timeout_seconds=3)))
    thread.start()
    time.sleep(0.05)
    live.post_user_message("wake up")
    thread.join(timeout=2)
    assert result["event"] == "message" and result["text"] == "wake up"


def test_cancel_is_delivered_as_next_event():
    sid = _new()
    queued = live.post_user_message("long task")
    first = live.wait_for_message(sid, 0, 1)
    cancelled = live.cancel_message(sid, queued["message_id"])
    event = live.wait_for_message(sid, first["cursor"], 1)
    assert cancelled["ok"] and event["event"] == "cancel"
    assert event["message_id"] == queued["message_id"]


def test_partial_reply_can_append_then_finalize():
    sid = _new()
    mid = live.post_user_message("stream")["message_id"]
    live.publish_reply(sid, "Hello", mid, final=False)
    live.publish_reply(sid, " world", mid, final=False, append=True)
    seen = []
    live.set_event_callback(seen.append)
    live.publish_reply(sid, "!", mid, final=True, append=True)
    assert seen[-1]["text"] == "Hello world!" and seen[-1]["final"] is True


def test_stale_client_lease_expires_instead_of_eating_messages_forever():
    sid = _new("Claude")
    with live._LOCK:
        live._SESSIONS[sid]["last_seen"] -= live._SESSION_LEASE_SECONDS + 1
    assert live.active_session() is None or live.active_session()["session_id"] != sid
    assert live.session_status(sid)["connected"] is False


def test_clients_share_protocol_and_disconnect_cleanly():
    claude = _new("Claude")
    chatgpt = _new("ChatGPT")
    sessions = live.session_status()["connected_sessions"]
    assert {s["client_name"] for s in sessions} >= {"Claude", "ChatGPT"}
    assert live.active_session()["session_id"] == chatgpt
    assert live.disconnect(chatgpt)["ok"]
    assert live.active_session()["session_id"] == claude


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)}/{len(tests)} MCP live-chat tests passed")
