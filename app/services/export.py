import json
import re
import uuid
import zipfile
from pathlib import Path

from app.core.config import Settings
from app.models.repository import RepositoryObject
from app.models.results import ExportResult


_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class ExportService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _safe_name(value: str) -> str:
        return _SAFE.sub("_", value).strip("._") or "object"

    def create_zip(self, objects: list[RepositoryObject], export_name: str) -> ExportResult:
        root = Path(self.settings.local_export_root)
        root.mkdir(parents=True, exist_ok=True)
        export_id = uuid.uuid4().hex[:12]
        file_name = f"{self._safe_name(export_name)}_{export_id}.zip"
        zip_path = root / file_name

        manifest = {
            "exportId": export_id,
            "objectCount": len(objects),
            "objects": [obj.model_dump(mode="json", exclude={"source"}) for obj in objects],
        }

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
            for obj in objects:
                base = f"objects/{self._safe_name(obj.object_type)}/{self._safe_name(obj.object_name)}"
                archive.writestr(
                    f"{base}/metadata.json",
                    obj.model_dump_json(indent=2, exclude={"source"}),
                )
                if obj.source and obj.source.content is not None:
                    extension = "abap" if (obj.source.language or "").upper() == "ABAP" else "txt"
                    archive.writestr(f"{base}/source.{extension}", obj.source.content)

        return ExportResult(
            export_id=export_id,
            object_count=len(objects),
            artifact_path=str(zip_path),
            artifact_name=file_name,
        )
