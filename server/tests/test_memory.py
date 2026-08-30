# SPDX-License-Identifier: MIT
"""Conversation memory."""

from __future__ import annotations


async def test_turns_round_trip(memory):
    await memory.add_turn("walle-01", "hello", "hi there")
    await memory.add_turn("walle-01", "how are you", "good")
    turns = await memory.recent_turns("walle-01")
    assert [t.user_text for t in turns] == ["hello", "how are you"]


async def test_recent_turns_are_oldest_first(memory):
    """Chat APIs want chronological order; the query is newest-first."""
    for i in range(5):
        await memory.add_turn("walle-01", f"q{i}", f"a{i}")
    turns = await memory.recent_turns("walle-01", limit=3)
    assert [t.user_text for t in turns] == ["q2", "q3", "q4"]


async def test_devices_are_isolated(memory):
    await memory.add_turn("walle-01", "mine", "ok")
    await memory.add_turn("walle-02", "theirs", "ok")
    assert len(await memory.recent_turns("walle-01")) == 1
    assert (await memory.recent_turns("walle-02"))[0].user_text == "theirs"


async def test_facts_upsert(memory):
    await memory.set_fact("walle-01", "name", "Sam")
    await memory.set_fact("walle-01", "name", "Samantha")
    facts = await memory.get_facts("walle-01")
    assert facts == ["name: Samantha"]


async def test_forget_one_fact_and_all_facts(memory):
    await memory.set_fact("walle-01", "name", "Sam")
    await memory.set_fact("walle-01", "drink", "tea")
    assert await memory.forget("walle-01", "name") == 1
    assert await memory.get_facts("walle-01") == ["drink: tea"]
    assert await memory.forget("walle-01") == 1
    assert await memory.get_facts("walle-01") == []


async def test_clear_history_keeps_facts(memory):
    await memory.set_fact("walle-01", "name", "Sam")
    await memory.add_turn("walle-01", "hello", "hi")
    await memory.clear_history("walle-01")
    assert await memory.recent_turns("walle-01") == []
    assert await memory.get_facts("walle-01") == ["name: Sam"]


async def test_prune_keeps_the_newest(memory):
    for i in range(20):
        await memory.add_turn("walle-01", f"q{i}", "a")
    await memory.prune("walle-01", keep=5)
    turns = await memory.recent_turns("walle-01", limit=100)
    assert len(turns) == 5
    assert turns[-1].user_text == "q19"


async def test_events_are_an_audit_trail(memory):
    await memory.log_event("walle-01", "smarthome", "ok: light.desk_lamp turn_on")
    await memory.log_event("walle-01", "smarthome", "refused: lock.front_door")
    events = await memory.recent_events()
    assert len(events) == 2
    assert "refused" in events[0]["detail"]  # newest first
