from cxquality.policy import parse_numbered_policies, select_relevant_policies


def test_parse_numbered_policies_keeps_arabic_text() -> None:
    items = parse_numbered_policies(
        "1- يجب الرد بنفس لغة العميل.\n\n2- يجب توضيح رسوم الشحن للعميل."
    )
    assert [item.external_id for item in items] == ["1", "2"]
    assert "لغة العميل" in items[0].body


def test_select_relevant_policy() -> None:
    items = parse_numbered_policies(
        "1- يجب الرد بنفس لغة العميل.\n2- يجب توضيح رسوم الشحن والتكلفة الاجمالية."
    )
    selected = select_relevant_policies("كم رسوم الشحن والتكلفة كاملة؟", items, limit=1)
    assert selected[0].external_id == "2"

