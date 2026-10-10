"""Fix executor _run_export_sync to use correct ArtifactStore.save() signature."""
import re

path = "app/jobs/executor.py"
src = open(path, encoding="utf-8").read()

# Locate and replace the broken save() call block
old_fragment = (
    "            filename = f\"ncoreiq_{job_id}.{ext}\"\n"
    "            sha256 = hashlib.sha256(content_bytes).hexdigest()\n"
    "            mime = _MIME_MAP.get(p6_fmt, \"application/octet-stream\")\n"
    "            artifact = self._artifacts.save(\n"
    "                job_id=job_id,\n"
    "                content=content_bytes,\n"
    "                filename=filename,\n"
    "                content_type=mime,\n"
    "                artifact_format=p6_fmt,\n"
    "                sha256=sha256,\n"
    "            )\n"
    "            logger.info(\"event=artifact_created job_id=%s artifact_id=%s size=%d\", job_id, artifact.artifact_id, artifact.size_bytes)\n"
    "            return artifact"
)

new_fragment = (
    "            filename = f\"ncoreiq_{job_id}.{ext}\"\n"
    "            mime = _MIME_MAP.get(p6_fmt, \"application/octet-stream\")\n"
    "            artifact_id = f\"ncoreiq_{job_id}\"\n"
    "            meta = self._artifacts.save(\n"
    "                artifact_id=artifact_id,\n"
    "                data=content_bytes,\n"
    "                filename=filename,\n"
    "                content_type=mime,\n"
    "                job_id=job_id,\n"
    "            )\n"
    "            artifact = JobArtifact(\n"
    "                artifact_id=meta.artifact_id,\n"
    "                format=p6_fmt,\n"
    "                filename=meta.filename,\n"
    "                content_type=meta.content_type,\n"
    "                size_bytes=meta.size_bytes,\n"
    "                sha256=meta.sha256,\n"
    "                created_at=meta.created_at,\n"
    "                expires_at=meta.expires_at,\n"
    "            )\n"
    "            logger.info(\n"
    "                \"event=artifact_created job_id=%s artifact_id=%s size=%d sha256=%.12s\",\n"
    "                job_id, artifact.artifact_id, artifact.size_bytes, artifact.sha256,\n"
    "            )\n"
    "            return artifact"
)

if old_fragment in src:
    out = src.replace(old_fragment, new_fragment, 1)
    # Also remove the now-unused standalone hashlib import if hashlib only used in old code
    # (hashlib is still in imports at top - keep it for safety, only remove if unused)
    open(path, "w", encoding="utf-8").write(out)
    print("REPLACED OK")
else:
    # Show context around artifact_format=p6_fmt to diagnose
    idx = src.find("artifact_format=p6_fmt")
    if idx >= 0:
        print("NOT MATCHED - context around artifact_format=p6_fmt:")
        print(repr(src[idx-300:idx+200]))
    else:
        print("NOT FOUND AT ALL")
