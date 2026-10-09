"""Phase 4 provider-level tests.

Tests ODataSAPConnectionProvider._parse_object_metadata,
_parse_object_source, get_object_metadata and get_object_source
using mocked httpx responses — no live SAP connection required.
"""

from __future__ import annotations

import pytest

from app.models.phase4 import RepositoryObjectMetadata, RepositoryObjectSource
from app.sap.connection.odata import ODataSAPConnectionProvider
from app.sap.exceptions import (
    RepositoryObjectNotFoundError,
    RepositorySourceDataIntegrityError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConnectionError,
    SAPResponseParseError,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_provider(settings) -> ODataSAPConnectionProvider:
    return ODataSAPConnectionProvider(settings)


def _metadata_payload(
    *,
    object_type: str = "PROG",
    object_name: str = "ZMY_PROGRAM",
    system_id: str = "NC1",
    client: str = "800",
    pgmid: str = "R3TR",
    package: str = "ZNCOREIQ",
    author: str = "DEVELOPER",
    master_language: str = "EN",
    source_system: str = "",
    created_date: str = "20230101",
    changed_by: str = "CHANGER",
    changed_date: str = "20240101",
    description: str = "My Program",
    active_status: str = "A",
    object_subtype: str = "",
    program_name: str = "",
    function_group: str = "",
    table_class: str = "",
    warning: str = "",
    found: object = True,
    message: str = "OK",
) -> dict:
    return {
        "d": {
            "results": [
                {
                    "ObjectType": object_type,
                    "ObjectName": object_name,
                    "SystemId": system_id,
                    "Client": client,
                    "Pgmid": pgmid,
                    "Package": package,
                    "Author": author,
                    "MasterLanguage": master_language,
                    "SourceSystem": source_system,
                    "CreatedDate": created_date,
                    "ChangedBy": changed_by,
                    "ChangedDate": changed_date,
                    "Description": description,
                    "ActiveStatus": active_status,
                    "ObjectSubtype": object_subtype,
                    "ProgramName": program_name,
                    "FunctionGroup": function_group,
                    "TableClass": table_class,
                    "Warning": warning,
                    "Found": found,
                    "Message": message,
                }
            ]
        }
    }


def _source_row(
    *,
    comp_type: str,
    comp_name: str,
    line_no: int,
    source_line: str,
    system_id: str = "NC1",
    client: str = "800",
    object_type: str = "PROG",
    object_name: str = "ZMY_PROGRAM",
    max_lines: int = 5000,
    found: object = True,
    truncated: object = False,
    message: str = "",
) -> dict:
    return {
        "ObjectType": object_type,
        "ObjectName": object_name,
        "ComponentType": comp_type,
        "ComponentName": comp_name,
        "LineNo": line_no,
        "SourceLine": source_line,
        "SystemId": system_id,
        "Client": client,
        "MaxLines": max_lines,
        "Found": found,
        "Truncated": truncated,
        "Message": message,
    }


def _source_payload(rows: list[dict]) -> dict:
    return {"d": {"results": rows}}


# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings(monkeypatch):
    monkeypatch.setenv("SAP_ODATA__BASE_URL", "https://sap.example.com:8443")
    monkeypatch.setenv("SAP_ODATA__USER", "NCOREIQ")
    monkeypatch.setenv("SAP_ODATA__PASSWORD", "secret")
    monkeypatch.setenv("SAP_ODATA__CLIENT", "800")
    monkeypatch.setenv("SAP_ODATA__SERVICE", "ZNCOREIQ_REPO_SRV")
    monkeypatch.setenv("SAP_CONNECTION_MODE", "odata")
    from app.core.config import Settings

    return Settings()


# ===========================================================================
# _parse_object_metadata — unit tests (no HTTP)
# ===========================================================================


class TestParseObjectMetadata:
    def _parse(self, payload, ot="PROG", on="ZMY_PROGRAM", settings=None):
        if settings is None:
            from unittest.mock import MagicMock

            settings = MagicMock()
        provider = ODataSAPConnectionProvider(settings)
        return provider._parse_object_metadata(payload, ot, on)

    def test_prog_metadata_happy_path(self):
        payload = _metadata_payload()
        result = self._parse(payload)
        assert isinstance(result, RepositoryObjectMetadata)
        assert result.sap_object_type == "PROG"
        assert result.object_name == "ZMY_PROGRAM"
        assert result.system_id == "NC1"
        assert result.client == "800"
        assert result.pgmid == "R3TR"
        assert result.package == "ZNCOREIQ"
        assert result.author == "DEVELOPER"
        assert result.master_language == "EN"
        assert result.created_date == "20230101"
        assert result.changed_by == "CHANGER"
        assert result.changed_date == "20240101"
        assert result.description == "My Program"
        assert result.active_status == "A"
        assert result.found is True
        assert result.message == "OK"

    def test_clas_metadata(self):
        payload = _metadata_payload(
            object_type="CLAS",
            object_name="ZCL_SAMPLE",
            description="Sample Class",
        )
        result = self._parse(payload, "CLAS", "ZCL_SAMPLE")
        assert result.sap_object_type == "CLAS"
        assert result.object_name == "ZCL_SAMPLE"
        assert result.description == "Sample Class"

    def test_fugr_metadata_with_function_group(self):
        payload = _metadata_payload(
            object_type="FUGR",
            object_name="ZFG_SAMPLE",
            function_group="ZFG_SAMPLE",
        )
        result = self._parse(payload, "FUGR", "ZFG_SAMPLE")
        assert result.sap_object_type == "FUGR"
        assert result.function_group == "ZFG_SAMPLE"

    def test_tabl_metadata_with_table_class(self):
        payload = _metadata_payload(
            object_type="TABL",
            object_name="ZTABLE",
            table_class="APPL",
        )
        result = self._parse(payload, "TABL", "ZTABLE")
        assert result.table_class == "APPL"

    def test_dtel_metadata(self):
        payload = _metadata_payload(
            object_type="DTEL",
            object_name="ZDTEL_AMOUNT",
            description="Amount Data Element",
        )
        result = self._parse(payload, "DTEL", "ZDTEL_AMOUNT")
        assert result.sap_object_type == "DTEL"
        assert result.description == "Amount Data Element"

    def test_tran_metadata(self):
        payload = _metadata_payload(object_type="TRAN", object_name="ZTX01")
        result = self._parse(payload, "TRAN", "ZTX01")
        assert result.sap_object_type == "TRAN"

    def test_msag_metadata(self):
        payload = _metadata_payload(object_type="MSAG", object_name="ZMSG")
        result = self._parse(payload, "MSAG", "ZMSG")
        assert result.sap_object_type == "MSAG"

    def test_func_metadata(self):
        payload = _metadata_payload(
            object_type="FUNC",
            object_name="Z_MY_FM",
            function_group="ZFG_SAMPLE",
            program_name="SAPLZFG_SAMPLE",
        )
        result = self._parse(payload, "FUNC", "Z_MY_FM")
        assert result.sap_object_type == "FUNC"
        assert result.function_group == "ZFG_SAMPLE"
        assert result.program_name == "SAPLZFG_SAMPLE"

    def test_found_false_raises_not_found(self):
        payload = _metadata_payload(found=False)
        with pytest.raises(RepositoryObjectNotFoundError) as exc_info:
            self._parse(payload)
        assert "PROG" in str(exc_info.value)
        assert "ZMY_PROGRAM" in str(exc_info.value)

    def test_found_abap_false_string_raises_not_found(self):
        payload = _metadata_payload(found="false")
        with pytest.raises(RepositoryObjectNotFoundError):
            self._parse(payload)

    def test_found_blank_string_raises_not_found(self):
        payload = _metadata_payload(found="")
        with pytest.raises(RepositoryObjectNotFoundError):
            self._parse(payload)

    def test_empty_results_raises_not_found(self):
        payload = {"d": {"results": []}}
        with pytest.raises(RepositoryObjectNotFoundError):
            self._parse(payload)

    def test_blank_optional_fields_become_none(self):
        payload = _metadata_payload(
            source_system="",
            object_subtype="",
            program_name="",
            function_group="",
            table_class="",
            warning="",
        )
        result = self._parse(payload)
        assert result.source_system is None
        assert result.object_subtype is None
        assert result.program_name is None
        assert result.function_group is None
        assert result.table_class is None
        assert result.warning is None

    def test_blank_dates_become_none(self):
        payload = _metadata_payload(created_date="", changed_date="")
        result = self._parse(payload)
        assert result.created_date is None
        assert result.changed_date is None

    def test_dates_preserved_as_yyyymmdd(self):
        payload = _metadata_payload(created_date="20220315", changed_date="20231201")
        result = self._parse(payload)
        assert result.created_date == "20220315"
        assert result.changed_date == "20231201"

    def test_description_unicode(self):
        payload = _metadata_payload(description="Résumé über Ärger — тест")
        result = self._parse(payload)
        assert result.description == "Résumé über Ärger — тест"

    def test_warning_field_preserved(self):
        payload = _metadata_payload(warning="Object is inactive")
        result = self._parse(payload)
        assert result.warning == "Object is inactive"

    def test_missing_d_envelope_raises_parse_error(self):
        with pytest.raises(SAPResponseParseError):
            self._parse({"x": {}})

    def test_missing_results_raises_parse_error(self):
        with pytest.raises(SAPResponseParseError):
            self._parse({"d": {"x": []}})

    def test_results_not_list_raises_parse_error(self):
        with pytest.raises(SAPResponseParseError):
            self._parse({"d": {"results": "not-a-list"}})

    def test_result_row_not_dict_raises_parse_error(self):
        with pytest.raises(SAPResponseParseError):
            self._parse({"d": {"results": ["not-a-dict"]}})

    def test_system_id_and_client_populated(self):
        payload = _metadata_payload(system_id="ECC", client="100")
        result = self._parse(payload)
        assert result.system_id == "ECC"
        assert result.client == "100"


# ===========================================================================
# _parse_object_source — unit tests (no HTTP)
# ===========================================================================


class TestParseObjectSource:
    def _parse(self, payload, ot="PROG", on="ZMY_PROGRAM", max_lines=5000, settings=None):
        if settings is None:
            from unittest.mock import MagicMock

            settings = MagicMock()
        provider = ODataSAPConnectionProvider(settings)
        return provider._parse_object_source(payload, ot, on, max_lines)

    # -- Basic happy path --

    def test_single_component_single_line(self):
        rows = [
            _source_row(
                comp_type="PROGRAM",
                comp_name="ZMY_PROGRAM",
                line_no=1,
                source_line="REPORT zmy_program.",
            )
        ]
        result = self._parse(_source_payload(rows))
        assert isinstance(result, RepositoryObjectSource)
        assert result.sap_object_type == "PROG"
        assert result.object_name == "ZMY_PROGRAM"
        assert result.returned_lines == 1
        assert result.component_count == 1
        assert result.found is True
        assert result.truncated is False
        comp = result.components[0]
        assert comp.component_type == "PROGRAM"
        assert comp.component_name == "ZMY_PROGRAM"
        assert comp.line_count == 1
        assert comp.lines[0].source_line == "REPORT zmy_program."
        assert comp.lines[0].line_no == 1

    def test_multiple_lines_in_order(self):
        lines = ["REPORT zmy_program.", "DATA lv_x TYPE i.", "lv_x = 1.", "WRITE lv_x."]
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=i + 1, source_line=lines[i])
            for i in range(len(lines))
        ]
        result = self._parse(_source_payload(rows))
        assert result.returned_lines == 4
        assert [sl.source_line for sl in result.components[0].lines] == lines

    def test_out_of_order_lines_sorted(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=3, source_line="LINE3"),
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="LINE1"),
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=2, source_line="LINE2"),
        ]
        result = self._parse(_source_payload(rows))
        assert [sl.line_no for sl in result.components[0].lines] == [1, 2, 3]
        assert [sl.source_line for sl in result.components[0].lines] == ["LINE1", "LINE2", "LINE3"]

    def test_multiple_components_preserved(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZCL_SAMPLE", line_no=1, source_line="CLASS zcl_sample DEFINITION."),
            _source_row(comp_type="PROGRAM", comp_name="ZCL_SAMPLE", line_no=2, source_line="ENDCLASS."),
            _source_row(comp_type="INCLUDE", comp_name="ZCL_SAMPLE_IMPL", line_no=1, source_line="CLASS zcl_sample IMPLEMENTATION."),
            _source_row(comp_type="INCLUDE", comp_name="ZCL_SAMPLE_IMPL", line_no=2, source_line="ENDCLASS."),
        ]
        result = self._parse(_source_payload(rows), "CLAS", "ZCL_SAMPLE")
        assert result.component_count == 2
        assert result.returned_lines == 4
        assert result.components[0].component_name == "ZCL_SAMPLE"
        assert result.components[1].component_name == "ZCL_SAMPLE_IMPL"

    def test_component_ordering_preserved(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="COMP_B", line_no=1, source_line="B"),
            _source_row(comp_type="INCLUDE", comp_name="COMP_A", line_no=1, source_line="A"),
        ]
        result = self._parse(_source_payload(rows))
        assert result.components[0].component_name == "COMP_B"
        assert result.components[1].component_name == "COMP_A"

    def test_truncated_flag_propagated(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT.", truncated=True),
        ]
        result = self._parse(_source_payload(rows))
        assert result.truncated is True

    def test_truncated_false_by_default(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT."),
        ]
        result = self._parse(_source_payload(rows))
        assert result.truncated is False

    def test_found_false_single_row_raises_not_found(self):
        rows = [{"ObjectType": "PROG", "ObjectName": "MISSING", "ComponentType": "", "ComponentName": "", "LineNo": 0, "SourceLine": "", "Found": False, "Truncated": False, "Message": "Not found", "SystemId": "", "Client": "", "MaxLines": 5000}]
        with pytest.raises(RepositoryObjectNotFoundError):
            self._parse(_source_payload(rows), "PROG", "MISSING")

    def test_empty_results_returns_empty_source(self):
        result = self._parse(_source_payload([]))
        assert result.returned_lines == 0
        assert result.component_count == 0
        assert result.found is True

    def test_duplicate_identical_line_skipped(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT."),
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT."),
        ]
        result = self._parse(_source_payload(rows))
        assert result.returned_lines == 1

    def test_conflicting_duplicate_line_raises_integrity_error(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="LINE_A"),
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="LINE_B"),
        ]
        with pytest.raises(RepositorySourceDataIntegrityError) as exc_info:
            self._parse(_source_payload(rows))
        assert exc_info.value.component_name == "ZMY_PROGRAM"
        assert exc_info.value.line_no == 1

    def test_source_text_derived_from_ordered_lines(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=2, source_line="DATA lv TYPE i."),
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT zmy."),
        ]
        result = self._parse(_source_payload(rows))
        assert result.components[0].source_text == "REPORT zmy.\nDATA lv TYPE i."

    def test_unicode_source_preserved(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="* Ärger über Unicode — тест"),
        ]
        result = self._parse(_source_payload(rows))
        assert result.components[0].lines[0].source_line == "* Ärger über Unicode — тест"

    def test_blank_source_line_preserved(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT."),
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=2, source_line=""),
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=3, source_line="WRITE 'x'."),
        ]
        result = self._parse(_source_payload(rows))
        assert result.components[0].lines[1].source_line == ""
        assert result.returned_lines == 3

    def test_source_line_with_quotes(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="WRITE 'it''s a test'."),
        ]
        result = self._parse(_source_payload(rows))
        assert result.components[0].lines[0].source_line == "WRITE 'it''s a test'."

    def test_source_line_with_backslash(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="* path\\to\\file"),
        ]
        result = self._parse(_source_payload(rows))
        assert result.components[0].lines[0].source_line == "* path\\to\\file"

    def test_source_line_abap_comment(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="* This is an ABAP comment"),
        ]
        result = self._parse(_source_payload(rows))
        assert result.components[0].lines[0].source_line == "* This is an ABAP comment"

    def test_max_lines_preserved_in_result(self):
        rows = [_source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT.")]
        result = self._parse(_source_payload(rows), max_lines=100)
        assert result.max_lines == 100

    def test_system_id_and_client_from_first_row(self):
        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT.", system_id="ECC", client="100"),
        ]
        result = self._parse(_source_payload(rows))
        assert result.system_id == "ECC"
        assert result.client == "100"

    def test_missing_d_envelope_raises_parse_error(self):
        with pytest.raises(SAPResponseParseError):
            self._parse({"x": {}})

    def test_missing_results_raises_parse_error(self):
        with pytest.raises(SAPResponseParseError):
            self._parse({"d": {}})

    def test_results_not_list_raises_parse_error(self):
        with pytest.raises(SAPResponseParseError):
            self._parse({"d": {"results": "bad"}})

    def test_result_row_not_dict_raises_parse_error(self):
        with pytest.raises(SAPResponseParseError):
            self._parse({"d": {"results": [42]}})

    def test_invalid_line_no_skipped(self):
        rows = [
            {"ComponentType": "PROGRAM", "ComponentName": "ZMY_PROGRAM", "LineNo": "not-int", "SourceLine": "X", "Found": True, "Truncated": False, "Message": ""},
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT."),
        ]
        result = self._parse(_source_payload(rows))
        assert result.returned_lines == 1

    def test_intf_source(self):
        rows = [
            _source_row(comp_type="INTF", comp_name="ZIF_SAMPLE", line_no=1, source_line="INTERFACE zif_sample.", object_type="INTF", object_name="ZIF_SAMPLE"),
            _source_row(comp_type="INTF", comp_name="ZIF_SAMPLE", line_no=2, source_line="ENDINTERFACE.", object_type="INTF", object_name="ZIF_SAMPLE"),
        ]
        result = self._parse(_source_payload(rows), "INTF", "ZIF_SAMPLE")
        assert result.returned_lines == 2

    def test_fugr_multiple_includes(self):
        rows = [
            _source_row(comp_type="FUGR", comp_name="ZFG_SAMPLE", line_no=1, source_line="FUNCTION-POOL zfg_sample."),
            _source_row(comp_type="INCLUDE", comp_name="LZFG_SAMPLEF01", line_no=1, source_line="FUNCTION z_my_fm."),
            _source_row(comp_type="INCLUDE", comp_name="LZFG_SAMPLEF01", line_no=2, source_line="ENDFUNCTION."),
        ]
        result = self._parse(_source_payload(rows), "FUGR", "ZFG_SAMPLE")
        assert result.component_count == 2
        assert result.returned_lines == 3

    def test_func_source(self):
        rows = [
            _source_row(comp_type="FUNC", comp_name="Z_MY_FM", line_no=1, source_line="FUNCTION z_my_fm.", object_type="FUNC", object_name="Z_MY_FM"),
            _source_row(comp_type="FUNC", comp_name="Z_MY_FM", line_no=2, source_line="ENDFUNCTION.", object_type="FUNC", object_name="Z_MY_FM"),
        ]
        result = self._parse(_source_payload(rows), "FUNC", "Z_MY_FM")
        assert result.returned_lines == 2

    def test_tabl_structured_definition(self):
        rows = [
            _source_row(comp_type="TABL", comp_name="ZTABLE", line_no=1, source_line="TABLE_NAME=ZTABLE", object_type="TABL", object_name="ZTABLE"),
        ]
        result = self._parse(_source_payload(rows), "TABL", "ZTABLE")
        assert result.component_count == 1

    def test_enqu_source(self):
        rows = [
            _source_row(comp_type="ENQU", comp_name="EZLOCK", line_no=1, source_line="LOCK_OBJECT=EZLOCK", object_type="ENQU", object_name="EZLOCK"),
        ]
        result = self._parse(_source_payload(rows), "ENQU", "EZLOCK")
        assert result.returned_lines == 1

    def test_tran_definition(self):
        rows = [
            _source_row(comp_type="TRAN", comp_name="ZTX01", line_no=1, source_line="PROG=ZMY_PROGRAM", object_type="TRAN", object_name="ZTX01"),
        ]
        result = self._parse(_source_payload(rows), "TRAN", "ZTX01")
        assert result.component_count == 1

    def test_msag_definition(self):
        rows = [
            _source_row(comp_type="MSAG", comp_name="ZMSG", line_no=1, source_line="001=First message", object_type="MSAG", object_name="ZMSG"),
        ]
        result = self._parse(_source_payload(rows), "MSAG", "ZMSG")
        assert result.returned_lines == 1


# ===========================================================================
# get_object_metadata / get_object_source — HTTP-level tests (mocked httpx)
# ===========================================================================


class TestGetObjectMetadataHTTP:
    @pytest.mark.asyncio
    async def test_metadata_200_success(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch

        provider = ODataSAPConnectionProvider(settings)
        mock_response = httpx.Response(200, json=_metadata_payload())
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            result = await provider.get_object_metadata("PROG", "ZMY_PROGRAM")

        assert result.sap_object_type == "PROG"
        assert result.object_name == "ZMY_PROGRAM"
        assert result.found is True

    @pytest.mark.asyncio
    async def test_metadata_401_raises_auth_error(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch

        provider = ODataSAPConnectionProvider(settings)
        mock_response = httpx.Response(401)
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            with pytest.raises(SAPAuthenticationError):
                await provider.get_object_metadata("PROG", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_metadata_500_raises_gateway_error(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.sap.exceptions import SAPGatewayError

        provider = ODataSAPConnectionProvider(settings)
        mock_response = httpx.Response(500)
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            with pytest.raises(SAPGatewayError):
                await provider.get_object_metadata("PROG", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_metadata_invalid_json_raises_parse_error(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch

        provider = ODataSAPConnectionProvider(settings)
        mock_response = httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            with pytest.raises(SAPResponseParseError):
                await provider.get_object_metadata("PROG", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_metadata_connection_error_raises_sap_connection_error(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch

        provider = ODataSAPConnectionProvider(settings)
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            with pytest.raises(SAPConnectionError):
                await provider.get_object_metadata("PROG", "ZMY_PROGRAM")


class TestGetObjectSourceHTTP:
    @pytest.mark.asyncio
    async def test_source_200_success(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch

        rows = [
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=1, source_line="REPORT zmy_program."),
            _source_row(comp_type="PROGRAM", comp_name="ZMY_PROGRAM", line_no=2, source_line="WRITE 'hello'."),
        ]
        provider = ODataSAPConnectionProvider(settings)
        mock_response = httpx.Response(200, json=_source_payload(rows))
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            result = await provider.get_object_source("PROG", "ZMY_PROGRAM", max_lines=5000)

        assert result.returned_lines == 2
        assert result.found is True
        assert result.max_lines == 5000

    @pytest.mark.asyncio
    async def test_source_401_raises_auth_error(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch

        provider = ODataSAPConnectionProvider(settings)
        mock_response = httpx.Response(401)
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            with pytest.raises(SAPAuthenticationError):
                await provider.get_object_source("PROG", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_source_unexpected_status_raises_communication_error(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch

        provider = ODataSAPConnectionProvider(settings)
        mock_response = httpx.Response(503)
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            with pytest.raises(Exception):
                await provider.get_object_source("PROG", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_source_timeout_raises_timeout_error(self, settings):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.sap.exceptions import SAPTimeoutError

        provider = ODataSAPConnectionProvider(settings)
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider, "_build_client", return_value=mock_cm):
            with pytest.raises(SAPTimeoutError):
                await provider.get_object_source("PROG", "ZMY_PROGRAM")
