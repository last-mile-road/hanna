import pytest

from core.caseid import check_digit, format_number, generate, is_valid, normalise


def test_known_luhn_check_digit():
    assert check_digit("79927398") == "2"
    assert is_valid("799273982")


def test_spaces_and_hyphens_are_accepted():
    assert normalise("799 273 982") == "799273982"
    assert is_valid("799-273-982")


def test_a_bad_check_digit_is_rejected():
    assert not is_valid("799273981")
    assert not is_valid("123456789")


def test_letters_and_wrong_length_are_rejected():
    assert not is_valid("79927398A")
    assert not is_valid("79927398")
    assert not is_valid("7992739820")


def test_a_payload_must_be_eight_digits():
    with pytest.raises(ValueError):
        check_digit("7992739")
    with pytest.raises(ValueError):
        check_digit("7992739A")


def test_display_is_grouped_three_three_three():
    assert format_number("799273982") == "799 273 982"
    assert format_number("799 273 982") == "799 273 982"


def test_generate_is_nine_digits_and_always_valid():
    for _ in range(200):
        number = generate()
        assert len(number) == 9
        assert number.isdigit()
        assert is_valid(number)
