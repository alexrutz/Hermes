"""The guarantee the whole system rests on: same input ⇒ byte-identical prefix.

If any test in this file fails, cache hits stop happening and the product is
worthless — these are not stylistic tests.
"""

from __future__ import annotations

import pytest

from app import prompts
from app.prefix import (
    PrefixViolation,
    PageRef,
    assert_prefix_invariants,
    build_messages,
    canonical_prefix_json,
    prefix_signature,
)

SYSTEM = prompts.GLOBAL_SYSTEM_PROMPT
LABEL = prompts.PAGE_LABEL_TEMPLATE
INSTRUCTION = prompts.COLLECTION_INSTRUCTION


def make_refs(n: int = 3) -> list[PageRef]:
    return [
        PageRef(
            sequence_index=i,
            page_number=i + 1,
            document_pages=n,
            filename="vertrag.pdf",
            mime_type="image/png",
            base64_data=f"AAAABBBB{i}",
            estimated_tokens=100 + i,
        )
        for i in range(n)
    ]


def build(question: str, refs=None, **kwargs):
    return build_messages(
        system_prompt=SYSTEM,
        page_refs=refs if refs is not None else make_refs(),
        collection_name="Verträge",
        collection_instruction_template=INSTRUCTION,
        label_template=LABEL,
        question=question,
        **kwargs,
    )


def test_identical_input_produces_identical_bytes():
    a = canonical_prefix_json(build("Wie hoch ist die Miete?"))
    b = canonical_prefix_json(build("Wie hoch ist die Miete?"))
    assert a == b


def test_repeated_builds_are_stable_across_many_iterations():
    reference = canonical_prefix_json(build("Frage"))
    for _ in range(50):
        assert canonical_prefix_json(build("Frage")) == reference


def test_different_questions_share_the_image_prefix_byte_for_byte():
    """The expensive part must be identical no matter what is asked."""
    a = build("Frage A")
    b = build("Eine ganz andere, viel längere Frage B?")
    refs = make_refs()
    image_part_count = 2 * len(refs)
    assert a[0] == b[0]
    assert a[1]["content"][:image_part_count] == b[1]["content"][:image_part_count]


def test_signature_ignores_question_and_instruction():
    refs = make_refs()
    sig = prefix_signature(system_prompt=SYSTEM, page_refs=refs, label_template=LABEL)
    assert (
        prefix_signature(system_prompt=SYSTEM, page_refs=refs, label_template=LABEL) == sig
    )


def test_signature_changes_when_image_bytes_change():
    refs = make_refs()
    base = prefix_signature(system_prompt=SYSTEM, page_refs=refs, label_template=LABEL)
    mutated = list(refs)
    mutated[1] = PageRef(**{**refs[1].__dict__, "base64_data": "DIFFERENT"})
    assert (
        prefix_signature(system_prompt=SYSTEM, page_refs=mutated, label_template=LABEL) != base
    )


def test_signature_changes_when_order_changes():
    refs = make_refs()
    base = prefix_signature(system_prompt=SYSTEM, page_refs=refs, label_template=LABEL)
    swapped = [refs[1], refs[0], refs[2]]
    assert (
        prefix_signature(system_prompt=SYSTEM, page_refs=swapped, label_template=LABEL) != base
    )


def test_signature_changes_when_system_prompt_changes():
    refs = make_refs()
    base = prefix_signature(system_prompt=SYSTEM, page_refs=refs, label_template=LABEL)
    assert (
        prefix_signature(system_prompt=SYSTEM + " ", page_refs=refs, label_template=LABEL)
        != base
    )


def test_appending_a_page_preserves_the_existing_prefix():
    """Append-only: the old branch must survive unchanged inside the new one."""
    short = make_refs(3)
    extended = short + [
        PageRef(
            sequence_index=3,
            page_number=4,
            document_pages=3,
            filename="anhang.pdf",
            mime_type="image/png",
            base64_data="NEWPAGE",
            estimated_tokens=120,
        )
    ]
    a = build("Frage", refs=short)[1]["content"]
    b = build("Frage", refs=extended)[1]["content"]
    assert b[: 2 * len(short)] == a[: 2 * len(short)]


def test_system_prompt_is_first_and_identical_across_collections():
    a = build_messages(
        system_prompt=SYSTEM,
        page_refs=make_refs(),
        collection_name="Verträge",
        collection_instruction_template=INSTRUCTION,
        label_template=LABEL,
        question="Q",
    )
    b = build_messages(
        system_prompt=SYSTEM,
        page_refs=make_refs(2),
        collection_name="Rechnungen",
        collection_instruction_template=INSTRUCTION,
        label_template=LABEL,
        question="Q",
    )
    assert a[0] == b[0] == {"role": "system", "content": SYSTEM}


def test_instruction_sits_after_the_image_block():
    messages = build("Frage")
    parts = messages[1]["content"]
    last_image = max(i for i, p in enumerate(parts) if p["type"] == "image_url")
    instruction_index = next(
        i for i, p in enumerate(parts) if p["type"] == "text" and "Sammlung" in p["text"]
        and "durchsuchst" in p["text"]
    )
    assert instruction_index > last_image


def test_history_never_precedes_the_images():
    messages = build(
        "Und die Kaution?",
        history=[("user", "Wie hoch ist die Miete?"), ("assistant", "1200 €")],
    )
    parts = messages[1]["content"]
    last_image = max(i for i, p in enumerate(parts) if p["type"] == "image_url")
    history_index = next(
        i for i, p in enumerate(parts) if p["type"] == "text" and "Gesprächsverlauf" in p["text"]
    )
    assert history_index > last_image
    # And the image block itself is untouched by the presence of history.
    without = build("Und die Kaution?")[1]["content"]
    assert parts[: last_image + 1] == without[: last_image + 1]


# ------------------------------------------------------------------ the guards
def test_guard_rejects_extra_message_before_the_user_turn():
    messages = build("Frage")
    broken = [messages[0], {"role": "user", "content": "alter Verlauf"}, messages[1]]
    with pytest.raises(PrefixViolation, match="genau 2 Nachrichten"):
        assert_prefix_invariants(broken, SYSTEM, 3)


def test_guard_rejects_modified_system_prompt():
    messages = build("Frage")
    messages[0]["content"] = SYSTEM + "\nHeute ist Montag."
    with pytest.raises(PrefixViolation, match="weicht vom globalen System-Prompt ab"):
        assert_prefix_invariants(messages, SYSTEM, 3)


def test_guard_rejects_text_injected_before_the_images():
    messages = build("Frage")
    messages[1]["content"].insert(0, {"type": "text", "text": "Kontext des Nutzers"})
    with pytest.raises(PrefixViolation):
        assert_prefix_invariants(messages, SYSTEM, 3)


def test_guard_rejects_timestamp_inside_the_image_block():
    messages = build("Frage")
    messages[1]["content"][0] = {
        "type": "text",
        "text": "<<< DOKUMENT: vertrag.pdf | Abgerufen 2024-05-01 12:00 >>>",
    }
    with pytest.raises(PrefixViolation, match="variablen Wert"):
        assert_prefix_invariants(messages, SYSTEM, 3)


def test_guard_rejects_uuid_inside_the_image_block():
    messages = build("Frage")
    messages[1]["content"][2] = {
        "type": "text",
        "text": "request 0123456789abcdef0123456789abcdef",
    }
    with pytest.raises(PrefixViolation, match="variablen Wert"):
        assert_prefix_invariants(messages, SYSTEM, 3)


def test_guard_rejects_a_missing_instruction_after_the_images():
    refs = make_refs(2)
    parts = []
    for ref in refs:
        parts.append({"type": "text", "text": "<<< DOKUMENT >>>"})
        parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64,X"}})
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": parts}]
    with pytest.raises(PrefixViolation, match="fehlt die Sammlungs-Instruktion"):
        assert_prefix_invariants(messages, SYSTEM, 2)


def test_guard_rejects_wrong_image_count():
    messages = build("Frage")
    with pytest.raises(PrefixViolation, match="Erwartet: 5 Bilder"):
        assert_prefix_invariants(messages, SYSTEM, 5)
