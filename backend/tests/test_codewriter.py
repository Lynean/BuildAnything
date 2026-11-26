from buildanything import firmware as fw, software as sw


def block(title, code):
    return fw.BlockOut(title=title, description="A described block of code for the review.", code=code)


def test_a_method_outside_its_class_is_caught():
    file = fw.FileOut(path="app.py", owner="Software", blocks=[block("App", "class App:\n    def __init__(self):\n        self.x = 1"), block("Draw", "def draw(self):\n    return self.x")])
    problems = fw.documentation_problems_host([file])
    assert any("outside any class" in p and "draw" in p for p in problems)


def test_a_repeated_device_folder_in_a_path_is_removed():
    files = fw.normalize_paths("laptop", [fw.FileOut(path="firmware/laptop/app.py", owner="Software", blocks=[]), fw.FileOut(path="core.py", owner="Software", blocks=[])])
    assert [f.path for f in files] == ["app.py", "core.py"]


def test_keeping_a_block_of_a_file_that_doesnt_exist_says_so(tmp_path):
    file = fw.FileOut(path="app.py", owner="Software", blocks=[fw.BlockOut(title="Setup", description="Imports and setup for the app.", code="", unchanged=True)])
    _files, problems = fw.merge_unchanged(tmp_path, "laptop", [file])
    assert "doesn't exist yet" in problems[0]


def test_text_files_are_allowed_and_written_as_they_are(tmp_path):
    req = fw.FileOut(path="requirements.txt", owner="Software", blocks=[block("Dependencies", "numpy<2\nopencv-python")])
    assert sw.path_problems([req]) == [] and fw.documentation_problems_host([req]) == []
    fw.write_files(tmp_path, "laptop", [req], host=True)
    assert (tmp_path / "firmware" / "laptop" / "requirements.txt").read_text() == "numpy<2\nopencv-python\n"


def test_edits_change_existing_files_like_a_patch_and_keep_descriptions(tmp_path):
    app = fw.FileOut(path="app.py", owner="Software", blocks=[block("Greeting", "def greet():\n    return 'hi'"), block("Main", "if __name__ == '__main__':\n    print(greet())")])
    fw.write_files(tmp_path, "laptop", [app], host=True)
    texts, problems = sw.apply_edits(tmp_path, "laptop", [sw.EditOut(path="firmware/laptop/app.py", find="return 'hi'", replace="return 'hello'")])
    assert problems == []
    parsed = fw.parse(texts["app.py"])
    assert parsed["blocks"][0]["code"].endswith("return 'hello'") and all(b["state"] == "documented" for b in parsed["blocks"])
    _texts, problems = sw.apply_edits(tmp_path, "laptop", [sw.EditOut(path="app.py", find="nothing like this", replace="x")])
    assert "occurs 0 times" in problems[0]
    _texts, problems = sw.apply_edits(tmp_path, "laptop", [sw.EditOut(path="app.py", find="return 'hi'", replace="return (")])
    assert any("after the edits" in p for p in problems)  # a broken result is refused before anything is written


def test_the_import_check_catches_a_program_that_cant_start(tmp_path):
    base = tmp_path / "firmware" / "laptop"
    base.mkdir(parents=True)
    (base / "app.py").write_text("import json\nclass App:\n    pass\nApp.draw(None)\n", encoding="utf-8")
    (base / "uses_missing.py").write_text("import some_package_that_is_not_installed\n", encoding="utf-8")
    problems = sw.import_check(tmp_path, "laptop")
    assert len(problems) == 1 and "app" in problems[0] and "AttributeError" in problems[0]  # a missing third-party package is skipped
