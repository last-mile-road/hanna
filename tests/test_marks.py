"""The HANNA number written in the card margin."""

from core import marks


def test_digits_reads_a_json_answer():
    assert marks.digits('{"number": "789731320"}') == "789731320"


def test_digits_removes_the_written_separators():
    assert marks.digits('{"number": "789-731-320"}') == "789731320"
    assert marks.digits("789 731 320") == "789731320"


def test_digits_rejects_anything_that_is_not_nine_digits():
    assert marks.digits('{"number": null}') is None
    assert marks.digits("") is None
    assert marks.digits(None) is None
    assert marks.digits("78973132") is None
    assert marks.digits("7897313201") is None


def test_read_number_is_skipped_on_a_page_without_a_strip(tmp_path):
    from core import marks as module

    class NeverCalled:
        def complete(self, *args, **kwargs):
            raise AssertionError("the model must not be called for this page")

    spec = module.load_spec("mozambique")
    assert spec is not None and spec["page"] == "front"
    assert (
        module.read_number(None, "mozambique", "back", NeverCalled(), {}, tmp_path)
        is None
    )
