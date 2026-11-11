import pytest

from musubi_gui.run_paths import job_folder


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("My training job", "My-training-job"),
        (' ../a:b/c\\d?*|<>" ', "a-b-c-d"),
        ("CON", "Job-CON"),
        ("nul.txt", "Job-nul.txt"),
        ("LPT¹", "Job-LPT¹"),
        ("...", "Job"),
        ("日光", "日光"),
        ("a" * 100, "a" * 60),
    ],
)
def test_job_folder_is_readable_and_windows_safe(name: str, expected: str) -> None:
    assert job_folder(name) == expected
