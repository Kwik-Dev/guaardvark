"""process_file says why a file of a supported type could not be read: a
package that is not installed, a password, damage, an old format. It does not
report such a file as empty, as unsupported, or as having no worksheets.

Files are built in a temporary uploads folder; no image is processed and
nothing touches the network."""

import pytest

from backend import config
from backend.services import excel_content_service
from backend.tools.agent_tools.file_operation_tools import ProcessFileTool
from backend.utils.enhanced_file_processor import (
    FileProcessingError, container_kind, create_file_processor, is_encrypted_office_file,
)

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# The container a password-protected .docx/.xlsx is saved in: an OLE file whose
# directory names the streams EncryptionInfo and EncryptedPackage.
ENCRYPTED_OFFICE = (
    OLE + b"\x00" * 1016 + "EncryptionInfo".encode("utf-16-le") + b"\x00" * 100
    + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 400
)
OLD_OFFICE = OLE + b"\x00" * 1016


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with a text layer and a correct cross-reference table."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    folder = tmp_path / "uploads"
    folder.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", str(folder))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "outputs"))
    # The stock install: neither optional Excel reader is present.
    monkeypatch.setattr(excel_content_service, "xlrd_available", False)
    monkeypatch.setattr(excel_content_service, "pyxlsb_available", False)
    return folder


def read(folder, name, data: bytes):
    (folder / name).write_bytes(data)
    return ProcessFileTool().execute(file_path=name)


@pytest.mark.parametrize("name,data,reason", [
    ("legacy.xls", OLD_OFFICE, "needs the xlrd package, which is not installed"),
    ("binary.xlsb", b"PK\x03\x04" + b"\x00" * 60, "needs the pyxlsb package, which is not installed"),
    ("locked.xlsx", ENCRYPTED_OFFICE, "password-protected"),
    ("old.xlsx", OLD_OFFICE, "needs the xlrd package"),
    ("corrupt.xlsx", b"PK\x03\x04" + b"\x00" * 60, "damaged or is not an Excel file"),
    ("text.xlsx", b"name,qty\napple,3\n", "is not an Excel workbook"),
    ("table.xls", b"<html><table><tr><td>x</td></tr></table></html>", "is not an Excel workbook"),
    ("empty.xlsx", b"", "empty (0 bytes)"),
])
def test_an_unreadable_workbook_fails_with_its_reason(uploads, name, data, reason):
    result = read(uploads, name, data)
    assert not result.success
    assert reason in result.error
    assert "No worksheets found" not in result.error


def test_a_good_workbook_is_still_read(uploads):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    workbook.active.append(["name", "qty"])
    workbook.active.append(["apple", 3])
    workbook.save(uploads / "good.xlsx")
    result = ProcessFileTool().execute(file_path="good.xlsx")
    assert result.success and "apple" in result.output


def test_a_pdf_with_a_text_layer_is_read(uploads):
    result = read(uploads, "good.pdf", minimal_pdf("Hello from a test PDF"))
    assert result.success and "Hello from a test PDF" in result.output


def _encrypted(uploads, **passwords) -> bytes:
    from pypdf import PdfReader, PdfWriter

    (uploads / "plain.pdf").write_bytes(minimal_pdf("Hello from a test PDF"))
    writer = PdfWriter()
    writer.append(PdfReader(uploads / "plain.pdf"))
    writer.encrypt(algorithm="RC4-128", **passwords)
    with open(uploads / "encrypted-source.pdf", "wb") as f:
        writer.write(f)
    return (uploads / "encrypted-source.pdf").read_bytes()


def test_a_password_protected_pdf_says_so(uploads):
    result = read(uploads, "locked.pdf", _encrypted(uploads, user_password="secret"))
    assert not result.success and "password-protected" in result.error
    assert "unsupported" not in result.error


def test_a_pdf_that_only_restricts_editing_is_read(uploads):
    data = _encrypted(uploads, user_password="", owner_password="owner-only")
    result = read(uploads, "owner-locked.pdf", data)
    assert result.success and "Hello from a test PDF" in result.output


@pytest.mark.parametrize("name,data,reason", [
    ("broken.pdf", b"%PDF-1.4\nthis is not a real pdf body\n", "the PDF is damaged or incomplete"),
    ("empty.pdf", b"", "empty (0 bytes)"),
    ("legacy.doc", OLD_OFFICE, "old-format Word file (.doc)"),
    ("locked.docx", ENCRYPTED_OFFICE, "the document is password-protected"),
    ("text.docx", b"just some text\n", "not a readable .docx document"),
    ("bad.xml", b"<root><item>alpha</root>", "the XML is not well-formed"),
])
def test_an_unreadable_document_fails_with_its_reason(uploads, name, data, reason):
    result = read(uploads, name, data)
    assert not result.success
    assert reason in result.error
    assert "unsupported type" not in result.error


def test_half_a_pdf_is_reported_as_damaged(uploads):
    whole = minimal_pdf("Hello from a test PDF")
    result = read(uploads, "truncated.pdf", whole[: len(whole) // 2])
    assert not result.success and "damaged or incomplete" in result.error


def test_a_type_with_no_reader_is_named_as_such(uploads):
    result = read(uploads, "data.bin", b"\x01\x02\x03")
    assert not result.success and "'.bin' files are not a type this tool reads" in result.error


def test_indexing_still_gets_none_and_can_ask_for_the_reason(uploads):
    """The indexing pipeline takes None as "fall back to the legacy reader"."""
    broken = uploads / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nthis is not a real pdf body\n")
    processor = create_file_processor()
    assert processor.process_file(str(broken)) is None
    with pytest.raises(FileProcessingError, match="damaged or incomplete"):
        processor.process_file(str(broken), raise_errors=True)
    (uploads / "data.bin").write_bytes(b"\x01")
    assert processor.process_file(str(uploads / "data.bin"), raise_errors=True) is None


def test_telling_the_containers_apart(tmp_path):
    for name, data, kind in [("a.xlsx", b"PK\x03\x04rest", "zip"), ("b.xls", OLD_OFFICE, "ole"),
                             ("c.txt", b"hello", "other"), ("d.pdf", b"", "empty")]:
        (tmp_path / name).write_bytes(data)
        assert container_kind(str(tmp_path / name)) == kind
    (tmp_path / "locked.xlsx").write_bytes(ENCRYPTED_OFFICE)
    assert is_encrypted_office_file(str(tmp_path / "locked.xlsx"))
    assert not is_encrypted_office_file(str(tmp_path / "b.xls"))


def test_the_encryption_marker_is_found_across_a_read_boundary(tmp_path):
    marker = "EncryptedPackage".encode("utf-16-le")
    path = tmp_path / "split.xlsx"
    path.write_bytes(OLE + b"\x00" * (1024 * 1024 - len(OLE) - 10) + marker + b"\x00" * 100)
    assert is_encrypted_office_file(str(path))
