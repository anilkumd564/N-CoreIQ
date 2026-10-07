from app.models.repository import RepositoryObject
from app.models.requests import ObjectKey, RepositorySearchRequest
from app.models.results import SearchResult
from app.sap.connection.base import SapConnectionProvider


class RepositoryService:
    def __init__(self, connection: SapConnectionProvider):
        self.connection = connection

    async def search(self, request: RepositorySearchRequest) -> SearchResult:
        """Discover custom SAP repository objects.

        The SAP-side contract will ultimately map to Z_MCP_REPO_DISCOVER.
        Keeping the contract here lets us test the API before ECC development starts.
        """
        response = await self.connection.call(
            "Z_MCP_REPO_DISCOVER",
            PACKAGES=request.packages,
            INCLUDE_SUBPACKAGES=request.include_subpackages,
            NAME_PATTERNS=request.name_patterns,
            OBJECT_TYPES=request.object_types,
            MAX_RESULTS=request.max_results,
        )
        objects = [RepositoryObject.model_validate(item) for item in response.get("objects", [])]
        return SearchResult(count=len(objects), objects=objects)

    async def get_object(self, key: ObjectKey) -> RepositoryObject:
        response = await self.connection.call(
            "Z_MCP_REPO_GET_OBJECT",
            OBJECT_TYPE=key.object_type,
            OBJECT_NAME=key.object_name,
        )
        return RepositoryObject.model_validate(response["object"])
