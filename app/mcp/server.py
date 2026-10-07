"""MCP adapter boundary for SAP Repository Intelligence.

Version 0.1 intentionally keeps MCP transport dependencies out of the core service.
The future MCP server should call RepositoryService/ExportService only; it must not
contain SAP RFC logic. This preserves the same business layer for REST, MCP, N-Suite,
and BTP-hosted consumers.
"""

MCP_TOOL_CONTRACTS = {
    "sap_repository_ping": "Test configured SAP repository connectivity.",
    "sap_repository_search": "Discover custom SAP repository objects using package/name/type filters.",
    "sap_repository_get_object": "Retrieve one SAP repository object and its source/metadata.",
    "sap_repository_export": "Export selected repository objects into a portable artifact.",
}
