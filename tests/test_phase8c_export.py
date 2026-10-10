"""Phase 8C — Source Export Service and API endpoint tests."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.models.phase4 import (
    RepositoryObjectSource,
    RepositorySourceComponent,
    RepositorySourceLine,
)
from app.models.phase5 import (
    BulkExtractionResult,
    BulkExtractionStatus,
    ExtractedRepositoryObject,
    ExtractionError,
    ExtractionStage,
    ObjectExtractionStatus,
)
from app.services.export import RepositoryExportService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings() -> Settings:
    return Settings(app_name="N-CoreIQ", app_version="0.8.6-test", app_env="test")


def _line(ctype, cname, line_no, text):
    return RepositorySourceLine(component_type=ctype, component_name=cname, line_no=line_no, source_line=text)


def _component(ctype, cname, lines, source_text=None):
    return RepositorySourceComponent(component_type=ctype, component_name=cname, lines=lines, source_text=source_text)


def _source(obj_type, obj_name, components, truncated=False):
    return RepositoryObjectSource(
        sap_object_type=obj_type, object_name=obj_name, max_lines=5000,
        returned_lines=sum(len(c.lines) for c in components),
        component_count=len(components), found=True, truncated=truncated,
        components=components,
    )


def _ok_obj(obj_type, obj_name, source):
    return ExtractedRepositoryObject(
        object_type=obj_type, object_name=obj_name,
        status=ObjectExtractionStatus.SUCCESS, source=source,
    )


def _fail_obj(obj_type, obj_name):
    return ExtractedRepositoryObject(
        object_type=obj_type, object_name=obj_name,
        status=ObjectExtractionStatus.FAILED,
        errors=[ExtractionError(code="ERR", message="Not found", stage=ExtractionStage.SOURCE)],
    )


_TS = datetime(2026, 10, 9, 23, 0, 0, tzinfo=timezone.utc)


def _result(objects, **kwargs):
    successful = sum(1 for o in objects if o.status == ObjectExtractionStatus.SUCCESS)
    failed = sum(1 for o in objects if o.status == ObjectExtractionStatus.FAILED)
    ts = kwargs.pop("started_at", _TS)
    return BulkExtractionResult(
        extraction_id="test-extr-001", started_at=ts,
        completed_at=datetime(2026, 10, 9, 23, 0, 5, tzinfo=timezone.utc),
        duration_ms=5000.0,
        status=BulkExtractionStatus.COMPLETED if failed == 0 else BulkExtractionStatus.COMPLETED_WITH_ERRORS,
        selection="explicit", requested_object_count=len(objects),
        processed_object_count=len(objects), successful_object_count=successful,
        partial_object_count=0, failed_object_count=failed,
        metadata_enabled=False, source_enabled=True, source_max_lines=5000,
        objects=objects, **kwargs,
    )


def _unzip(raw):
    zf = zipfile.ZipFile(io.BytesIO(raw))
    return {name: zf.read(name) for name in zf.namelist()}


def _prog(name, body=None):
    body = body or f"REPORT {name}."
    lines = [_line("PROGRAM", name, 1, body)]
    return _ok_obj("PROG", name, _source("PROG", name, [_component("PROGRAM", name, lines)]))


# ---------------------------------------------------------------------------
# 1. Folder structure
# ---------------------------------------------------------------------------

class TestFolderStructure:
    def test_root_uses_ncoreiq_source_prefix(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([_prog("ZTEST1")]))
        roots = {p.split("/")[0] for p in _unzip(raw)}
        assert list(roots)[0].startswith("N-CoreIQ-Source-")

    def test_root_contains_timestamp(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([]))
        root = next(iter(_unzip(raw))).split("/")[0]
        assert "20261009_230000" in root

    def test_manifest_at_root_level_only(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([]))
        manifests = [p for p in _unzip(raw) if p.endswith("manifest.json")]
        assert len(manifests) == 1
        assert manifests[0].count("/") == 1

    def test_prog_under_prog_subdir(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([_prog("ZREP1")]))
        abap = [p for p in _unzip(raw) if p.endswith(".abap")]
        assert abap[0].split("/")[1] == "PROG"

    def test_clas_under_clas_subdir(self):
        clas_src = _source("CLAS", "ZCL_X", [
            _component("CLASS_POOL", "ZCL_X", [_line("CLASS_POOL", "ZCL_X", 1, "CLASS ZCL_X DEFINITION.")]),
            _component("METHOD", "ZCL_X_RUN", [_line("METHOD", "ZCL_X_RUN", 1, "  METHOD run.")]),
        ])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj("CLAS", "ZCL_X", clas_src)])
        )
        abap = [p for p in _unzip(raw) if p.endswith(".abap")]
        assert all(p.split("/")[1] == "CLAS" for p in abap)


# ---------------------------------------------------------------------------
# 2. Single-component naming
# ---------------------------------------------------------------------------

class TestSingleComponentNaming:
    def test_filename_is_objectname_abap(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([_prog("ZREPORT1")]))
        abap = [p for p in _unzip(raw) if p.endswith(".abap")]
        assert any("ZREPORT1.abap" in p for p in abap)

    def test_three_programs(self):
        objects = [_prog(f"ZPROGRAM_{i}") for i in range(1, 4)]
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result(objects))
        abap = [p for p in _unzip(raw) if p.endswith(".abap")]
        assert len(abap) == 3
        names = {p.split("/")[-1] for p in abap}
        assert names == {"ZPROGRAM_1.abap", "ZPROGRAM_2.abap", "ZPROGRAM_3.abap"}


# ---------------------------------------------------------------------------
# 3. Multi-component naming
# ---------------------------------------------------------------------------

class TestMultiComponentNaming:
    def _clas_result(self):
        clas_src = _source("CLAS", "ZCL_SAMPLE", [
            _component("CLASS_POOL", "ZCL_SAMPLE", [
                _line("CLASS_POOL", "ZCL_SAMPLE", 1, "CLASS ZCL_SAMPLE DEFINITION."),
            ]),
            _component("METHOD", "ZCL_SAMPLE_DO_SOMETHING", [
                _line("METHOD", "ZCL_SAMPLE_DO_SOMETHING", 1, "  METHOD do_something."),
                _line("METHOD", "ZCL_SAMPLE_DO_SOMETHING", 2, "  ENDMETHOD."),
            ]),
        ])
        return _result([_ok_obj("CLAS", "ZCL_SAMPLE", clas_src)])

    def test_class_component_names(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(self._clas_result())
        filenames = {p.split("/")[-1] for p in _unzip(raw) if p.endswith(".abap")}
        assert "CLASS_POOL__ZCL_SAMPLE.abap" in filenames
        assert "METHOD__ZCL_SAMPLE_DO_SOMETHING.abap" in filenames

    def test_fugr_no_overwrite(self):
        fugr_src = _source("FUGR", "ZFIN_FM", [
            _component("FUNCTION", "ZFI_GET_DATA", [
                _line("FUNCTION", "ZFI_GET_DATA", 1, "FUNCTION ZFI_GET_DATA."),
                _line("FUNCTION", "ZFI_GET_DATA", 2, "ENDFUNCTION."),
            ]),
            _component("FUNCTION", "ZFI_POST_DATA", [
                _line("FUNCTION", "ZFI_POST_DATA", 1, "FUNCTION ZFI_POST_DATA."),
                _line("FUNCTION", "ZFI_POST_DATA", 2, "ENDFUNCTION."),
            ]),
        ])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj("FUGR", "ZFIN_FM", fugr_src)])
        )
        entries = _unzip(raw)
        abap = [p for p in entries if p.endswith(".abap")]
        assert len(abap) == 2
        for path, content in entries.items():
            if "ZFI_GET_DATA.abap" in path:
                assert b"ZFI_GET_DATA" in content
                assert b"ZFI_POST_DATA" not in content
            elif "ZFI_POST_DATA.abap" in path:
                assert b"ZFI_POST_DATA" in content
                assert b"ZFI_GET_DATA" not in content

    def test_deterministic_filenames(self):
        result = self._clas_result()
        svc = RepositoryExportService(_settings())
        names1 = {p for p in _unzip(svc.to_source_zip_bytes(result)) if p.endswith(".abap")}
        names2 = {p for p in _unzip(svc.to_source_zip_bytes(result)) if p.endswith(".abap")}
        assert names1 == names2


# ---------------------------------------------------------------------------
# 4-9. Source content fidelity
# ---------------------------------------------------------------------------

class TestSourceContentFidelity:
    def test_utf8_preserved(self):
        lines = [
            _line("PROGRAM", "ZU", 1, "* \u00fcber ABAP"),
            _line("PROGRAM", "ZU", 2, "* \u65e5\u672c\u8a9e"),
            _line("PROGRAM", "ZU", 3, "* caf\u00e9"),
        ]
        src = _source("PROG", "ZU", [_component("PROGRAM", "ZU", lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj("PROG", "ZU", src)])
        )
        text = next(v for k, v in _unzip(raw).items() if k.endswith(".abap")).decode("utf-8")
        assert "\u00fc" in text
        assert "\u65e5\u672c\u8a9e" in text
        assert "\u00e9" in text

    def test_indentation_preserved(self):
        lines = [
            _line("PROGRAM", "ZI", 1, "IF lv_ok = abap_true."),
            _line("PROGRAM", "ZI", 2, "  WRITE 'yes'."),
            _line("PROGRAM", "ZI", 3, "ENDIF."),
        ]
        src = _source("PROG", "ZI", [_component("PROGRAM", "ZI", lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj("PROG", "ZI", src)])
        )
        text = next(v for k, v in _unzip(raw).items() if k.endswith(".abap")).decode("utf-8")
        assert "  WRITE 'yes'." in text

    def test_comments_preserved(self):
        lines = [
            _line("PROGRAM", "ZC", 1, "* This is a comment"),
            _line("PROGRAM", "ZC", 2, "\" Another comment"),
            _line("PROGRAM", "ZC", 3, "REPORT ZC."),
        ]
        src = _source("PROG", "ZC", [_component("PROGRAM", "ZC", lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj("PROG", "ZC", src)])
        )
        text = next(v for k, v in _unzip(raw).items() if k.endswith(".abap")).decode("utf-8")
        assert "* This is a comment" in text
        assert "\" Another comment" in text

    def test_blank_lines_preserved(self):
        lines = [
            _line("PROGRAM", "ZB", 1, "REPORT ZB."),
            _line("PROGRAM", "ZB", 2, ""),
            _line("PROGRAM", "ZB", 3, "START-OF-SELECTION."),
        ]
        src = _source("PROG", "ZB", [_component("PROGRAM", "ZB", lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj("PROG", "ZB", src)])
        )
        text = next(v for k, v in _unzip(raw).items() if k.endswith(".abap")).decode("utf-8")
        assert "\n\n" in text

    def test_lines_ordered_by_line_no(self):
        # Provide lines out of order; should be sorted by line_no
        lines = [
            _line('PROGRAM', 'ZO', 3, 'ENDREPORT.'),
            _line('PROGRAM', 'ZO', 1, 'REPORT ZO.'),
            _line('PROGRAM', 'ZO', 2, 'WRITE hello.'),
        ]
        src = _source('PROG', 'ZO', [_component('PROGRAM', 'ZO', lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj('PROG', 'ZO', src)])
        )
        text = next(v for k, v in _unzip(raw).items() if k.endswith('.abap')).decode('utf-8')
        idx_report = text.index('REPORT ZO.')
        idx_write = text.index('WRITE hello.')
        idx_end = text.index('ENDREPORT.')
        assert idx_report < idx_write < idx_end

    def test_no_html_or_json_wrappers_in_abap_files(self):
        lines = [
            _line('PROGRAM', 'ZW', 1, 'REPORT ZW.'),
            _line('PROGRAM', 'ZW', 2, 'WRITE hello.'),
        ]
        src = _source('PROG', 'ZW', [_component('PROGRAM', 'ZW', lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj('PROG', 'ZW', src)])
        )
        text = next(v for k, v in _unzip(raw).items() if k.endswith('.abap')).decode('utf-8')
        # Must not contain JSON structure or HTML tags
        assert '{' not in text
        assert '<html' not in text.lower()
        assert 'Content-Type' not in text

    def test_source_text_preferred_over_lines_join(self):
        pre_joined = 'REPORT ZPT.\nWRITE already_joined.'
        lines = [
            _line('PROGRAM', 'ZPT', 1, 'REPORT ZPT.'),
            _line('PROGRAM', 'ZPT', 2, 'WRITE from_lines.'),  # should be ignored
        ]
        comp = _component('PROGRAM', 'ZPT', lines, source_text=pre_joined)
        src = _source('PROG', 'ZPT', [comp])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj('PROG', 'ZPT', src)])
        )
        text = next(v for k, v in _unzip(raw).items() if k.endswith('.abap')).decode('utf-8')
        assert 'already_joined' in text
        assert 'from_lines' not in text


# ---------------------------------------------------------------------------
# 10. Filename sanitization
# ---------------------------------------------------------------------------

class TestFilenameSanitization:
    def test_slash_in_object_name_sanitized(self):
        # SAP namespace: /MYNS/PROGRAM — slash should be replaced
        lines = [_line('PROGRAM', '_MYNS_PROG', 1, 'REPORT NS.')]
        src = _source('PROG', '/MYNS/PROG', [_component('PROGRAM', '/MYNS/PROG', lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj('PROG', '/MYNS/PROG', src)])
        )
        entries = _unzip(raw)
        abap = [p for p in entries if p.endswith('.abap')]
        assert len(abap) == 1
        # No raw slash in the path components after the type directory
        filename = abap[0].split('/')[-1]
        assert '/' not in filename

    def test_dotdot_in_object_name_blocked(self):
        # Object name containing ../ must not produce a traversal path
        lines = [_line('PROGRAM', 'ZOK', 1, 'REPORT ZOK.')]
        src = _source('PROG', '../../etc/passwd', [_component('PROGRAM', 'ZOK', lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj('PROG', '../../etc/passwd', src)])
        )
        entries = _unzip(raw)
        abap = [p for p in entries if p.endswith('.abap')]
        assert len(abap) == 1
        # Path must remain under root dir
        root = abap[0].split('/')[0]
        assert root.startswith('N-CoreIQ-Source-')
        # Must not contain traversal sequences
        for part in abap[0].split('/'):
            assert '..' not in part

    def test_long_name_truncated_at_64_chars(self):
        long_name = 'Z' + 'X' * 80
        lines = [_line('PROGRAM', long_name[:64], 1, 'REPORT long.')]
        src = _source('PROG', long_name, [_component('PROGRAM', long_name, lines)])
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj('PROG', long_name, src)])
        )
        entries = _unzip(raw)
        abap = [p for p in entries if p.endswith('.abap')]
        filename = abap[0].split('/')[-1]
        assert len(filename) <= 70  # 64 + len('.abap')


# ---------------------------------------------------------------------------
# 11. Truncated source in manifest
# ---------------------------------------------------------------------------

class TestTruncatedSourceInManifest:
    def test_truncated_flag_in_manifest_object(self):
        src = _source('PROG', 'ZTRUNC', [
            _component('PROGRAM', 'ZTRUNC', [_line('PROGRAM', 'ZTRUNC', 1, 'REPORT ZTRUNC.')])
        ], truncated=True)
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(
            _result([_ok_obj('PROG', 'ZTRUNC', src)])
        )
        entries = _unzip(raw)
        manifest = json.loads(entries[next(p for p in entries if p.endswith('manifest.json'))].decode('utf-8'))
        obj = manifest['objects'][0]
        assert obj['source_truncated'] is True


# ---------------------------------------------------------------------------
# 12. One failing object among multiple successful ones
# ---------------------------------------------------------------------------

class TestMixedResults:
    def test_failed_object_not_in_abap_files(self):
        objects = [
            _prog('ZSUCCESS_1'),
            _fail_obj('PROG', 'ZFAILED_1'),
            _prog('ZSUCCESS_2'),
        ]
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result(objects))
        entries = _unzip(raw)
        abap = [p for p in entries if p.endswith('.abap')]
        # Only 2 successful objects should have .abap files
        assert len(abap) == 2
        filenames = {p.split('/')[-1] for p in abap}
        assert 'ZSUCCESS_1.abap' in filenames
        assert 'ZSUCCESS_2.abap' in filenames
        assert not any('ZFAILED_1' in f for f in filenames)

    def test_failed_object_in_manifest_with_error(self):
        objects = [_prog('ZOK'), _fail_obj('PROG', 'ZBAD')]
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result(objects))
        entries = _unzip(raw)
        manifest = json.loads(entries[next(p for p in entries if p.endswith('manifest.json'))].decode('utf-8'))
        statuses = {o['object_name']: o['status'] for o in manifest['objects']}
        assert statuses['ZOK'] == 'SUCCESS'
        assert statuses['ZBAD'] == 'FAILED'
        bad = next(o for o in manifest['objects'] if o['object_name'] == 'ZBAD')
        assert 'errors' in bad
        assert len(bad['errors']) > 0


# ---------------------------------------------------------------------------
# 13. Manifest contents
# ---------------------------------------------------------------------------

class TestManifestContents:
    def test_manifest_schema_version(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([]))
        entries = _unzip(raw)
        manifest = json.loads(entries[next(p for p in entries if p.endswith('manifest.json'))].decode('utf-8'))
        assert manifest['schema_version'] == '8c'

    def test_manifest_extraction_id(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([]))
        entries = _unzip(raw)
        manifest = json.loads(entries[next(p for p in entries if p.endswith('manifest.json'))].decode('utf-8'))
        assert manifest['extraction_id'] == 'test-extr-001'

    def test_manifest_statistics(self):
        objects = [_prog('ZSTAT_OK'), _fail_obj('PROG', 'ZSTAT_BAD')]
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result(objects))
        entries = _unzip(raw)
        manifest = json.loads(entries[next(p for p in entries if p.endswith('manifest.json'))].decode('utf-8'))
        stats = manifest['statistics']
        assert stats['total_requested'] == 2
        assert stats['successful'] == 1
        assert stats['failed'] == 1

    def test_manifest_file_hashes_present(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([_prog('ZH1'), _prog('ZH2')]))
        entries = _unzip(raw)
        manifest = json.loads(entries[next(p for p in entries if p.endswith('manifest.json'))].decode('utf-8'))
        assert len(manifest['file_hashes']) == 2

    def test_manifest_file_hashes_are_sha256(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([_prog('ZSH1')]))
        entries = _unzip(raw)
        manifest = json.loads(entries[next(p for p in entries if p.endswith('manifest.json'))].decode('utf-8'))
        for hash_val in manifest['file_hashes'].values():
            assert len(hash_val) == 64  # SHA-256 hex digest is 64 chars
            assert all(c in '0123456789abcdef' for c in hash_val)

    def test_manifest_application_name(self):
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result([]))
        entries = _unzip(raw)
        manifest = json.loads(entries[next(p for p in entries if p.endswith('manifest.json'))].decode('utf-8'))
        assert manifest['application_name'] == 'N-CoreIQ'


# ---------------------------------------------------------------------------
# 14. ZIP fallback: same logical structure as folder export
# ---------------------------------------------------------------------------

class TestZipFallback:
    def test_zip_contains_manifest_and_abap_files(self):
        objects = [_prog('ZFALLBACK_1'), _prog('ZFALLBACK_2')]
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result(objects))
        entries = _unzip(raw)
        assert any(p.endswith('manifest.json') for p in entries)
        abap = [p for p in entries if p.endswith('.abap')]
        assert len(abap) == 2

    def test_zip_structure_matches_flat_spec(self):
        objects = [_prog('ZFLAT')]
        raw = RepositoryExportService(_settings()).to_source_zip_bytes(_result(objects))
        entries = _unzip(raw)
        abap = next(p for p in entries if p.endswith('.abap'))
        parts = abap.split('/')
        # root/PROG/ZFLAT.abap
        assert len(parts) == 3
        assert parts[0].startswith('N-CoreIQ-Source-')
        assert parts[1] == 'PROG'
        assert parts[2] == 'ZFLAT.abap'

    def test_source_export_filename_format(self):
        svc = RepositoryExportService(_settings())
        fn = svc.source_export_filename('20261009_230000')
        assert fn == 'N-CoreIQ-Source-20261009_230000.zip'


# ---------------------------------------------------------------------------
# 15. API endpoint tests
# ---------------------------------------------------------------------------

class TestSourceExportEndpoint:
    def _make_client(self):
        from app.main import app
        return TestClient(app)

    def _mock_bulk_result(self):
        src = _source('PROG', 'ZAPI_TEST', [
            _component('PROGRAM', 'ZAPI_TEST', [
                _line('PROGRAM', 'ZAPI_TEST', 1, 'REPORT ZAPI_TEST.'),
            ])
        ])
        return _result([_ok_obj('PROG', 'ZAPI_TEST', src)])

    def test_endpoint_returns_zip_200(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services.bulk_extraction import BulkExtractionService
        mock_result = self._mock_bulk_result()
        with patch.object(BulkExtractionService, 'extract', new_callable=AsyncMock, return_value=mock_result):
            with TestClient(app) as client:
                resp = client.post(
                    '/api/v1/repository/source-export',
                    json={'objects': [{'object_type': 'PROG', 'object_name': 'ZAPI_TEST'}],
                          'include_metadata': False, 'include_source': True}
                )
        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'application/zip'

    def test_endpoint_content_disposition_filename(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services.bulk_extraction import BulkExtractionService
        mock_result = self._mock_bulk_result()
        with patch.object(BulkExtractionService, 'extract', new_callable=AsyncMock, return_value=mock_result):
            with TestClient(app) as client:
                resp = client.post(
                    '/api/v1/repository/source-export',
                    json={'objects': [{'object_type': 'PROG', 'object_name': 'ZAPI_TEST'}],
                          'include_metadata': False, 'include_source': True}
                )
        cd = resp.headers.get('content-disposition', '')
        assert 'N-CoreIQ-Source-' in cd
        assert '.zip' in cd

    def test_endpoint_returns_valid_zip(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.services.bulk_extraction import BulkExtractionService
        mock_result = self._mock_bulk_result()
        with patch.object(BulkExtractionService, 'extract', new_callable=AsyncMock, return_value=mock_result):
            with TestClient(app) as client:
                resp = client.post(
                    '/api/v1/repository/source-export',
                    json={'objects': [{'object_type': 'PROG', 'object_name': 'ZAPI_TEST'}],
                          'include_metadata': False, 'include_source': True}
                )
        entries = _unzip(resp.content)
        assert any(p.endswith('manifest.json') for p in entries)
        assert any(p.endswith('.abap') for p in entries)

    def test_endpoint_422_on_empty_objects(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            resp = client.post(
                '/api/v1/repository/source-export',
                json={'objects': [], 'include_metadata': False, 'include_source': True}
            )
        assert resp.status_code == 422

    def test_endpoint_422_on_missing_selection(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            resp = client.post(
                '/api/v1/repository/source-export',
                json={'include_metadata': False, 'include_source': True}
            )
        assert resp.status_code == 422
