from spam_filter.signals import run_signals


def test_signal_1_positive():
    fields = {'x_gm_features': 'AQt7F2rLGlvxzGKTC1NaGADQ1A8RjTb6BMGUHcpBhWwzze9EE'}
    matched, reason = run_signals(fields)
    assert matched
    assert 'Signal 1' in reason


def test_signal_1_negative():
    fields = {'x_gm_features': 'AQt7F2rLGlvxzGKTClegitimateXYZ'}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_2_positive():
    fields = {'message_id': '<foo+g9W_ddh+QDR18-tkYxyw01Rtjxhzw1NaGADQbar@mail.gmail.com>'}
    matched, reason = run_signals(fields)
    assert matched
    assert 'Signal 2' in reason


def test_signal_2_negative():
    fields = {'message_id': '<CABWrandom@mail.gmail.com>'}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_3_positive():
    fields = {'in_reply_to': 'aograotnctng-jbbvdh.edu'}
    matched, reason = run_signals(fields)
    assert matched
    assert 'Signal 3' in reason


def test_signal_3_negative():
    fields = {'in_reply_to': 'cs.stanford.edu'}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_4_positive():
    fields = {'body_text': 'http://[::ffff:192.0.2.1]/go'}
    matched, reason = run_signals(fields)
    assert matched
    assert 'Signal 4' in reason


def test_signal_4_negative():
    fields = {'body_text': 'https://example.com'}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_5_positive_stranger():
    fields = {'to_addrs': ['stranger@gmail.com']}
    matched, reason = run_signals(fields)
    assert matched
    assert 'Signal 5' in reason


def test_signal_5_negative_my_address_with_tag():
    fields = {'to_addrs': ['skip.morrow.mobile+nyt@gmail.com']}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_5_negative_camel_tag():
    fields = {'to_addrs': ['skip.morrow.mobile+camel@gmail.com']}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_5_multi_recipient_my_address_present():
    fields = {'to_addrs': ['james@outlook.com', 'skip.morrow.mobile@gmail.com']}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_5_negative_stranger_at_non_personal_domain():
    fields = {'to_addrs': ['someone@github.com']}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_6_positive():
    fields = {'return_path': 'noReply_tuiftndq@fit001.com'}
    matched, reason = run_signals(fields)
    assert matched
    assert 'Signal 6' in reason


def test_signal_6_negative():
    fields = {'return_path': 'noreply@amazon.com'}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_7_positive():
    fields = {'body_text': '...?syclid=abc123...'}
    matched, reason = run_signals(fields)
    assert matched
    assert 'Signal 7' in reason


def test_signal_7_negative():
    fields = {'body_text': 'normal email body'}
    matched, _ = run_signals(fields)
    assert not matched


def test_signal_7_within_1000_char_slice_mcafee_template():
    # Mirrors the real McAfee-format spam body where syclid= lands around char 640 —
    # would be missed with the old (pre-incident) 500-char truncation.
    padding = 'x' * 600
    body = padding + ' reset your password ( https://example.fau.edu?syclid=abc )'
    truncated = body[:1000]
    fields = {'body_text': truncated}
    matched, reason = run_signals(fields)
    assert matched
    assert 'Signal 7' in reason


def test_no_signal_matches_legitimate_email():
    fields = {
        'x_gm_features': '',
        'message_id': '<CABWrandom@mail.gmail.com>',
        'in_reply_to': '',
        'body_text': 'Hi, just checking in about the project.',
        'to_addrs': ['skip.morrow.mobile@gmail.com'],
        'return_path': 'bounces.amazon.com',
    }
    matched, reason = run_signals(fields)
    assert not matched
    assert reason == ''
