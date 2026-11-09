"""Page identity and the binary admission gate, measured on the real templates."""

from __future__ import annotations

import cv2

from core import admission, settings


def _specs():
    return admission.load_pages("mozambique")


def _front() -> str:
    return str(settings.COUNTRIES_DIR / "mozambique" / "template.jpg")


def _back() -> str:
    return str(settings.COUNTRIES_DIR / "mozambique" / "template.back.jpg")


def test_registry_declares_the_two_sides():
    specs = _specs()
    assert [spec.id for spec in specs] == ["front", "back"]
    assert [spec.page_no for spec in specs] == [1, 2]
    assert specs[0].required is True
    assert specs[1].required is False  # require_back controls it


def test_front_photo_matches_the_front():
    match = admission.match_image(_front(), _specs(), shot_no=1)
    assert match.spec is not None and match.spec.id == "front"
    assert match.inliers >= admission.MIN_INLIERS
    assert match.admitted


def test_back_photo_matches_the_back():
    match = admission.match_image(_back(), _specs(), shot_no=2)
    assert match.spec is not None and match.spec.id == "back"
    assert match.inliers >= admission.MIN_INLIERS
    assert match.admitted


def test_front_photo_does_not_admit_as_the_back():
    back_spec = next(spec for spec in _specs() if spec.id == "back")
    match = admission.match_image(_front(), [back_spec], shot_no=1)
    assert not match.admitted
    assert match.reasons


def test_a_rotated_front_is_admitted_and_the_rotation_is_reported(tmp_path):
    image = cv2.imread(_front())
    assert image is not None
    rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    path = tmp_path / "front_rot90.jpg"
    assert cv2.imwrite(str(path), rotated)

    match = admission.match_image(path, _specs(), shot_no=1)
    assert match.spec is not None and match.spec.id == "front"
    assert match.admitted
    assert abs(match.rotation) == 90


def test_front_only_is_a_missing_back():
    specs = _specs()
    front = admission.match_image(_front(), specs, shot_no=1)
    assessment = admission.assess([front], specs, require_back=True)
    assert not assessment.admitted
    assert assessment.pages_missing == ["back"]


def test_front_only_is_admitted_when_the_back_is_not_required():
    specs = _specs()
    front = admission.match_image(_front(), specs, shot_no=1)
    assessment = admission.assess([front], specs, require_back=False)
    assert assessment.admitted
    assert assessment.pages_missing == []


def test_two_fronts_are_flagged_as_duplicates_and_the_back_is_missing():
    specs = _specs()
    first = admission.match_image(_front(), specs, shot_no=1)
    second = admission.match_image(_front(), specs, shot_no=2)
    assessment = admission.assess([first, second], specs, require_back=True)
    assert not assessment.admitted
    assert assessment.duplicates == ["front"]
    assert assessment.pages_missing == ["back"]
    assert sum(1 for page in assessment.pages if page.spec and page.spec.id == "front") == 2


def test_a_photo_that_matches_no_side_is_reported():
    # A front matched only against the back template has no admitted side.
    back_spec = next(spec for spec in _specs() if spec.id == "back")
    match = admission.match_image(_front(), [back_spec], shot_no=1)
    assessment = admission.assess([match], [back_spec], require_back=False)
    assert not assessment.admitted
    assert match.reasons
