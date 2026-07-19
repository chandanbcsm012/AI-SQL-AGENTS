from middleware.pii import contains_pii, get_vault, mask_text, unmask_text


def test_mask_and_unmask_round_trip():
    trace_id = "trace-1"
    text = "Contact asha.rao@example.com or +91-98765-43210 for details."

    masked = mask_text(text, trace_id)

    assert "asha.rao@example.com" not in masked
    assert "PII_EMAIL_1" in masked

    restored = unmask_text(masked, trace_id)
    assert restored == text


def test_mask_is_scoped_per_trace_id():
    mask_text("a@b.com", "trace-a")
    mask_text("c@d.com", "trace-b")

    vault = get_vault()
    assert vault.get_map("trace-a") != vault.get_map("trace-b")


def test_contains_pii_detects_email():
    assert contains_pii("email me at someone@example.com")
    assert not contains_pii("no personal data here")
