from spingi.core.events import EventLog


def test_events_roundtrip_jsonl(tmp_path):
    path = tmp_path / "r" / "events.jsonl"
    log = EventLog(run_id="r-1", path=path, clock=lambda: 1.5)
    log.emit("run.start", plan_id="p")
    log.emit("skill.end", skill="navigate", outcome="success", duration_s=1.2)
    again = EventLog.read(path)
    assert [e.kind for e in again] == ["run.start", "skill.end"]
    assert again[1].data == {"skill": "navigate", "outcome": "success", "duration_s": 1.2}
    assert again[0].ts == 1.5 and again[0].run_id == "r-1"


def test_count_and_find_filter_on_data():
    log = EventLog(run_id="r")
    log.emit("skill.end", skill="a", outcome="success")
    log.emit("skill.end", skill="b", outcome="recoverable")
    assert log.count("skill.end") == 2
    assert log.count("skill.end", outcome="success") == 1
    assert log.find("skill.end", skill="b")[0].data["outcome"] == "recoverable"
