import pytest

from buildanything import files


@pytest.fixture
def root(tmp_path):
    (tmp_path / "firmware" / "laptop").mkdir(parents=True)
    (tmp_path / "firmware" / "laptop" / "app.py").write_bytes(b"print('hi')\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    return tmp_path


def test_lists_folders_first_including_git(root):
    names = [e["name"] for e in files.listing(root)["entries"]]
    assert names[:2] == [".git", "firmware"] and "a.png" in names


def test_reads_text_images_and_flags_binary(root):
    assert files.content(root, "firmware/laptop/app.py")["text"] == "print('hi')\n"
    assert files.content(root, "a.png")["data"].startswith("data:image/png;base64,")
    assert files.content(root, "blob.bin")["kind"] == "binary"


def test_big_text_files_come_in_pages_of_whole_lines(root, monkeypatch):
    monkeypatch.setattr(files, "MAX_TEXT", 50)
    (root / "log.jsonl").write_bytes(b"".join(b'{"n": %d}\n' % i for i in range(30)))
    last = files.content(root, "log.jsonl")
    assert last["partial"] and last["end"] == (root / "log.jsonl").stat().st_size and last["text"].startswith('{"n"') and last["text"].endswith("}\n")
    first = files.content(root, "log.jsonl", 0)
    assert first["start"] == 0 and first["text"].startswith('{"n": 0}') and first["end"] < last["start"] + 1


def test_cannot_read_outside_the_project(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("other") / "secret.txt"
    outside.write_text("no", encoding="utf-8")
    with pytest.raises(files.FilesError):
        files.content(root, "../" + outside.parent.name + "/secret.txt")
    with pytest.raises(files.FilesError):
        files.listing(root, "..")


def test_tables_from_csv_and_xlsx(root):
    import zipfile
    (root / "parts.csv").write_bytes(b'name,qty\n"Chord, lower",2\n')
    sheet = root / "bom.xlsx"
    with zipfile.ZipFile(sheet, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="BOM" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Part</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1"><v>7</v></c></row></sheetData></worksheet>')
    assert files.content(root, "parts.csv")["sheets"][0]["rows"][1] == ["Chord, lower", "2"]
    assert files.content(root, "bom.xlsx")["sheets"][0] == {"name": "BOM", "rows": [["Part", "", "7"]], "more": False}


def test_xml_is_indented_and_raw_serves_only_safe_types(root):
    (root / "robot.urdf").write_bytes(b"<robot><link name='a'/></robot>")
    assert "\n  <link" in files.content(root, "robot.urdf")["pretty"]
    (root / "doc.pdf").write_bytes(b"%PDF-1.4")
    assert files.raw(root, "doc.pdf")[1] == "application/pdf"
    (root / "page.html").write_bytes(b"<script>alert(1)</script>")
    with pytest.raises(files.FilesError):
        files.raw(root, "page.html")


def test_formats_lists_what_the_app_cannot_open(root):
    (root / "mesh.stl").write_bytes(b"solid x")
    (root / "board.brd").write_bytes(b"?")
    found = files.formats(root)
    assert any(t["type"] == ".stl" and t["viewer"] == "model" for t in found["types"])
    assert [t["type"] for t in found["unsupported"]] == [".brd", ".bin"] or ".brd" in [t["type"] for t in found["unsupported"]]
